"""Lease and fencing primitives for distributed deployment operations.

The PostgreSQL implementation is deliberately migration-owned and does not
create schema at startup. The in-memory implementation is a safe default for
single-process development and tests, but it is not a coordination mechanism
for multiple Control Plane replicas.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from osa.control_plane.backend.deployment_errors import DeploymentError

DEPLOYMENT_OPERATION_LEASE_SECONDS_ENV_VAR = "OSA_DEPLOYMENT_OPERATION_LEASE_SECONDS"
DEFAULT_DEPLOYMENT_OPERATION_LEASE_SECONDS = 30
MIN_DEPLOYMENT_OPERATION_LEASE_SECONDS = 5
_OWNERSHIP_TABLE_NAME = "osa_deployment_operation_owners"


@dataclass(frozen=True)
class DeploymentOperationLease:
    """The capability required to perform and persist one operation."""

    tenant_id: str | None
    resource_id: str
    operation_id: str
    owner_id: str
    fencing_epoch: int
    attempt: int
    lease_expires_at: datetime


class DeploymentOperationOwnershipStore(ABC):
    """Coordinate mutating operations for one deployment resource."""

    @property
    @abstractmethod
    def lease_seconds(self) -> int:
        """Return the lease duration used for new ownership claims."""
        ...

    @abstractmethod
    async def initialize(self) -> None:
        """Validate that the required migration-owned schema is available."""
        ...

    @abstractmethod
    async def acquire(
        self,
        tenant_id: str | None,
        resource_id: str,
        operation_id: str,
    ) -> DeploymentOperationLease | None:
        """Acquire ownership, returning ``None`` when another owner is live."""
        ...

    @abstractmethod
    async def heartbeat(self, lease: DeploymentOperationLease) -> bool:
        """Extend a live lease if this owner still holds its fencing epoch."""
        ...

    @abstractmethod
    async def is_current(self, lease: DeploymentOperationLease) -> bool:
        """Check whether the lease remains current and unexpired."""
        ...

    @abstractmethod
    async def release(self, lease: DeploymentOperationLease, outcome: str) -> bool:
        """Release a lease without allowing an old owner to release a new one."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release local resources; shared stores do not own the DB engine."""
        ...


@dataclass
class _InMemoryLeaseEntry:
    operation_id: str
    owner_id: str
    fencing_epoch: int
    attempt: int
    lease_expires_at: datetime
    active: bool = True


class InMemoryDeploymentOperationOwnershipStore(DeploymentOperationOwnershipStore):
    """Single-process ownership store for development and local SQLite use."""

    def __init__(
        self,
        *,
        owner_id: str | None = None,
        lease_seconds: int = DEFAULT_DEPLOYMENT_OPERATION_LEASE_SECONDS,
    ) -> None:
        _validate_lease_seconds(lease_seconds)
        self._owner_id = owner_id or f"local-{uuid4().hex}"
        self._lease_seconds = lease_seconds
        self._lock = asyncio.Lock()
        self._entries: dict[tuple[str, str], _InMemoryLeaseEntry] = {}

    @property
    def lease_seconds(self) -> int:
        return self._lease_seconds

    async def initialize(self) -> None:
        return None

    async def acquire(
        self,
        tenant_id: str | None,
        resource_id: str,
        operation_id: str,
    ) -> DeploymentOperationLease | None:
        key = (_scope(tenant_id), resource_id)
        now = datetime.now(UTC)
        async with self._lock:
            existing = self._entries.get(key)
            if existing is not None and existing.active and existing.lease_expires_at > now:
                return None
            fencing_epoch = (existing.fencing_epoch if existing is not None else 0) + 1
            attempt = (existing.attempt if existing is not None else 0) + 1
            expires_at = now + timedelta(seconds=self._lease_seconds)
            entry = _InMemoryLeaseEntry(
                operation_id=operation_id,
                owner_id=self._owner_id,
                fencing_epoch=fencing_epoch,
                attempt=attempt,
                lease_expires_at=expires_at,
            )
            self._entries[key] = entry
            return _lease(tenant_id, resource_id, entry)

    async def heartbeat(self, lease: DeploymentOperationLease) -> bool:
        key = (_scope(lease.tenant_id), lease.resource_id)
        now = datetime.now(UTC)
        async with self._lock:
            entry = self._entries.get(key)
            if not _matches(entry, lease, now):
                return False
            if entry is None:
                return False
            entry.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            return True

    async def is_current(self, lease: DeploymentOperationLease) -> bool:
        key = (_scope(lease.tenant_id), lease.resource_id)
        now = datetime.now(UTC)
        async with self._lock:
            return _matches(self._entries.get(key), lease, now)

    async def release(self, lease: DeploymentOperationLease, outcome: str) -> bool:
        del outcome  # The local store does not retain historical outcomes.
        key = (_scope(lease.tenant_id), lease.resource_id)
        now = datetime.now(UTC)
        async with self._lock:
            entry = self._entries.get(key)
            if not _matches(entry, lease, now):
                return False
            assert entry is not None
            entry.active = False
            return True

    async def close(self) -> None:
        return None


