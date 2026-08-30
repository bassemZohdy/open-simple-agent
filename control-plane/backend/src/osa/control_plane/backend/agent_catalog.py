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
            raise ValueError(f"Agent with name '{record.name}' already exists")
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

    def update(self, agent_id: str, **kwargs: Any) -> AgentRecord:
        """Update an agent record."""
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError(f"Agent not found: {agent_id}")
        for key, value in kwargs.items():
            if hasattr(record, key):
                setattr(record, key, value)
        record.updated_at = datetime.now(UTC)
        return record

    def disable(self, agent_id: str) -> AgentRecord:
        """Disable an agent."""
        return self.update(agent_id, status=AgentRecordStatus.DISABLED)

    def archive(self, agent_id: str) -> AgentRecord:
        """Archive an agent."""
        return self.update(agent_id, status=AgentRecordStatus.ARCHIVED)

    def delete(self, agent_id: str) -> bool:
        """Delete an agent record. Returns True if the record existed."""
        return self._records.pop(agent_id, None) is not None

    def add_version(self, agent_id: str, version: AgentVersion) -> AgentVersion:
        """Add a new version to an agent record."""
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError(f"Agent not found: {agent_id}")
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
