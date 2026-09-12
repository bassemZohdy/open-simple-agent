"""Small, dependency-light observability contracts shared by OSA services.

The module deliberately records identifiers and bounded metadata only. Request
and model payloads are never captured. OpenTelemetry is used when its API is
installed and otherwise falls back to a no-op tracer, allowing the domain
package to remain usable in minimal offline environments.
"""

from __future__ import annotations

import contextlib
import hashlib
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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

_SENSITIVE_KEY = re.compile(r"(?:token|secret|password|credential|authorization|api[_-]?key|prompt|input|output)", re.I)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_MAX_FIELD_LENGTH = 256
_MAX_CAPABILITY_NAME_LENGTH = 128
_MAX_CAPABILITY_ERROR_LENGTH = 128
DEFAULT_CAPABILITY_TELEMETRY_MAX_BYTES = 10_000_000
CAPABILITY_TELEMETRY_PATH_ENV_VAR = "OSA_CAPABILITY_TELEMETRY_PATH"
CAPABILITY_TELEMETRY_MAX_BYTES_ENV_VAR = "OSA_CAPABILITY_TELEMETRY_MAX_BYTES"
CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR = "OSA_CAPABILITY_TELEMETRY_DATABASE_URL"
CAPABILITY_TELEMETRY_TABLE_ENV_VAR = "OSA_CAPABILITY_TELEMETRY_TABLE"
CAPABILITY_TELEMETRY_RETENTION_DAYS_ENV_VAR = "OSA_CAPABILITY_TELEMETRY_RETENTION_DAYS"
DEFAULT_CAPABILITY_TELEMETRY_TABLE = "osa_capability_telemetry"
DEFAULT_CAPABILITY_TELEMETRY_RETENTION_DAYS = 30
CAPABILITY_TELEMETRY_SCHEMA_VERSION = 1
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
_telemetry_sequence: ContextVar[int] = ContextVar("osa_telemetry_sequence", default=0)