class PostgresDeploymentOperationOwnershipStore(DeploymentOperationOwnershipStore):
    """Migration-owned PostgreSQL coordination for multi-replica Control Planes."""

    def __init__(
        self,
        engine: Any,
        *,
        owner_id: str | None = None,
        lease_seconds: int = DEFAULT_DEPLOYMENT_OPERATION_LEASE_SECONDS,
    ) -> None:
        _validate_lease_seconds(lease_seconds)
        self._engine = engine
        self._lease_seconds = lease_seconds
        self._owner_id = owner_id or f"pg-{uuid4().hex}"

    @property
    def lease_seconds(self) -> int:
        return self._lease_seconds

    async def initialize(self) -> None:
        from sqlalchemy import inspect

        required = {
            "tenant_id",
            "resource_id",
            "operation_id",
            "owner_id",
            "fencing_epoch",
            "attempt",
            "state",
            "last_outcome",
            "lease_expires_at",
            "heartbeat_at",
            "created_at",
            "updated_at",
        }

        async with self._engine.connect() as connection:
            table_exists = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).has_table(_OWNERSHIP_TABLE_NAME)
            )
            columns = (
                await connection.run_sync(
                    lambda sync_connection: {
                        column["name"] for column in inspect(sync_connection).get_columns(_OWNERSHIP_TABLE_NAME)
                    }
                )
                if table_exists
                else set()
            )
        if not table_exists or not required.issubset(columns):
            missing = sorted(required - columns) if table_exists else sorted(required)
            raise DeploymentError(
                "Deployment-operation ownership schema is not ready; run osa-cp-migrate "
                f"(missing: {', '.join(missing)})"
            )

    async def acquire(
        self,
        tenant_id: str | None,
        resource_id: str,
        operation_id: str,
    ) -> DeploymentOperationLease | None:
        from sqlalchemy import func, select
        from sqlalchemy.dialects.postgresql import insert

        table = deployment_operation_owners_table()
        scope = _scope(tenant_id)
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(table)
                .values(
                    tenant_id=scope,
                    resource_id=resource_id,
                    operation_id="",
                    owner_id="",
                    fencing_epoch=0,
                    attempt=0,
                    state="released",
                    last_outcome=None,
                    lease_expires_at=datetime(1970, 1, 1, tzinfo=UTC),
                    heartbeat_at=None,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "resource_id"])
            )
            result = await connection.execute(
                select(table, func.now().label("database_now"))
                .where(table.c.tenant_id == scope, table.c.resource_id == resource_id)
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None:
                raise DeploymentError("Unable to read deployment-operation ownership row")
            database_now = _as_utc(row["database_now"])
            if row["state"] == "active" and _as_utc(row["lease_expires_at"]) > database_now:
                return None
            fencing_epoch = int(row["fencing_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            expires_at = database_now + timedelta(seconds=self._lease_seconds)
            await connection.execute(
                table.update()
                .where(table.c.tenant_id == scope, table.c.resource_id == resource_id)
                .values(
                    operation_id=operation_id,
                    owner_id=self._owner_id,
                    fencing_epoch=fencing_epoch,
                    attempt=attempt,
                    state="active",
                    last_outcome=None,
                    lease_expires_at=expires_at,
                    heartbeat_at=database_now,
                    updated_at=database_now,
                )
            )
        return DeploymentOperationLease(
            tenant_id=tenant_id,
            resource_id=resource_id,
            operation_id=operation_id,
            owner_id=self._owner_id,
            fencing_epoch=fencing_epoch,
            attempt=attempt,
            lease_expires_at=expires_at,
        )

    async def heartbeat(self, lease: DeploymentOperationLease) -> bool:
        from sqlalchemy import func, select

        table = deployment_operation_owners_table()
        scope = _scope(lease.tenant_id)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(table, func.now().label("database_now"))
                .where(table.c.tenant_id == scope, table.c.resource_id == lease.resource_id)
                .with_for_update()
            )
            row = result.mappings().first()
            if row is None:
                return False
            database_now = _as_utc(row["database_now"])
            if not _row_matches(row, lease, database_now):
                return False
            expires_at = database_now + timedelta(seconds=self._lease_seconds)
            await connection.execute(
                table.update()
                .where(
                    table.c.tenant_id == scope,
                    table.c.resource_id == lease.resource_id,
                    table.c.owner_id == lease.owner_id,
                    table.c.fencing_epoch == lease.fencing_epoch,
                    table.c.state == "active",
                )
                .values(lease_expires_at=expires_at, heartbeat_at=database_now, updated_at=database_now)
            )
        return True

    async def is_current(self, lease: DeploymentOperationLease) -> bool:
        from sqlalchemy import func, select

        table = deployment_operation_owners_table()
        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(table, func.now().label("database_now")).where(
                    table.c.tenant_id == _scope(lease.tenant_id),
                    table.c.resource_id == lease.resource_id,
                )
            )
            row = result.mappings().first()
        return row is not None and _row_matches(row, lease, _as_utc(row["database_now"]))

    async def release(self, lease: DeploymentOperationLease, outcome: str) -> bool:
        from sqlalchemy import func, update

        table = deployment_operation_owners_table()
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(table)
                .where(
                    table.c.tenant_id == _scope(lease.tenant_id),
                    table.c.resource_id == lease.resource_id,
                    table.c.operation_id == lease.operation_id,
                    table.c.owner_id == lease.owner_id,
                    table.c.fencing_epoch == lease.fencing_epoch,
                    table.c.state == "active",
                )
                .values(
                    state="released",
                    last_outcome=outcome,
                    lease_expires_at=func.now(),
                    updated_at=func.now(),
                )
            )
        return bool(result.rowcount)

    async def close(self) -> None:
        return None


