"""File-backed SQLite session provider for local single-process deployments."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from osa.generic_agent import (
    Session,
    SessionAccessError,
    SessionConcurrencyError,
    SessionConfigurationError,
    SessionId,
    SessionNotFoundError,
    SessionProvider,
)
from osa.generic_agent.session import DEFAULT_MAX_HISTORY_MESSAGES

SCHEMA_VERSION_TABLE = "osa_session_sqlite_schema_versions"
SESSION_TABLE = "osa_runtime_sessions"
CURRENT_SCHEMA_VERSION = 1


def _require_sqlite() -> None:
    if find_spec("sqlalchemy") is None:
        raise SessionConfigurationError(
            "The SQLite session provider requires 'sqlalchemy'; install the 'osa-adk-runtime[sqlite]' extra"
        )


def _sqlite_url(dsn: str) -> Any:
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        url = make_url(dsn)
    except (ArgumentError, ValueError) as exc:
        raise SessionConfigurationError("Session SQLite URL is invalid") from exc
    if url.get_backend_name() != "sqlite":
        raise SessionConfigurationError("Session SQLite provider requires a SQLite DSN")
    if url.database in {None, ":memory:"}:
        raise SessionConfigurationError("SQLite session persistence requires a file-backed database")
    if url.get_driver_name() not in {"pysqlite", "aiosqlite"}:
        raise SessionConfigurationError("SQLite session persistence requires the pysqlite or aiosqlite driver")
    return url


def _normalize_dsn(dsn: str) -> str:
    return str(_sqlite_url(dsn).set(drivername="sqlite+pysqlite").render_as_string(hide_password=False))


def _restrict_file_permissions(dsn: str) -> None:
    """Keep an existing/newly migrated SQLite file private on POSIX hosts."""
    if os.name == "nt":
        return
    database = _sqlite_url(dsn).database
    if database is None or database == ":memory:":
        return
    path = Path(database)
    try:
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            if candidate.exists():
                candidate.chmod(0o600)
    except OSError as exc:
        raise SessionConfigurationError("Unable to restrict SQLite session file permissions") from exc


def _configure_sqlite_engine(engine: Any) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def _iso(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat()


def _utc(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


class SqliteSessionProvider(SessionProvider):
    """Ownership-checked session provider backed by a local SQLite file."""

    def __init__(self, dsn: str) -> None:
        _require_sqlite()
        _restrict_file_permissions(dsn)
        from sqlalchemy import create_engine

        self._engine = create_engine(_normalize_dsn(dsn), connect_args={"timeout": 5}, pool_pre_ping=True)
        _configure_sqlite_engine(self._engine)

    def migrate(self) -> int:
        """Apply the explicit SQLite session schema."""
        from sqlalchemy import text

        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            current = int(
                connection.execute(text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}")).scalar_one()
            )
            if current < CURRENT_SCHEMA_VERSION:
                connection.execute(
                    text(
                        f"""
                        CREATE TABLE IF NOT EXISTS {SESSION_TABLE} (
                            session_id TEXT PRIMARY KEY,
                            agent_name TEXT NOT NULL,
                            user_id TEXT NULL,
                            tenant_id TEXT NULL,
                            created_at TEXT NOT NULL,
                            last_active_at TEXT NOT NULL,
                            ttl_seconds INTEGER NULL,
                            max_history_messages INTEGER NOT NULL,
                            metadata TEXT NOT NULL DEFAULT '{{}}',
                            conversation_history TEXT NOT NULL DEFAULT '[]',
                            revision INTEGER NOT NULL DEFAULT 0
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_{SESSION_TABLE}_owner "
                        f"ON {SESSION_TABLE} (tenant_id, agent_name, user_id)"
                    )
                )
                connection.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_{SESSION_TABLE}_last_active ON {SESSION_TABLE} (last_active_at)"
                    )
                )
                connection.execute(
                    text(f"INSERT INTO {SCHEMA_VERSION_TABLE} (version) VALUES (:version)"),
                    {"version": CURRENT_SCHEMA_VERSION},
                )
                current = CURRENT_SCHEMA_VERSION
        _restrict_file_permissions(str(self._engine.url))
        return current

    def ensure_schema(self) -> None:
        """Validate the schema without mutating it during runtime startup."""
        from sqlalchemy import text

        try:
            with self._engine.connect() as connection:
                current = int(
                    connection.execute(
                        text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}")
                    ).scalar_one()
                )
                present = int(
                    connection.execute(
                        text("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = :name"),
                        {"name": SESSION_TABLE},
                    ).scalar_one()
                )
        except Exception as exc:
            raise SessionConfigurationError(
                "The runtime SQLite session schema is unavailable; run 'osa-session-migrate' "
                "against OSA_SESSION_DATABASE_URL before starting"
            ) from exc
        if current < CURRENT_SCHEMA_VERSION or present != 1:
            raise SessionConfigurationError(
                f"SQLite session schema version {current} is older than required {CURRENT_SCHEMA_VERSION}; "
                "run 'osa-session-migrate' before starting"
            )

    def create(
        self,
        agent_name: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        ttl_seconds: int | None = None,
        max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    ) -> Session:
        from sqlalchemy import text

        session = Session(
            session_id=SessionId.generate(),
            agent_name=agent_name,
            user_id=user_id,
            tenant_id=tenant_id,
            ttl_seconds=ttl_seconds,
            max_history_messages=max_history_messages,
        )
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {SESSION_TABLE} "
                    "(session_id, agent_name, user_id, tenant_id, created_at, last_active_at, "
                    "ttl_seconds, max_history_messages, metadata, conversation_history, revision) "
                    "VALUES (:session_id, :agent_name, :user_id, :tenant_id, :created_at, :last_active_at, "
                    ":ttl_seconds, :max_history_messages, :metadata, :conversation_history, :revision)"
                ),
                self._parameters(session),
            )
        return session

    def resolve(
        self,
        session_id: str,
        *,
        agent_name: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> Session:
        session = self._load(session_id)
        if session is None or session.is_expired():
            if session is not None:
                self._delete_raw(session_id)
            raise SessionNotFoundError(session_id)
        if not session.matches_owner(agent_name=agent_name, user_id=user_id, tenant_id=tenant_id):
            raise SessionAccessError(session_id)
        return session

    def get_runtime_view(self, session_id: str) -> Session | None:
        session = self._load(session_id)
        if session is None:
            return None
        if session.is_expired():
            self._delete_raw(session_id)
            return None
        return session

    def save(self, session: Session) -> None:
        from sqlalchemy import text

        session.last_active_at = datetime.now(UTC)
        next_revision = session.revision + 1
        with self._engine.begin() as connection:
            result = connection.execute(
                text(
                    f"UPDATE {SESSION_TABLE} SET last_active_at = :last_active_at, "
                    "metadata = :metadata, conversation_history = :conversation_history, "
                    "revision = :next_revision WHERE session_id = :session_id AND revision = :revision"
                ),
                {
                    "last_active_at": _iso(session.last_active_at),
                    "metadata": _json(session.metadata),
                    "conversation_history": _json(session.conversation_history),
                    "next_revision": next_revision,
                    "session_id": str(session.session_id),
                    "revision": session.revision,
                },
            )
        if result.rowcount != 1:
            raise SessionConcurrencyError(str(session.session_id))
        session.revision = next_revision

    def delete(
        self,
        session_id: str,
        *,
        agent_name: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        session = self._load(session_id)
        if session is None:
            return False
        if not session.matches_owner(agent_name=agent_name, user_id=user_id, tenant_id=tenant_id):
            raise SessionAccessError(session_id)
        return self._delete_raw(session_id)

    def purge_expired(self) -> int:
        from sqlalchemy import text

        with self._engine.begin() as connection:
            result = connection.execute(
                text(
                    f"DELETE FROM {SESSION_TABLE} WHERE ttl_seconds IS NOT NULL "
                    "AND (julianday('now') - julianday(last_active_at)) * 86400 >= ttl_seconds"
                )
            )
        return int(result.rowcount or 0)

    def close(self) -> None:
        self._engine.dispose()

    def _load(self, session_id: str) -> Session | None:
        from sqlalchemy import text

        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        f"SELECT session_id, agent_name, user_id, tenant_id, created_at, last_active_at, "
                        f"ttl_seconds, max_history_messages, metadata, conversation_history, revision "
                        f"FROM {SESSION_TABLE} WHERE session_id = :session_id"
                    ),
                    {"session_id": session_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
        history = (
            row["conversation_history"]
            if isinstance(row["conversation_history"], list)
            else json.loads(row["conversation_history"])
        )
        return Session(
            session_id=SessionId(str(row["session_id"])),
            agent_name=str(row["agent_name"]),
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            created_at=_utc(row["created_at"]),
            last_active_at=_utc(row["last_active_at"]),
            ttl_seconds=row["ttl_seconds"],
            max_history_messages=int(row["max_history_messages"]),
            metadata=metadata,
            conversation_history=history,
            revision=int(row["revision"]),
        )

    def _delete_raw(self, session_id: str) -> bool:
        from sqlalchemy import text

        with self._engine.begin() as connection:
            result = connection.execute(
                text(f"DELETE FROM {SESSION_TABLE} WHERE session_id = :session_id"),
                {"session_id": session_id},
            )
        return bool(result.rowcount)

    @staticmethod
    def _parameters(session: Session) -> dict[str, Any]:
        return {
            "session_id": str(session.session_id),
            "agent_name": session.agent_name,
            "user_id": session.user_id,
            "tenant_id": session.tenant_id,
            "created_at": _iso(session.created_at),
            "last_active_at": _iso(session.last_active_at),
            "ttl_seconds": session.ttl_seconds,
            "max_history_messages": session.max_history_messages,
            "metadata": _json(session.metadata),
            "conversation_history": _json(session.conversation_history),
            "revision": session.revision,
        }


__all__ = ["CURRENT_SCHEMA_VERSION", "SCHEMA_VERSION_TABLE", "SESSION_TABLE", "SqliteSessionProvider"]
