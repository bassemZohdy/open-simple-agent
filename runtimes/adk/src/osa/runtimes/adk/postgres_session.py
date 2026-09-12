"""Durable PostgreSQL session provider and its explicit schema migrations.

The generic session contract is synchronous because framework adapters call it
while an invocation is already running inside an event loop.  This provider
therefore uses SQLAlchemy's synchronous PostgreSQL driver and performs each
short transaction directly; it never calls ``asyncio.run`` from request code.

Schema ownership is deliberately separate from the Control Plane Alembic
history.  ``osa-session-migrate`` owns this database's small, versioned schema
and runtime startup only validates that the required version is present.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from importlib.util import find_spec
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

SCHEMA_VERSION_TABLE = "osa_session_schema_versions"
SESSION_TABLE = "osa_runtime_sessions"
CURRENT_SCHEMA_VERSION = 1
SESSION_DATABASE_URL_ENV_VAR = "OSA_SESSION_DATABASE_URL"

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        f"""
        CREATE TABLE {SESSION_TABLE} (
            session_id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            user_id TEXT NULL,
            tenant_id TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            last_active_at TIMESTAMPTZ NOT NULL,
            ttl_seconds INTEGER NULL,
            max_history_messages INTEGER NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            conversation_history JSONB NOT NULL DEFAULT '[]'::jsonb,
            revision BIGINT NOT NULL DEFAULT 0
        )
        """,
        f"CREATE INDEX ix_{SESSION_TABLE}_owner ON {SESSION_TABLE} (tenant_id, agent_name, user_id)",
        f"CREATE INDEX ix_{SESSION_TABLE}_last_active ON {SESSION_TABLE} (last_active_at)",
    ),
}


def _require_dependencies() -> None:
    if find_spec("sqlalchemy") is None or find_spec("pg8000") is None:
        raise SessionConfigurationError(
            "The PostgreSQL session provider requires 'sqlalchemy' and 'pg8000'; "
            "install the 'osa-adk-runtime[postgres]' extra"
        )


def _validate_postgres_dsn(dsn: str) -> None:
    """Reject unsupported or malformed configured database URLs early."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        backend = make_url(dsn).get_backend_name()
    except (ArgumentError, ValueError) as exc:
        raise SessionConfigurationError("Session database URL must be a valid PostgreSQL DSN") from exc
    if backend != "postgresql":
        raise SessionConfigurationError(
            "Session database URL must use PostgreSQL; SQLite and in-memory URLs are unsupported"
        )


def _sync_dsn(dsn: str) -> str:
    """Accept the asyncpg DSN used by the other OSA stores as input."""
    if "+asyncpg" in dsn:
        return dsn.replace("+asyncpg", "+pg8000", 1)
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+pg8000://", 1)
    return dsn


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


class PostgresSessionProvider(SessionProvider):
    """Session provider with ownership checks and optimistic concurrency."""

    def __init__(self, dsn: str) -> None:
        _require_dependencies()
        _validate_postgres_dsn(dsn)
        from sqlalchemy import create_engine

        self._engine = create_engine(_sync_dsn(dsn), pool_pre_ping=True)

    def migrate(self) -> int:
        """Apply all pending session migrations and return the new version."""
        from sqlalchemy import text

        with self._engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} "
                    "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
            )
            current = int(
                connection.execute(text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}")).scalar_one()
            )
            for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[version]:
                    connection.execute(text(statement))
                connection.execute(
                    text(f"INSERT INTO {SCHEMA_VERSION_TABLE} (version) VALUES (:version)"),
                    {"version": version},
                )
                current = version
        return current

    def ensure_schema(self) -> None:
        """Validate connectivity and require an operator-applied schema."""
        from sqlalchemy import text

        try:
            with self._engine.connect() as connection:
                current = connection.execute(
                    text(f"SELECT COALESCE(MAX(version), 0) FROM {SCHEMA_VERSION_TABLE}")
                ).scalar_one()
        except Exception as exc:
            raise SessionConfigurationError(
                "The runtime session schema is unavailable; run 'osa-session-migrate' "
                "against OSA_SESSION_DATABASE_URL before starting the runtime"
            ) from exc
        if int(current) < CURRENT_SCHEMA_VERSION:
            raise SessionConfigurationError(
                f"Session schema version {current} is older than required {CURRENT_SCHEMA_VERSION}; "
                "run 'osa-session-migrate' before starting the runtime"
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
                    ":ttl_seconds, :max_history_messages, CAST(:metadata AS jsonb), "
                    "CAST(:conversation_history AS jsonb), :revision)"
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
                    "metadata = CAST(:metadata AS jsonb), conversation_history = CAST(:conversation_history AS jsonb), "
                    "revision = :next_revision WHERE session_id = :session_id AND revision = :revision"
                ),
                {
                    "last_active_at": session.last_active_at,
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
                    "AND now() - last_active_at >= ttl_seconds * INTERVAL '1 second'"
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
            "created_at": session.created_at,
            "last_active_at": session.last_active_at,
            "ttl_seconds": session.ttl_seconds,
            "max_history_messages": session.max_history_messages,
            "metadata": _json(session.metadata),
            "conversation_history": _json(session.conversation_history),
            "revision": session.revision,
        }


def migrate_cli(argv: list[str] | None = None) -> int:
    """Apply runtime session migrations from the operator environment."""
    import argparse

    parser = argparse.ArgumentParser(prog="osa-session-migrate")
    parser.add_argument("--database-url", default=os.environ.get(SESSION_DATABASE_URL_ENV_VAR))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error(f"--database-url is required (or set {SESSION_DATABASE_URL_ENV_VAR})")
    provider = PostgresSessionProvider(args.database_url)
    try:
        print(f"Applied session schema version {provider.migrate()}")
    finally:
        provider.close()
    return 0


__all__ = ["CURRENT_SCHEMA_VERSION", "PostgresSessionProvider", "migrate_cli"]