_OWNERSHIP_TABLE: Any = None


def deployment_operation_owners_table() -> Any:
    """Return the lazily-built SQLAlchemy table used by PG stores/repos."""
    global _OWNERSHIP_TABLE
    if _OWNERSHIP_TABLE is None:
        from sqlalchemy import BigInteger, Column, DateTime, Index, MetaData, PrimaryKeyConstraint, Table, Text, func

        metadata = MetaData()
        _OWNERSHIP_TABLE = Table(
            _OWNERSHIP_TABLE_NAME,
            metadata,
            Column("tenant_id", Text, nullable=False),
            Column("resource_id", Text, nullable=False),
            Column("operation_id", Text, nullable=False),
            Column("owner_id", Text, nullable=False),
            Column("fencing_epoch", BigInteger, nullable=False),
            Column("attempt", BigInteger, nullable=False),
            Column("state", Text, nullable=False),
            Column("last_outcome", Text, nullable=True),
            Column("lease_expires_at", DateTime(timezone=True), nullable=False),
            Column("heartbeat_at", DateTime(timezone=True), nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
            Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
            PrimaryKeyConstraint("tenant_id", "resource_id", name="pk_osa_deployment_operation_owners"),
        )
        Index(
            "ix_osa_deployment_operation_owners_operation",
            _OWNERSHIP_TABLE.c.operation_id,
            _OWNERSHIP_TABLE.c.owner_id,
        )
    return _OWNERSHIP_TABLE


def deployment_operation_lease_seconds_from_env() -> int:
    raw = os.environ.get(DEPLOYMENT_OPERATION_LEASE_SECONDS_ENV_VAR)
    if raw is None:
        return DEFAULT_DEPLOYMENT_OPERATION_LEASE_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise DeploymentError(
            f"{DEPLOYMENT_OPERATION_LEASE_SECONDS_ENV_VAR} must be an integer of at least "
            f"{MIN_DEPLOYMENT_OPERATION_LEASE_SECONDS} seconds"
        ) from exc
    _validate_lease_seconds(value)
    return value


def _validate_lease_seconds(value: int) -> None:
    if value < MIN_DEPLOYMENT_OPERATION_LEASE_SECONDS:
        raise DeploymentError(
            f"{DEPLOYMENT_OPERATION_LEASE_SECONDS_ENV_VAR} must be at least "
            f"{MIN_DEPLOYMENT_OPERATION_LEASE_SECONDS} seconds"
        )


def _scope(tenant_id: str | None) -> str:
    return tenant_id or ""


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _lease(tenant_id: str | None, resource_id: str, entry: _InMemoryLeaseEntry) -> DeploymentOperationLease:
    return DeploymentOperationLease(
        tenant_id=tenant_id,
        resource_id=resource_id,
        operation_id=entry.operation_id,
        owner_id=entry.owner_id,
        fencing_epoch=entry.fencing_epoch,
        attempt=entry.attempt,
        lease_expires_at=entry.lease_expires_at,
    )


def _matches(entry: _InMemoryLeaseEntry | None, lease: DeploymentOperationLease, now: datetime) -> bool:
    return (
        entry is not None
        and entry.active
        and entry.operation_id == lease.operation_id
        and entry.owner_id == lease.owner_id
        and entry.fencing_epoch == lease.fencing_epoch
        and entry.lease_expires_at > now
    )


def _row_matches(row: Any, lease: DeploymentOperationLease, now: datetime) -> bool:
    return (
        row["state"] == "active"
        and row["operation_id"] == lease.operation_id
        and row["owner_id"] == lease.owner_id
        and int(row["fencing_epoch"]) == lease.fencing_epoch
        and _as_utc(row["lease_expires_at"]) > now
    )


__all__ = [
    "DEFAULT_DEPLOYMENT_OPERATION_LEASE_SECONDS",
    "DEPLOYMENT_OPERATION_LEASE_SECONDS_ENV_VAR",
    "DeploymentOperationLease",
    "DeploymentOperationOwnershipStore",
    "InMemoryDeploymentOperationOwnershipStore",
    "MIN_DEPLOYMENT_OPERATION_LEASE_SECONDS",
    "PostgresDeploymentOperationOwnershipStore",
    "deployment_operation_lease_seconds_from_env",
    "deployment_operation_owners_table",
]
