"""Small, dependency-light observability contracts shared by OSA services.

The module deliberately records identifiers and bounded metadata only. Request
and model payloads are never captured. OpenTelemetry is used when its API is
installed and otherwise falls back to a no-op tracer, allowing the domain
package to remain usable in minimal offline environments.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import math
import os
import re
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

_SENSITIVE_KEY = re.compile(r"(?:token|secret|password|credential|authorization|api[_-]?key|prompt|input|output)", re.I)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_MAX_FIELD_LENGTH = 256
_MAX_CAPABILITY_NAME_LENGTH = 128
_MAX_CAPABILITY_ERROR_LENGTH = 128
DEFAULT_CAPABILITY_TELEMETRY_MAX_BYTES = 10_000_000
CAPABILITY_TELEMETRY_PATH_ENV_VAR = "OSA_CAPABILITY_TELEMETRY_PATH"
CAPABILITY_TELEMETRY_MAX_BYTES_ENV_VAR = "OSA_CAPABILITY_TELEMETRY_MAX_BYTES"
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


@dataclass(frozen=True)
class CapabilityTelemetryEvent:
    """Redaction-safe outcome for one model, native-tool, or MCP capability."""

    kind: str
    name: str
    outcome: str
    error_code: str | None
    duration_seconds: float


class CapabilityTelemetrySink(Protocol):
    """Optional sink for bounded capability outcomes."""

    def record(self, event: CapabilityTelemetryEvent) -> None:
        """Persist or forward a telemetry event without payload data."""
        ...


class InMemoryCapabilityTelemetrySink:
    """Bounded sink useful for tests and local diagnostics."""

    def __init__(self, *, max_events: int = 1000) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._events: list[CapabilityTelemetryEvent] = []
        self._max_events = max_events
        self._lock = Lock()

    def record(self, event: CapabilityTelemetryEvent) -> None:
        with self._lock:
            self._events.append(event)
            del self._events[: -self._max_events]

    def events(self) -> list[CapabilityTelemetryEvent]:
        """Return a snapshot of the bounded event list."""
        with self._lock:
            return list(self._events)


class JsonlCapabilityTelemetrySink:
    """Persist bounded, payload-free capability events as newline-delimited JSON.

    The sink is intentionally synchronous because the observability contract
    is also used by native-tool worker threads. Writes are bounded by
    ``max_bytes`` and compacted atomically when the limit is reached. Each
    append is flushed and optionally fsynced so an operator can choose a
    crash-durable local audit trail without making it the default behavior.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_bytes: int = DEFAULT_CAPABILITY_TELEMETRY_MAX_BYTES,
        fsync: bool = True,
    ) -> None:
        if max_bytes < 1024:
            raise ValueError("max_bytes must be at least 1024")
        self._path = Path(path)
        if not self._path.name:
            raise ValueError("path must name a file")
        self._max_bytes = max_bytes
        self._fsync = fsync
        self._lock = Lock()

    def record(self, event: CapabilityTelemetryEvent) -> None:
        """Append one sanitized event and retain only the newest bounded data."""
        line = self._encode(event)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            current_size = self._path.stat().st_size if self._path.exists() else 0
            if current_size + len(line) <= self._max_bytes:
                self._append(line)
            else:
                self._compact(line)

    def _append(self, line: bytes) -> None:
        with self._path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            if self._fsync:
                os.fsync(stream.fileno())

    def _compact(self, newest: bytes) -> None:
        existing = self._path.read_bytes() if self._path.exists() else b""
        lines = [line + b"\n" for line in existing.splitlines() if line.strip()]
        lines.append(newest)
        total = sum(len(line) for line in lines)
        while len(lines) > 1 and total > self._max_bytes:
            total -= len(lines.pop(0))
        temporary_path = self._path.with_name(f".{self._path.name}.tmp")
        try:
            with temporary_path.open("wb") as stream:
                stream.writelines(lines)
                stream.flush()
                if self._fsync:
                    os.fsync(stream.fileno())
            os.replace(temporary_path, self._path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary_path.unlink()

    @staticmethod
    def _encode(event: CapabilityTelemetryEvent) -> bytes:
        duration = event.duration_seconds if math.isfinite(event.duration_seconds) else 0.0
        payload = {
            "kind": bounded_text(event.kind, limit=32),
            "name": bounded_text(event.name, limit=_MAX_CAPABILITY_NAME_LENGTH),
            "outcome": bounded_text(event.outcome, limit=32),
            "error_code": (
                bounded_text(event.error_code, limit=_MAX_CAPABILITY_ERROR_LENGTH)
                if event.error_code is not None
                else None
            ),
            "duration_seconds": max(0.0, round(duration, 6)),
        }
        return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def capability_sink_from_env(environ: Mapping[str, str] | None = None) -> JsonlCapabilityTelemetrySink | None:
    """Build the optional local JSONL sink from operator environment."""
    values = os.environ if environ is None else environ
    raw_path = values.get(CAPABILITY_TELEMETRY_PATH_ENV_VAR, "").strip()
    if not raw_path:
        return None
    raw_max_bytes = values.get(CAPABILITY_TELEMETRY_MAX_BYTES_ENV_VAR)
    try:
        max_bytes = int(raw_max_bytes) if raw_max_bytes else DEFAULT_CAPABILITY_TELEMETRY_MAX_BYTES
    except ValueError as exc:
        raise ValueError(f"{CAPABILITY_TELEMETRY_MAX_BYTES_ENV_VAR} must be an integer") from exc
    return JsonlCapabilityTelemetrySink(raw_path, max_bytes=max_bytes)


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

    def __init__(
        self,
        metrics: MetricsRegistry | None = None,
        capability_sink: CapabilityTelemetrySink | None = None,
    ) -> None:
        self.metrics = metrics if metrics is not None else MetricsRegistry()
        self.capability_sink = capability_sink if capability_sink is not None else capability_sink_from_env()
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
        error_code: str | None = None
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
            error_code = getattr(exc, "code", None)
            span.record_exception(exc)
            raise
        finally:
            self._record_capability(operation, safe_labels, outcome, time.perf_counter() - start, error_code)
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
        error_code: str | None = None
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
            error_code = getattr(exc, "code", None)
            span.record_exception(exc)
            raise
        finally:
            self._record_capability(operation, safe_labels, outcome, time.perf_counter() - start, error_code)
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

    def _record_capability(
        self,
        operation: str,
        labels: Mapping[str, object],
        outcome: str,
        duration_seconds: float,
        error_code: str | None,
    ) -> None:
        operation_kind, _, operation_name = operation.partition(".")
        if operation_kind not in {"model", "tool", "mcp"}:
            return
        name = str(labels.get("model") or labels.get("tool") or labels.get("server") or operation_name)
        self.metrics.increment(
            "osa_capability_events_total",
            {"kind": operation_kind, "capability": name, "outcome": outcome},
        )
        if self.capability_sink is not None:
            event = CapabilityTelemetryEvent(
                kind=operation_kind,
                name=bounded_text(name),
                outcome=outcome,
                error_code=bounded_text(error_code) if error_code is not None else None,
                duration_seconds=duration_seconds,
            )
            with contextlib.suppress(Exception):
                self.capability_sink.record(event)


__all__ = [
    "CAPABILITY_TELEMETRY_MAX_BYTES_ENV_VAR",
    "CAPABILITY_TELEMETRY_PATH_ENV_VAR",
    "CapabilityTelemetryEvent",
    "CapabilityTelemetrySink",
    "DEFAULT_CAPABILITY_TELEMETRY_MAX_BYTES",
    "InMemoryCapabilityTelemetrySink",
    "JsonlCapabilityTelemetrySink",
    "JsonFormatter",
    "MetricsRegistry",
    "Observability",
    "bounded_text",
    "capability_sink_from_env",
    "configure_structured_logging",
    "log_context",
    "log_event",
    "redact_fields",
    "redact_text",
]
