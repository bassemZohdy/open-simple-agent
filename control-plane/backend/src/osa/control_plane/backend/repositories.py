"""Control Plane repository contracts and implementations (ADR-004).

The abstract interfaces are the storage contract; the in-memory
implementations back tests and single-process development, and the PostgreSQL
implementations provide durable, replica-shared state. Both raise the same
typed errors so API error mapping stays backend-agnostic.

SQLAlchemy is imported lazily inside the PostgreSQL implementations so the
in-memory default never requires the optional ``postgres`` extra.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from osa.control_plane.backend.agent_catalog import (
    _VALID_TRANSITIONS,
    AgentCatalog,
    AgentCatalogError,
    AgentRecord,
    AgentRecordStatus,
    AgentVersion,
    ConcurrentUpdateError,
    DuplicateAgentError,
    DuplicateVersionError,
    InvalidTransitionError,
)

if TYPE_CHECKING:
    from osa.generic_agent import AgentDefinition

__all__ = [
    "AgentRepository",
    "AuditEvent",
    "AuditEventRepository",
    "ConcurrentUpdateError",
    "DeploymentRecord",
    "DeploymentRecordRepository",
    "DuplicateAgentError",
    "DuplicateVersionError",
    "InMemoryAgentRepository",
    "InMemoryAuditEventRepository",
    "InMemoryDeploymentRecordRepository",
    "InMemoryResourceDefinitionRepository",
    "InvalidTransitionError",
    "PostgresAgentRepository",
    "PostgresResourceDefinitionRepository",
    "ResourceDefinitionRepository",
    "dump_definition",
    "load_definition",
]

_UPDATABLE_COLUMNS = frozenset({"name", "description", "labels", "definition", "skills", "runtime", "endpoint"})


def dump_definition(definition: AgentDefinition | dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialize a definition for JSONB storage (stable wire form)."""
    if definition is None:
        return None
    if isinstance(definition, dict):
        return definition
    return definition.model_dump(mode="json", by_alias=True)


def load_definition(data: dict[str, Any] | None) -> AgentDefinition | None:
    """Rebuild a definition from JSONB storage."""
    from osa.generic_agent import AgentDefinition

    if data is None:
        return None
    return AgentDefinition.model_validate(data)


# ---------------------------------------------------------------------------
# Agent + version repository
# ---------------------------------------------------------------------------


class AgentRepository(ABC):
    """Persistence contract for agent records and immutable versions."""

    @abstractmethod
    async def create(self, record: AgentRecord) -> AgentRecord:
        """Store a new record; raises DuplicateAgentError on name conflict."""
        ...

    @abstractmethod
    async def get(self, agent_id: str) -> AgentRecord | None:
        """Fetch one record with its version history."""
        ...

    @abstractmethod
    async def get_by_name(self, name: str) -> AgentRecord | None: ...

    @abstractmethod
    async def list_all(self) -> list[AgentRecord]: ...

    @abstractmethod
    async def search(self, query: str) -> list[AgentRecord]: ...

    @abstractmethod
    async def filter_by_status(self, status: AgentRecordStatus) -> list[AgentRecord]: ...

    @abstractmethod
    async def filter_by_skill(self, skill: str) -> list[AgentRecord]: ...

    @abstractmethod
    async def filter_by_runtime(self, runtime: str) -> list[AgentRecord]: ...

    @abstractmethod
    async def update(self, agent_id: str, *, expected_version: str | None = None, **fields: Any) -> AgentRecord:
        """Apply a field update.

        ``expected_version`` enables compare-and-set on ``current_version``:
        a mismatch raises :class:`ConcurrentUpdateError`. Unknown agents raise
        KeyError.
        """
        ...

    @abstractmethod
    async def transition(self, agent_id: str, new_status: AgentRecordStatus) -> AgentRecord:
        """Apply a lifecycle transition; invalid moves raise InvalidTransitionError."""
        ...

    @abstractmethod
    async def delete(self, agent_id: str) -> bool: ...

    @abstractmethod
    async def add_version(self, agent_id: str, version: AgentVersion) -> AgentVersion:
        """Append an immutable version snapshot; duplicates raise
        DuplicateVersionError."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources (connections); in-memory backends are no-ops."""
        ...


