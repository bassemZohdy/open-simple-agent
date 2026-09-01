"""Agent Catalog — persistence and operations for agent records.

The catalog stores AgentDefinition and metadata. It does NOT store
in-memory runtime Agent objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from osa.generic_agent import AgentDefinition


class AgentRecordStatus(StrEnum):
    """Status of an agent record in the catalog."""

    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


# Allowed lifecycle transitions; archived is terminal.
_VALID_TRANSITIONS: dict[AgentRecordStatus, set[AgentRecordStatus]] = {
    AgentRecordStatus.DRAFT: {AgentRecordStatus.ACTIVE, AgentRecordStatus.ARCHIVED},
    AgentRecordStatus.ACTIVE: {AgentRecordStatus.DISABLED, AgentRecordStatus.ARCHIVED},
    AgentRecordStatus.DISABLED: {AgentRecordStatus.ACTIVE, AgentRecordStatus.ARCHIVED},
    AgentRecordStatus.ARCHIVED: set(),
}


class AgentCatalogError(Exception):
    """Base error for agent catalog operations."""


class DuplicateAgentError(AgentCatalogError):
    """An agent with the same name already exists."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Agent with name '{name}' already exists")


class InvalidTransitionError(AgentCatalogError):
    """A lifecycle transition that is not allowed was requested."""

    def __init__(self, current: AgentRecordStatus, requested: AgentRecordStatus) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"Cannot transition agent from '{current.value}' to '{requested.value}'")


class DuplicateVersionError(AgentCatalogError):
    """A version with the same identifier already exists for the agent."""

    def __init__(self, agent_name: str, version: str) -> None:
        self.agent_name = agent_name
        self.version = version
        super().__init__(f"Version '{version}' already exists for agent '{agent_name}'")


class ConcurrentUpdateError(AgentCatalogError):
    """An update expected a current_version that no longer matches."""

    def __init__(self, agent_id: str, expected_version: str) -> None:
        self.agent_id = agent_id
        self.expected_version = expected_version
        super().__init__(
            f"Version conflict on agent '{agent_id}': expected current version '{expected_version}' does not match"
        )


@dataclass
class AgentVersion:
    """A versioned snapshot of an agent definition."""

    version_id: str = field(default_factory=lambda: str(uuid4()))
    version: str = "1.0.0"
    definition: AgentDefinition | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_by: str = ""
    change_summary: str = ""


@dataclass
class AgentRecord:
    """Persistent agent record in the catalog.

    Stores the agent definition and metadata. Does NOT store
    runtime Agent objects.
    """

    agent_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    description: str = ""
    status: AgentRecordStatus = AgentRecordStatus.DRAFT
    definition: AgentDefinition | None = None
    versions: list[AgentVersion] = field(default_factory=list)
    current_version: str = "1.0.0"
    skills: list[str] = field(default_factory=list)
    runtime: str = "adk"
    endpoint: str | None = None
    #: "managed" agents are deployed by OSA; "external" agents are A2A
    #: records that OSA never deploys (P2.1).
    agent_type: str = "managed"
    tenant_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AgentCatalog:
    """In-memory agent catalog.

    Provides CRUD operations for agent records. Can be extended
    with a persistent backend (e.g., PostgreSQL) for production use.
    """

    def __init__(self) -> None:
        self._records: dict[str, AgentRecord] = {}

    def create(self, record: AgentRecord) -> AgentRecord:
        """Create a new agent record."""
        if record.name in {r.name for r in self._records.values()}:
            raise DuplicateAgentError(record.name)
        self._records[record.agent_id] = record
        return record

    def get(self, agent_id: str) -> AgentRecord | None:
        """Get an agent record by ID."""
        return self._records.get(agent_id)

    def get_by_name(self, name: str) -> AgentRecord | None:
        """Get an agent record by name."""
        for record in self._records.values():
            if record.name == name:
                return record
        return None

    def list_all(self) -> list[AgentRecord]:
        """List all agent records."""
        return list(self._records.values())

    def search(self, query: str) -> list[AgentRecord]:
        """Search agents by name or description."""
        query_lower = query.lower()
        return [
            r for r in self._records.values() if query_lower in r.name.lower() or query_lower in r.description.lower()
        ]

    def filter_by_status(self, status: AgentRecordStatus) -> list[AgentRecord]:
        """Filter agents by status."""
        return [r for r in self._records.values() if r.status == status]

    def filter_by_skill(self, skill: str) -> list[AgentRecord]:
        """Filter agents by skill."""
        return [r for r in self._records.values() if skill in r.skills]

    def filter_by_runtime(self, runtime: str) -> list[AgentRecord]:
        """Filter agents by runtime."""
        return [r for r in self._records.values() if r.runtime == runtime]

    def update(self, agent_id: str, *, expected_version: str | None = None, **kwargs: Any) -> AgentRecord:
        """Update an agent record.

        ``expected_version`` enables compare-and-set on ``current_version``;
        a mismatch raises :class:`ConcurrentUpdateError` (mirroring the
        PostgreSQL repository's behavior).
        """
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError(f"Agent not found: {agent_id}")
        if expected_version is not None and record.current_version != expected_version:
            raise ConcurrentUpdateError(agent_id, expected_version)
        for key, value in kwargs.items():
            if hasattr(record, key):
                setattr(record, key, value)
        record.updated_at = datetime.now(UTC)
        return record

    def disable(self, agent_id: str) -> AgentRecord:
        """Disable an agent."""
        return self.transition(agent_id, AgentRecordStatus.DISABLED)

    def transition(self, agent_id: str, new_status: AgentRecordStatus) -> AgentRecord:
        """Apply a lifecycle transition, rejecting invalid moves.

        Allowed: draft -> active/archived, active -> disabled/archived,
        disabled -> active/archived. Archived is terminal.
        """
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError(f"Agent not found: {agent_id}")
        if new_status == record.status:
            return record
        if new_status not in _VALID_TRANSITIONS[record.status]:
            raise InvalidTransitionError(record.status, new_status)
        return self.update(agent_id, status=new_status)

    def delete(self, agent_id: str) -> bool:
        """Delete an agent record. Returns True if the record existed."""
        return self._records.pop(agent_id, None) is not None

    def add_version(self, agent_id: str, version: AgentVersion) -> AgentVersion:
        """Add a new version to an agent record.

        The definition stored on the version is an immutable snapshot (deep
        copy) of the agent's current definition at creation time; later record
        updates never mutate it. Version identifiers must be unique per agent.
        """
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError(f"Agent not found: {agent_id}")
        if any(existing.version == version.version for existing in record.versions):
            raise DuplicateVersionError(record.name, version.version)
        if version.definition is None:
            version.definition = record.definition.model_copy(deep=True) if record.definition else None
        else:
            version.definition = version.definition.model_copy(deep=True)
        record.versions.append(version)
        record.current_version = version.version
        if version.definition:
            record.definition = version.definition
        record.updated_at = datetime.now(UTC)
        return version

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._records