@contextmanager
def log_context(fields: Mapping[str, object]) -> Iterator[None]:
    """Add redacted correlation fields to logs emitted in this context."""
    merged = dict(_log_context.get() or {})
    merged.update(redact_fields(fields))
    token = _log_context.set(merged)
    sequence_token = None
    if "request_id" in fields or "operation_id" in fields:
        sequence_token = _telemetry_sequence.set(0)
    try:
        yield
    finally:
        _log_context.reset(token)
        if sequence_token is not None:
            _telemetry_sequence.reset(sequence_token)


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
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    tenant_id: str | None = None
    operation_id: str | None = None
    sequence: int | None = None


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
        occurred_at = event.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        payload = {
            "event_id": bounded_text(event.event_id, limit=128),
            "occurred_at": occurred_at.isoformat(),
            "tenant_id": _optional_bounded_identifier(event.tenant_id),
            "operation_id": _optional_bounded_identifier(event.operation_id),
            "sequence": event.sequence if event.sequence is None or event.sequence > 0 else None,
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


class PostgresCapabilityTelemetrySink:
    """Migration-owned, replica-shared capability telemetry sink.

    The sink uses a synchronous SQLAlchemy connection because capability spans
    can finish in native-tool worker threads. It is deliberately optional and
    only accepts PostgreSQL DSNs; schema creation is exposed through the
    migration CLI, while service startup validates the existing schema.
    Events are idempotent by ``event_id`` and are ordered for readers by
    database ingestion time plus the stable event id. Retention is operator
    owned and prunes by ingestion time during writes.
    """

    _REQUIRED_COLUMNS = frozenset(
        {
            "event_id",
            "occurred_at",
            "ingested_at",
            "tenant_id",
            "operation_id",
            "sequence",
            "kind",
            "capability_name",
            "outcome",
            "error_code",
            "duration_seconds",
        }
    )

    def __init__(
        self,
        database_url: str,
        *,
        table_name: str = DEFAULT_CAPABILITY_TELEMETRY_TABLE,
        retention_days: int = DEFAULT_CAPABILITY_TELEMETRY_RETENTION_DAYS,
    ) -> None:
        if not database_url.strip():
            raise ValueError(f"{CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR} must not be empty")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
            raise ValueError(f"{CAPABILITY_TELEMETRY_TABLE_ENV_VAR} must be a simple SQL identifier")
        if retention_days < 1:
            raise ValueError(f"{CAPABILITY_TELEMETRY_RETENTION_DAYS_ENV_VAR} must be at least 1")
        self.database_url = database_url
        self.table_name = table_name
        self.retention_days = retention_days
        self._engine: Any = None
        self._table: Any = None
        self._version_table: Any = None
        self._schema_validated = False
        self._lock = Lock()

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.engine import make_url
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("PostgreSQL capability telemetry requires the generic-agent postgres extra") from exc
        try:
            url = make_url(self.database_url)
        except Exception as exc:  # pragma: no cover - SQLAlchemy owns detailed parsing
            raise ValueError("Capability telemetry database URL is invalid") from exc
        if url.get_backend_name() != "postgresql":
            raise ValueError("Capability telemetry database URL must use PostgreSQL")
        # pg8000 is part of OSA's postgres extra and keeps this synchronous
        # sink usable from both the async runtime and native-tool threads.
        if url.get_driver_name() in {"", "asyncpg", "psycopg", "psycopg2"}:
            url = url.set(drivername="postgresql+pg8000")
        self._engine = create_engine(url, pool_pre_ping=True)
        return self._engine

    def _get_table(self) -> Any:
        if self._table is not None:
            return self._table
        try:
            from sqlalchemy import Column, DateTime, Float, Index, Integer, MetaData, String, Table
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("PostgreSQL capability telemetry requires the generic-agent postgres extra") from exc
        metadata = MetaData()
        self._table = Table(
            self.table_name,
            metadata,
            Column("event_id", String(128), primary_key=True),
            Column("occurred_at", DateTime(timezone=True), nullable=False),
            Column("ingested_at", DateTime(timezone=True), nullable=False),
            Column("tenant_id", String(128), nullable=True),
            Column("operation_id", String(128), nullable=True),
            Column("sequence", Integer, nullable=True),
            Column("kind", String(32), nullable=False),
            Column("capability_name", String(_MAX_CAPABILITY_NAME_LENGTH), nullable=False),
            Column("outcome", String(32), nullable=False),
            Column("error_code", String(_MAX_CAPABILITY_ERROR_LENGTH), nullable=True),
            Column("duration_seconds", Float, nullable=False),
        )
        table_hash = hashlib.sha256(self.table_name.encode("utf-8")).hexdigest()[:12]
        Index(f"ix_osa_capability_ingested_{table_hash}", self._table.c.ingested_at)
        Index(
            f"ix_osa_capability_tenant_{table_hash}",
            self._table.c.tenant_id,
            self._table.c.ingested_at,
        )
        return self._table

    def _get_version_table(self) -> Any:
        if self._version_table is not None:
            return self._version_table
        try:
            from sqlalchemy import Column, DateTime, Integer, Table
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("PostgreSQL capability telemetry requires the generic-agent postgres extra") from exc
        metadata = self._get_table().metadata
        table_hash = hashlib.sha256(self.table_name.encode("utf-8")).hexdigest()[:16]
        self._version_table = Table(
            f"osa_capability_schema_{table_hash}",
            metadata,
            Column("version", Integer, primary_key=True),
            Column("applied_at", DateTime(timezone=True), nullable=False),
        )
        return self._version_table

    def create_schema(self) -> None:
        """Create the telemetry table for the explicit migration command."""
        table = self._get_table()
        version_table = self._get_version_table()
        from sqlalchemy import func, select
        from sqlalchemy.dialects.postgresql import insert

        with self._get_engine().begin() as connection:
            table.metadata.create_all(connection, tables=[table, version_table])
            versions = connection.execute(select(version_table.c.version)).scalars().all()
            if versions and max(int(version) for version in versions) != CAPABILITY_TELEMETRY_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Capability telemetry schema version {max(int(version) for version in versions)} "
                    f"is incompatible with required version {CAPABILITY_TELEMETRY_SCHEMA_VERSION}"
                )
            connection.execute(
                insert(version_table)
                .values(version=CAPABILITY_TELEMETRY_SCHEMA_VERSION, applied_at=func.now())
                .on_conflict_do_nothing(index_elements=["version"])
            )
        self._schema_validated = True

    def validate_schema(self) -> None:
        """Verify that the operator-owned table exists and is current."""
        with self._lock:
            if self._schema_validated:
                return
            from sqlalchemy import inspect

            self._get_table()
            version_table = self._get_version_table()
            with self._get_engine().connect() as connection:
                inspector = inspect(connection)
                if not inspector.has_table(self.table_name) or not inspector.has_table(version_table.name):
                    raise RuntimeError(
                        f"The capability telemetry schema is unavailable; run 'osa-capability-telemetry-migrate' "
                        f"against {CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR} before starting"
                    )
                columns = {str(column["name"]) for column in inspector.get_columns(self.table_name)}
            missing = self._REQUIRED_COLUMNS - columns
            if missing:
                missing_columns = ", ".join(sorted(missing))
                raise RuntimeError(
                    f"Capability telemetry schema for '{self.table_name}' is missing columns: {missing_columns}"
                )
            from sqlalchemy import select

            with self._get_engine().connect() as connection:
                version = (
                    connection.execute(select(version_table.c.version).order_by(version_table.c.version.desc()))
                    .scalars()
                    .first()
                )
            if version != CAPABILITY_TELEMETRY_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Capability telemetry schema version {version or 'missing'} is older than required "
                    f"{CAPABILITY_TELEMETRY_SCHEMA_VERSION}; run 'osa-capability-telemetry-migrate' first"
                )
            self._schema_validated = True

    def record(self, event: CapabilityTelemetryEvent) -> None:
        """Insert one event idempotently and prune records outside retention."""
        self.validate_schema()
        from sqlalchemy import delete, func
        from sqlalchemy.dialects.postgresql import insert

        table = self._get_table()
        occurred_at = event.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        if event.kind not in {"model", "tool", "mcp"}:
            raise ValueError("capability telemetry kind must be model, tool, or mcp")
        duration = event.duration_seconds if math.isfinite(event.duration_seconds) else 0.0
        values = {
            "event_id": _required_bounded_identifier(event.event_id, limit=128, field_name="event_id"),
            "occurred_at": occurred_at,
            "ingested_at": func.now(),
            "tenant_id": _optional_bounded_identifier(event.tenant_id),
            "operation_id": _optional_bounded_identifier(event.operation_id),
            "sequence": event.sequence if event.sequence is None or event.sequence > 0 else None,
            "kind": bounded_text(event.kind, limit=32),
            "capability_name": bounded_text(event.name, limit=_MAX_CAPABILITY_NAME_LENGTH),
            "outcome": bounded_text(event.outcome, limit=32),
            "error_code": bounded_text(event.error_code, limit=_MAX_CAPABILITY_ERROR_LENGTH)
            if event.error_code is not None
            else None,
            "duration_seconds": max(0.0, round(duration, 6)),
        }
        statement = insert(table).values(**values).on_conflict_do_nothing(index_elements=["event_id"])
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        with self._get_engine().begin() as connection:
            connection.execute(statement)
            connection.execute(delete(table).where(table.c.ingested_at < cutoff))

    def list_events(self, *, tenant_id: str | None = None, limit: int = 100) -> list[CapabilityTelemetryEvent]:
        """Read bounded, deterministically ordered events for operations/tests."""
        self.validate_schema()
        from sqlalchemy import select

        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        table = self._get_table()
        query = select(table)
        if tenant_id is not None:
            query = query.where(table.c.tenant_id == _optional_bounded_identifier(tenant_id))
        query = query.order_by(table.c.ingested_at.asc(), table.c.event_id.asc()).limit(limit)
        with self._get_engine().connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [
            CapabilityTelemetryEvent(
                kind=str(row["kind"]),
                name=str(row["capability_name"]),
                outcome=str(row["outcome"]),
                error_code=str(row["error_code"]) if row["error_code"] is not None else None,
                duration_seconds=float(row["duration_seconds"]),
                event_id=str(row["event_id"]),
                occurred_at=row["occurred_at"],
                tenant_id=str(row["tenant_id"]) if row["tenant_id"] is not None else None,
                operation_id=str(row["operation_id"]) if row["operation_id"] is not None else None,
                sequence=int(row["sequence"]) if row["sequence"] is not None else None,
            )
            for row in rows
        ]

    def delete_tenant(self, tenant_id: str) -> int:
        """Delete all telemetry owned by one tenant and return deleted rows."""
        self.validate_schema()
        from sqlalchemy import delete

        table = self._get_table()
        with self._get_engine().begin() as connection:
            result = connection.execute(
                delete(table).where(table.c.tenant_id == _optional_bounded_identifier(tenant_id))
            )
        return int(result.rowcount or 0)

    def prune(self) -> int:
        """Apply the configured retention window and return deleted rows."""
        self.validate_schema()
        from sqlalchemy import delete

        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        table = self._get_table()
        with self._get_engine().begin() as connection:
            result = connection.execute(delete(table).where(table.c.ingested_at < cutoff))
        return int(result.rowcount or 0)

    def close(self) -> None:
        """Dispose the owned SQLAlchemy engine."""
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None
            self._schema_validated = False