class InMemoryAgentRepository(AgentRepository):
    """In-memory repository delegating to :class:`AgentCatalog`."""

    def __init__(self, catalog: AgentCatalog | None = None) -> None:
        self._catalog = catalog if catalog is not None else AgentCatalog()

    @property
    def catalog(self) -> AgentCatalog:
        """The underlying catalog (tests clear state through it)."""
        return self._catalog

    async def create(self, record: AgentRecord) -> AgentRecord:
        return self._catalog.create(record)

    async def get(self, agent_id: str) -> AgentRecord | None:
        return self._catalog.get(agent_id)

    async def get_by_name(self, name: str) -> AgentRecord | None:
        return self._catalog.get_by_name(name)

    async def list_all(self) -> list[AgentRecord]:
        return self._catalog.list_all()

    async def search(self, query: str) -> list[AgentRecord]:
        return self._catalog.search(query)

    async def filter_by_status(self, status: AgentRecordStatus) -> list[AgentRecord]:
        return self._catalog.filter_by_status(status)

    async def filter_by_skill(self, skill: str) -> list[AgentRecord]:
        return self._catalog.filter_by_skill(skill)

    async def filter_by_runtime(self, runtime: str) -> list[AgentRecord]:
        return self._catalog.filter_by_runtime(runtime)

    async def update(self, agent_id: str, *, expected_version: str | None = None, **fields: Any) -> AgentRecord:
        return self._catalog.update(agent_id, expected_version=expected_version, **fields)

    async def transition(self, agent_id: str, new_status: AgentRecordStatus) -> AgentRecord:
        return self._catalog.transition(agent_id, new_status)

    async def delete(self, agent_id: str) -> bool:
        return self._catalog.delete(agent_id)

    async def add_version(self, agent_id: str, version: AgentVersion) -> AgentVersion:
        return self._catalog.add_version(agent_id, version)

    async def close(self) -> None:
        return None


