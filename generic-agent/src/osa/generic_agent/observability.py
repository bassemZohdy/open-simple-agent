"""Small, dependency-light observability contracts shared by OSA services.

The module deliberately records identifiers and bounded metadata only. Request
and model payloads are never captured. OpenTelemetry is used when its API is
installed and otherwise falls back to a no-op tracer, allowing the domain
package to remain usable in minimal offline environments.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Any

_SENSITIVE_KEY = re.compile(r"(?:token|secret|password|credential|authorization|api[_-]?key|prompt|input|output)", re.I)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_MAX_FIELD_LENGTH = 256
_SECRET_VALUE = re.compile(
    r"(?i)(\b(?:authorization\s*:\s*)?bearer\s+|\b(?:api[_-]?key|token|secret|password|client_secret)\s*[:=]\s*)"
    r"[\"']?[^,\s\"']+"
)


def redact_text(value: str) -> str:
    """Redact common inline credential forms from externally produced text."""
    return _SECRET_VALUE.sub(r"\1[REDACTED]", value)


def bounded_text(value: object, *, limit: int = _MAX_FIELD_LENGTH) -> str:
    """Return a bounded, single-line representation safe for telemetry."""
    text = redact_text(str(value)).replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def redact_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Redact sensitive keys and bound all structured values."""
    redacted: dict[str, object] = {}
    for key, value in fields.items():
        if _SENSITIVE_KEY.search(key):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, Mapping):
            redacted[key] = redact_fields(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            redacted[key] = bounded_text(value) if isinstance(value, str) else value
        else:
            redacted[key] = bounded_text(value)
    return redacted


_log_context: ContextVar[dict[str, object] | None] = ContextVar("osa_log_context", default=None)


@contextmanager
def log_context(fields: Mapping[str, object]) -> Iterator[None]:
    """Add redacted correlation fields to logs emitted in this context."""
    merged = dict(_log_context.get() or {})
    merged.update(redact_fields(fields))
    token = _log_context.set(merged)
    try:
        yield
    finally:
        _log_context.reset(token)


def log_event(logger: logging.Logger, level: int, message: str, fields: Mapping[str, object] | None = None) -> None:
    """Emit a structured log record without accepting sensitive payloads."""
    merged = dict(_log_context.get() or {})
    if fields:
        merged.update(redact_fields(fields))
    logger.log(level, message, extra={"osa_fields": merged})


class JsonFormatter(logging.Formatter):
    """JSON formatter for OSA log records."""

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "osa_fields", {})
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": bounded_text(record.getMessage()),
        }
        if isinstance(fields, Mapping):
            payload.update(redact_fields(fields))
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure_structured_logging() -> None:
    """Enable JSON logs when ``OSA_LOG_FORMAT=json`` is configured."""
    if os.environ.get("OSA_LOG_FORMAT", "").strip().lower() != "json":
        return
    logger = logging.getLogger("osa")
    if any(getattr(handler, "_osa_json", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler._osa_json = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)


@dataclass
class _MetricSeries:
    count: float = 0.0
    total: float = 0.0


class MetricsRegistry:
    """Bounded in-process counters and duration summaries."""

    def __init__(self, *, max_series: int = 1000) -> None:
        if max_series < 1:
            raise ValueError("max_series must be positive")
        self._max_series = max_series
        self._series: dict[tuple[str, tuple[tuple[str, str], ...]], _MetricSeries] = {}
        self._lock = Lock()

    def increment(self, name: str, labels: Mapping[str, object] | None = None, value: float = 1.0) -> None:
        """Increment a counter with bounded, non-sensitive labels."""
        self._update(name, labels, value, duration=False)

    def observe(self, name: str, seconds: float, labels: Mapping[str, object] | None = None) -> None:
        """Record a duration count and sum."""
        self._update(name, labels, seconds, duration=True)

    def _update(
        self,
        name: str,
        labels: Mapping[str, object] | None,
        value: float,
        *,
        duration: bool,
    ) -> None:
        safe_name = re.sub(r"[^a-zA-Z0-9_:]", "_", name)
        safe_labels = tuple(
            sorted(
                (str(key), bounded_text(value))
                for key, value in (labels or {}).items()
                if _SAFE_IDENTIFIER.fullmatch(str(key)) and not _SENSITIVE_KEY.search(str(key))
            )
        )
        key = (f"{safe_name}_seconds" if duration else safe_name, safe_labels)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                if len(self._series) >= self._max_series:
                    return
                series = self._series[key] = _MetricSeries()
            series.count += 1
            series.total += value

    def render_prometheus(self) -> str:
        """Render redaction-safe Prometheus text exposition."""
        lines: list[str] = []
        with self._lock:
            items = list(self._series.items())
        for (name, labels), series in sorted(items):
            suffix = _format_labels(labels)
            if name.endswith("_seconds"):
                lines.append(f"{name}_count{suffix} {series.count:g}")
                lines.append(f"{name}_sum{suffix} {series.total:g}")
            else:
                lines.append(f"{name}{suffix} {series.total:g}")
        return "\n".join(lines) + ("\n" if lines else "")


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    rendered = ",".join(
        f'{key}="{value.replace(chr(92), chr(92) + chr(92)).replace(chr(34), chr(92) + chr(34))}"'
        for key, value in labels
    )
    return f"{{{rendered}}}"


class _NoopSpan:
    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def record_exception(self, _exception: BaseException) -> None:
        return None


def _tracer() -> Any:
    try:
        module = importlib.import_module("opentelemetry.trace")
        return module.get_tracer("osa")
    except (ImportError, AttributeError):
        return None


class Observability:
    """Metrics + tracing facade used by API and runtime boundaries."""

    def __init__(self, metrics: MetricsRegistry | None = None) -> None:
        self.metrics = metrics or MetricsRegistry()
        self._tracer = _tracer()

    @asynccontextmanager
    async def span(
        self,
        operation: str,
        *,
        labels: Mapping[str, object] | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Any]:
        """Trace and time an operation while counting success or failure."""
        safe_labels = redact_fields(labels or {})
        start = time.perf_counter()
        outcome = "success"
        span = _NoopSpan()
        try:
            if self._tracer is None:
                with span:
                    yield span
            else:
                with self._tracer.start_as_current_span(
                    f"osa.{operation}", attributes=dict(redact_fields(attributes or {}))
                ) as active_span:
                    span = active_span
                    yield span
        except Exception as exc:
            outcome = "error"
            span.record_exception(exc)
            raise
        finally:
            metric_labels = {"operation": operation, **safe_labels, "outcome": outcome}
            self.metrics.increment("osa_operations_total", metric_labels)
            self.metrics.observe("osa_operation_duration", time.perf_counter() - start, {"operation": operation})

    @contextmanager
    def span_sync(
        self,
        operation: str,
        *,
        labels: Mapping[str, object] | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[Any]:
        """Synchronous counterpart for native tools executed in worker threads."""
        safe_labels = redact_fields(labels or {})
        start = time.perf_counter()
        outcome = "success"
        span = _NoopSpan()
        try:
            if self._tracer is None:
                with span:
                    yield span
            else:
                with self._tracer.start_as_current_span(
                    f"osa.{operation}", attributes=dict(redact_fields(attributes or {}))
                ) as active_span:
                    span = active_span
                    yield span
        except Exception as exc:
            outcome = "error"
            span.record_exception(exc)
            raise
        finally:
            metric_labels = {"operation": operation, **safe_labels, "outcome": outcome}
            self.metrics.increment("osa_operations_total", metric_labels)
            self.metrics.observe("osa_operation_duration", time.perf_counter() - start, {"operation": operation})

    def record_token_usage(
        self,
        model: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        """Record provider-reported token counts without recording text."""
        labels = {"model": model}
        self.metrics.increment("osa_model_tokens_total", {**labels, "kind": "prompt"}, prompt_tokens)
        self.metrics.increment("osa_model_tokens_total", {**labels, "kind": "completion"}, completion_tokens)
        self.metrics.increment("osa_model_tokens_total", {**labels, "kind": "total"}, total_tokens)


__all__ = [
    "JsonFormatter",
    "MetricsRegistry",
    "Observability",
    "bounded_text",
    "configure_structured_logging",
    "log_context",
    "log_event",
    "redact_fields",
    "redact_text",
]