def _required_bounded_identifier(value: str, *, limit: int, field_name: str) -> str:
    bounded = bounded_text(value, limit=limit)
    if not bounded:
        raise ValueError(f"capability telemetry {field_name} must not be empty")
    return bounded


def _optional_bounded_identifier(value: str | None) -> str | None:
    return bounded_text(value, limit=128) if value is not None else None


def capability_sink_from_env(environ: Mapping[str, str] | None = None) -> CapabilityTelemetrySink | None:
    """Build one optional local or replica-shared sink from operator environment."""
    values = os.environ if environ is None else environ
    raw_database = values.get(CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR)
    raw_path = values.get(CAPABILITY_TELEMETRY_PATH_ENV_VAR, "").strip()
    if raw_database is not None:
        if not raw_database.strip():
            raise ValueError(f"{CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR} must not be empty")
        if raw_path:
            raise ValueError(
                f"configure only one of {CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR} and "
                f"{CAPABILITY_TELEMETRY_PATH_ENV_VAR}"
            )
        from osa.generic_agent.persistence import require_shared_database

        require_shared_database(raw_database, "capability telemetry state", environ=values)
        raw_retention_days = values.get(CAPABILITY_TELEMETRY_RETENTION_DAYS_ENV_VAR)
        try:
            retention_days = (
                int(raw_retention_days)
                if raw_retention_days and raw_retention_days.strip()
                else DEFAULT_CAPABILITY_TELEMETRY_RETENTION_DAYS
            )
        except ValueError as exc:
            raise ValueError(f"{CAPABILITY_TELEMETRY_RETENTION_DAYS_ENV_VAR} must be an integer") from exc
        return PostgresCapabilityTelemetrySink(
            raw_database,
            table_name=values.get(CAPABILITY_TELEMETRY_TABLE_ENV_VAR, DEFAULT_CAPABILITY_TELEMETRY_TABLE),
            retention_days=retention_days,
        )
    if not raw_path:
        if values.get(CAPABILITY_TELEMETRY_RETENTION_DAYS_ENV_VAR):
            raise ValueError(
                f"{CAPABILITY_TELEMETRY_RETENTION_DAYS_ENV_VAR} requires {CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR}"
            )
        return None
    from osa.generic_agent.persistence import require_shared_database

    require_shared_database(None, "capability telemetry state", environ=values)
    raw_max_bytes = values.get(CAPABILITY_TELEMETRY_MAX_BYTES_ENV_VAR)
    try:
        max_bytes = int(raw_max_bytes) if raw_max_bytes else DEFAULT_CAPABILITY_TELEMETRY_MAX_BYTES
    except ValueError as exc:
        raise ValueError(f"{CAPABILITY_TELEMETRY_MAX_BYTES_ENV_VAR} must be an integer") from exc
    return JsonlCapabilityTelemetrySink(raw_path, max_bytes=max_bytes)