class PostgresAgentRepository(AgentRepository):
    """PostgreSQL repository: transactions, unique constraints, CAS locking."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def close(self) -> None:
        await self._engine.dispose()

    # -- mapping helpers --

    @staticmethod
    def _record_from_row(row: Any, versions: list[AgentVersion]) -> AgentRecord:
        return AgentRecord(
            agent_id=row.agent_id,
            name=row.name,
            description=row.description,
            status=AgentRecordStatus(row.status),
            current_version=row.current_version,
            runtime=row.runtime,
            endpoint=row.endpoint,
            definition=load_definition(row.definition),
            skills=list(row.skills or []),
            labels=dict(row.labels or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
            versions=versions,
        )

    @staticmethod
    def _version_from_row(row: Any) -> AgentVersion:
        return AgentVersion(
            version_id=str(row.id),
            version=row.version,
            definition=load_definition(row.definition),
            created_at=row.created_at,
            created_by=row.created_by,
            change_summary=row.change_summary,
        )

    async def _fetch_versions(self, connection: Any, agent_id: str) -> list[AgentVersion]:
        from sqlalchemy import select

        from osa.control_plane.backend.tables import agent_versions_table

        result = await connection.execute(
            select(agent_versions_table)
            .where(agent_versions_table.c.agent_id == agent_id)
            .order_by(agent_versions_table.c.created_at.asc(), agent_versions_table.c.id.asc())
        )
        return [self._version_from_row(row) for row in result.fetchall()]

    @staticmethod
    def _translate_integrity_error(exc: Exception, agent_name: str, version: str) -> AgentCatalogError:
        message = str(exc)
        if "uq_osa_agents_name" in message:
            return DuplicateAgentError(agent_name)
        if "uq_osa_agent_versions_agent_version" in message:
            return DuplicateVersionError(agent_name, version)
        return AgentCatalogError(f"integrity constraint violated: {message}")

    # -- reads --

    async def get(self, agent_id: str) -> AgentRecord | None:
        from sqlalchemy import select

        from osa.control_plane.backend.tables import agents_table

        async with self._engine.begin() as connection:
            result = await connection.execute(select(agents_table).where(agents_table.c.agent_id == agent_id))
            row = result.first()
            if row is None:
                return None
            return self._record_from_row(row, await self._fetch_versions(connection, agent_id))

    async def get_by_name(self, name: str) -> AgentRecord | None:
        from sqlalchemy import select

        from osa.control_plane.backend.tables import agents_table

        async with self._engine.begin() as connection:
            result = await connection.execute(select(agents_table).where(agents_table.c.name == name))
            row = result.first()
            if row is None:
                return None
            return self._record_from_row(row, await self._fetch_versions(connection, row.agent_id))

    async def list_all(self) -> list[AgentRecord]:
        return await self._query_list(None)

    async def search(self, query: str) -> list[AgentRecord]:
        from sqlalchemy import func, or_

        from osa.control_plane.backend.tables import agents_table

        like = f"%{query.lower()}%"
        where = or_(
            func.lower(agents_table.c.name).like(like),
            func.lower(agents_table.c.description).like(like),
        )
        return await self._query_list(where)

    async def filter_by_status(self, status: AgentRecordStatus) -> list[AgentRecord]:
        from osa.control_plane.backend.tables import agents_table

        return await self._query_list(agents_table.c.status == status.value)

    async def filter_by_skill(self, skill: str) -> list[AgentRecord]:
        from sqlalchemy import Text, func

        from osa.control_plane.backend.tables import agents_table

        where = func.lower(func.cast(agents_table.c.skills, Text)).like(f'%"{skill.lower()}"%')
        return await self._query_list(where)

    async def filter_by_runtime(self, runtime: str) -> list[AgentRecord]:
        from osa.control_plane.backend.tables import agents_table

        return await self._query_list(agents_table.c.runtime == runtime)

    async def _query_list(self, where: Any) -> list[AgentRecord]:
        from sqlalchemy import select

        from osa.control_plane.backend.tables import agents_table

        statement = select(agents_table)
        if where is not None:
            statement = statement.where(where)
        statement = statement.order_by(agents_table.c.name.asc())
        async with self._engine.begin() as connection:
            result = await connection.execute(statement)
            return [self._record_from_row(row, []) for row in result.fetchall()]

    # -- writes --

    async def create(self, record: AgentRecord) -> AgentRecord:
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError

        from osa.control_plane.backend.tables import agents_table

        async with self._engine.begin() as connection:
            try:
                await connection.execute(
                    insert(agents_table).values(
                        agent_id=record.agent_id,
                        name=record.name,
                        description=record.description,
                        status=record.status.value,
                        current_version=record.current_version,
                        runtime=record.runtime,
                        endpoint=record.endpoint,
                        definition=dump_definition(record.definition),
                        skills=record.skills,
                        labels=record.labels,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                    )
                )
            except IntegrityError as exc:
                raise self._translate_integrity_error(exc, record.name, record.current_version) from exc
        return record

    async def update(self, agent_id: str, *, expected_version: str | None = None, **fields: Any) -> AgentRecord:
        from sqlalchemy import select, update

        from osa.control_plane.backend.tables import agents_table

        values = {key: fields[key] for key in fields if key in _UPDATABLE_COLUMNS}
        if not values:
            record = await self.get(agent_id)
            if record is None:
                raise KeyError(f"Agent not found: {agent_id}")
            return record
        values["updated_at"] = datetime.now(UTC)

        async with self._engine.begin() as connection:
            statement = update(agents_table).where(agents_table.c.agent_id == agent_id)
            if expected_version is not None:
                statement = statement.where(agents_table.c.current_version == expected_version)
            result = await connection.execute(statement.values(**values))
            if result.rowcount == 0:
                existing = await connection.execute(
                    select(agents_table.c.agent_id).where(agents_table.c.agent_id == agent_id)
                )
                if existing.first() is None:
                    raise KeyError(f"Agent not found: {agent_id}")
                raise ConcurrentUpdateError(agent_id, expected_version or "")

        record = await self.get(agent_id)
        assert record is not None
        return record

    async def transition(self, agent_id: str, new_status: AgentRecordStatus) -> AgentRecord:
        from sqlalchemy import select, update

        from osa.control_plane.backend.tables import agents_table

        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(agents_table).where(agents_table.c.agent_id == agent_id).with_for_update()
            )
            row = result.first()
            if row is None:
                raise KeyError(f"Agent not found: {agent_id}")
            current = AgentRecordStatus(row.status)
            if new_status != current and new_status not in _VALID_TRANSITIONS[current]:
                raise InvalidTransitionError(current, new_status)
            await connection.execute(
                update(agents_table)
                .where(agents_table.c.agent_id == agent_id)
                .values(status=new_status.value, updated_at=datetime.now(UTC))
            )
        record = await self.get(agent_id)
        assert record is not None
        return record

    async def delete(self, agent_id: str) -> bool:
        from sqlalchemy import delete

        from osa.control_plane.backend.tables import agents_table

        async with self._engine.begin() as connection:
            result = await connection.execute(delete(agents_table).where(agents_table.c.agent_id == agent_id))
        return bool(result.rowcount)

    async def add_version(self, agent_id: str, version: AgentVersion) -> AgentVersion:
        from sqlalchemy import insert, select, update

        from osa.control_plane.backend.tables import agent_versions_table, agents_table

        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(agents_table).where(agents_table.c.agent_id == agent_id).with_for_update()
            )
            row = result.first()
            if row is None:
                raise KeyError(f"Agent not found: {agent_id}")
            agent_name = row.name
            existing = await connection.execute(
                select(agent_versions_table.c.version).where(
                    agent_versions_table.c.agent_id == agent_id,
                    agent_versions_table.c.version == version.version,
                )
            )
            if existing.first() is not None:
                raise DuplicateVersionError(agent_name, version.version)

            # The snapshot is the row's stored definition JSON — later record
            # mutations can never rewrite an existing version's history.
            snapshot_json = version.definition if version.definition is not None else load_definition(row.definition)
            version.definition = snapshot_json
            await connection.execute(
                insert(agent_versions_table).values(
                    agent_id=agent_id,
                    version=version.version,
                    definition=dump_definition(snapshot_json),
                    change_summary=version.change_summary,
                    created_by=version.created_by,
                    created_at=version.created_at,
                )
            )
            await connection.execute(
                update(agents_table)
                .where(agents_table.c.agent_id == agent_id)
                .values(
                    current_version=version.version,
                    definition=dump_definition(snapshot_json),
                    updated_at=datetime.now(UTC),
                )
            )
        return version


# ---------------------------------------------------------------------------
# Resource definitions repository
# ---------------------------------------------------------------------------


class ResourceDefinitionRepository(ABC):
    """Persistence contract for catalog resource definitions (kind + JSONB)."""

    @abstractmethod
    async def upsert(self, kind: str, name: str, spec: dict[str, Any]) -> None: ...

    @abstractmethod
    async def get(self, kind: str, name: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list(self, kind: str) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, kind: str, name: str) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class InMemoryResourceDefinitionRepository(ResourceDefinitionRepository):
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    async def upsert(self, kind: str, name: str, spec: dict[str, Any]) -> None:
        self._items[(kind, name)] = spec

    async def get(self, kind: str, name: str) -> dict[str, Any] | None:
        return self._items.get((kind, name))

    async def list(self, kind: str) -> dict[str, dict[str, Any]]:
        return {name: spec for (k, name), spec in self._items.items() if k == kind}

    async def delete(self, kind: str, name: str) -> bool:
        return self._items.pop((kind, name), None) is not None

    async def close(self) -> None:
        return None


class PostgresResourceDefinitionRepository(ResourceDefinitionRepository):
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def close(self) -> None:
        await self._engine.dispose()

    async def upsert(self, kind: str, name: str, spec: dict[str, Any]) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from osa.control_plane.backend.tables import resource_definitions_table

        async with self._engine.begin() as connection:
            await connection.execute(
                insert(resource_definitions_table)
                .values(kind=kind, name=name, spec=spec)
                .on_conflict_do_update(
                    index_elements=["kind", "name"],
                    set_={"spec": spec, "updated_at": datetime.now(UTC)},
                )
            )

    async def get(self, kind: str, name: str) -> dict[str, Any] | None:
        from sqlalchemy import select

        from osa.control_plane.backend.tables import resource_definitions_table

        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(resource_definitions_table.c.spec).where(
                    resource_definitions_table.c.kind == kind,
                    resource_definitions_table.c.name == name,
                )
            )
            row = result.first()
        return dict(row.spec) if row is not None else None

    async def list(self, kind: str) -> dict[str, dict[str, Any]]:
        from sqlalchemy import select

        from osa.control_plane.backend.tables import resource_definitions_table

        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(resource_definitions_table.c.name, resource_definitions_table.c.spec).where(
                    resource_definitions_table.c.kind == kind
                )
            )
            rows = result.fetchall()
        return {row.name: dict(row.spec) for row in rows}

    async def delete(self, kind: str, name: str) -> bool:
        from sqlalchemy import delete

        from osa.control_plane.backend.tables import resource_definitions_table

        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(resource_definitions_table).where(
                    resource_definitions_table.c.kind == kind,
                    resource_definitions_table.c.name == name,
                )
            )
        return bool(result.rowcount)


# ---------------------------------------------------------------------------
# Deployment records (interface now; persistence wired in P1.5)
# ---------------------------------------------------------------------------


@dataclass
class DeploymentRecord:
    """Persisted deployment intent and last observed state."""

    deployment_id: str
    agent_id: str
    status: str = "starting"
    detail: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class DeploymentRecordRepository(ABC):
    """Persistence contract for deployment records."""

    @abstractmethod
    async def upsert(self, record: DeploymentRecord) -> None: ...

    @abstractmethod
    async def get(self, deployment_id: str) -> DeploymentRecord | None: ...

    @abstractmethod
    async def list_for_agent(self, agent_id: str) -> list[DeploymentRecord]: ...

    @abstractmethod
    async def delete(self, deployment_id: str) -> bool: ...


class InMemoryDeploymentRecordRepository(DeploymentRecordRepository):
    def __init__(self) -> None:
        self._records: dict[str, DeploymentRecord] = {}

    async def upsert(self, record: DeploymentRecord) -> None:
        self._records[record.deployment_id] = record

    async def get(self, deployment_id: str) -> DeploymentRecord | None:
        return self._records.get(deployment_id)

    async def list_for_agent(self, agent_id: str) -> list[DeploymentRecord]:
        return [r for r in self._records.values() if r.agent_id == agent_id]

    async def delete(self, deployment_id: str) -> bool:
        return self._records.pop(deployment_id, None) is not None


# ---------------------------------------------------------------------------
# Audit metadata (interface now; enforcement in P2.2)
# ---------------------------------------------------------------------------


@dataclass
class AuditEvent:
    """One management mutation or privileged invocation."""

    event_id: str
    actor: str
    action: str
    target: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    detail: dict[str, Any] = field(default_factory=dict)


class AuditEventRepository(ABC):
    """Persistence contract for audit events (append-only)."""

    @abstractmethod
    async def append(self, event: AuditEvent) -> None: ...

    @abstractmethod
    async def list_events(self, limit: int = 100) -> list[AuditEvent]: ...


class InMemoryAuditEventRepository(AuditEventRepository):
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    async def list_events(self, limit: int = 100) -> list[AuditEvent]:
        return list(self._events[-limit:])