def capability_telemetry_migrate_cli(argv: list[str] | None = None) -> int:
    """Create the shared capability telemetry table for an operator-selected database."""
    from argparse import ArgumentParser

    parser = ArgumentParser(prog="osa-capability-telemetry-migrate")
    parser.add_argument(
        "--database-url",
        default=os.environ.get(CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR),
    )
    parser.add_argument(
        "--table",
        default=os.environ.get(CAPABILITY_TELEMETRY_TABLE_ENV_VAR, DEFAULT_CAPABILITY_TELEMETRY_TABLE),
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error(f"--database-url is required (or set {CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR})")
    sink = PostgresCapabilityTelemetrySink(args.database_url, table_name=args.table)
    try:
        sink.create_schema()
    finally:
        sink.close()
    print(f"Ensured capability telemetry schema table {args.table}")
    return 0


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
            context = _log_context.get() or {}
            operation_id = _optional_bounded_identifier(
                str(labels.get("operation_id") or context.get("operation_id") or context.get("request_id"))
                if labels.get("operation_id") or context.get("operation_id") or context.get("request_id")
                else None
            )
            tenant_id = _optional_bounded_identifier(
                str(labels.get("tenant_id") or context.get("tenant_id"))
                if labels.get("tenant_id") or context.get("tenant_id")
                else None
            )
            sequence = _telemetry_sequence.get() + 1
            _telemetry_sequence.set(sequence)
            event_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"osa-capability:{operation_id}:{sequence}:{operation_kind}:{name}",
                )
                if operation_id is not None
                else uuid4()
            )
            event = CapabilityTelemetryEvent(
                kind=operation_kind,
                name=bounded_text(name),
                outcome=outcome,
                error_code=bounded_text(error_code) if error_code is not None else None,
                duration_seconds=duration_seconds,
                event_id=event_id,
                tenant_id=tenant_id,
                operation_id=operation_id,
                sequence=sequence,
            )
            with contextlib.suppress(Exception):
                self.capability_sink.record(event)


__all__ = [
    "CAPABILITY_TELEMETRY_MAX_BYTES_ENV_VAR",
    "CAPABILITY_TELEMETRY_PATH_ENV_VAR",
    "CAPABILITY_TELEMETRY_DATABASE_URL_ENV_VAR",
    "CAPABILITY_TELEMETRY_RETENTION_DAYS_ENV_VAR",
    "CAPABILITY_TELEMETRY_SCHEMA_VERSION",
    "CAPABILITY_TELEMETRY_TABLE_ENV_VAR",
    "DEFAULT_CAPABILITY_TELEMETRY_TABLE",
    "DEFAULT_CAPABILITY_TELEMETRY_RETENTION_DAYS",
    "CapabilityTelemetryEvent",
    "CapabilityTelemetrySink",
    "DEFAULT_CAPABILITY_TELEMETRY_MAX_BYTES",
    "InMemoryCapabilityTelemetrySink",
    "JsonlCapabilityTelemetrySink",
    "PostgresCapabilityTelemetrySink",
    "JsonFormatter",
    "MetricsRegistry",
    "Observability",
    "bounded_text",
    "capability_telemetry_migrate_cli",
    "capability_sink_from_env",
    "configure_structured_logging",
    "log_context",
    "log_event",
    "redact_fields",
    "redact_text",
]
