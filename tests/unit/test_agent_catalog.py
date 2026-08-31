"""Tests for the Agent Catalog."""

from typing import Any

import pytest

from osa.control_plane.backend import (
    AgentCatalog,
    AgentRecord,
    AgentRecordStatus,
    AgentVersion,
)
from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentSpec,
)


def _make_definition(name: str = "test-agent") -> AgentDefinition:
    return AgentDefinition(
        metadata=AgentMetadataConfig(name=name),
        spec=AgentSpec(description=f"The {name} agent"),
    )


def _make_record(name: str = "test-agent", **kwargs: Any) -> AgentRecord:
    return AgentRecord(
        name=name,
        description=f"The {name} agent",
        definition=_make_definition(name),
        **kwargs,
    )


class TestAgentRecord:
    def test_create(self) -> None:
        record = _make_record()
        assert record.name == "test-agent"
        assert record.status == AgentRecordStatus.DRAFT
        assert record.runtime == "adk"

    def test_defaults(self) -> None:
        record = AgentRecord()
        assert record.status == AgentRecordStatus.DRAFT
        assert record.skills == []
        assert record.labels == {}


class TestAgentVersion:
    def test_create(self) -> None:
        version = AgentVersion(version="1.0.0")
        assert version.version == "1.0.0"
        assert version.definition is None


class TestAgentCatalog:
    def test_create_and_get(self) -> None:
        catalog = AgentCatalog()
        record = _make_record("support")
        catalog.create(record)
        retrieved = catalog.get(record.agent_id)
        assert retrieved is not None
        assert retrieved.name == "support"

    def test_create_duplicate_name_raises(self) -> None:
        from osa.control_plane.backend import DuplicateAgentError

        catalog = AgentCatalog()
        catalog.create(_make_record("support"))
        with pytest.raises(DuplicateAgentError, match="already exists"):
            catalog.create(_make_record("support"))

    def test_get_by_name(self) -> None:
        catalog = AgentCatalog()
        record = _make_record("billing")
        catalog.create(record)
        retrieved = catalog.get_by_name("billing")
        assert retrieved is not None
        assert retrieved.agent_id == record.agent_id

    def test_get_by_name_not_found(self) -> None:
        catalog = AgentCatalog()
        assert catalog.get_by_name("nonexistent") is None

    def test_list_all(self) -> None:
        catalog = AgentCatalog()
        catalog.create(_make_record("a"))
        catalog.create(_make_record("b"))
        assert len(catalog.list_all()) == 2

    def test_search(self) -> None:
        catalog = AgentCatalog()
        catalog.create(_make_record("support-agent"))
        catalog.create(_make_record("billing-agent"))
        results = catalog.search("support")
        assert len(results) == 1
        assert results[0].name == "support-agent"

    def test_filter_by_status(self) -> None:
        catalog = AgentCatalog()
        catalog.create(_make_record("a", status=AgentRecordStatus.ACTIVE))
        catalog.create(_make_record("b", status=AgentRecordStatus.DRAFT))
        results = catalog.filter_by_status(AgentRecordStatus.ACTIVE)
        assert len(results) == 1

    def test_filter_by_skill(self) -> None:
        catalog = AgentCatalog()
        catalog.create(_make_record("a", skills=["support", "billing"]))
        catalog.create(_make_record("b", skills=["research"]))
        results = catalog.filter_by_skill("support")
        assert len(results) == 1

    def test_filter_by_runtime(self) -> None:
        catalog = AgentCatalog()
        catalog.create(_make_record("a", runtime="adk"))
        catalog.create(_make_record("b", runtime="langchain"))
        results = catalog.filter_by_runtime("adk")
        assert len(results) == 1

    def test_update(self) -> None:
        catalog = AgentCatalog()
        record = _make_record("test")
        catalog.create(record)
        updated = catalog.update(record.agent_id, description="Updated description")
        assert updated.description == "Updated description"

    def test_update_not_found(self) -> None:
        catalog = AgentCatalog()
        with pytest.raises(KeyError, match="Agent not found"):
            catalog.update("nonexistent", description="x")

    def test_disable_requires_activation_first(self) -> None:
        from osa.control_plane.backend import InvalidTransitionError

        catalog = AgentCatalog()
        record = _make_record("test")
        catalog.create(record)
        with pytest.raises(InvalidTransitionError):
            catalog.disable(record.agent_id)
        catalog.transition(record.agent_id, AgentRecordStatus.ACTIVE)
        disabled = catalog.disable(record.agent_id)
        assert disabled.status == AgentRecordStatus.DISABLED

    def test_archive(self) -> None:
        catalog = AgentCatalog()
        record = _make_record("test")
        catalog.create(record)
        archived = catalog.transition(record.agent_id, AgentRecordStatus.ARCHIVED)
        assert archived.status == AgentRecordStatus.ARCHIVED

    def test_transition_rejects_unknown_agent(self) -> None:

        catalog = AgentCatalog()
        with pytest.raises(KeyError, match="Agent not found"):
            catalog.transition("nope", AgentRecordStatus.ARCHIVED)

    def test_invalid_transition_error_message(self) -> None:
        from osa.control_plane.backend import InvalidTransitionError

        error = InvalidTransitionError(AgentRecordStatus.DRAFT, AgentRecordStatus.DISABLED)
        assert "draft" in str(error) and "disabled" in str(error)

    def test_delete(self) -> None:
        catalog = AgentCatalog()
        record = _make_record("test")
        catalog.create(record)
        assert catalog.delete(record.agent_id) is True
        assert catalog.get(record.agent_id) is None
        assert catalog.delete("nonexistent") is False

    def test_add_version(self) -> None:
        catalog = AgentCatalog()
        record = _make_record("test")
        catalog.create(record)

        new_def = _make_definition("test-v2")
        version = AgentVersion(version="2.0.0", definition=new_def)
        catalog.add_version(record.agent_id, version)

        updated = catalog.get(record.agent_id)
        assert updated is not None
        assert updated.current_version == "2.0.0"
        assert len(updated.versions) == 1

    def test_add_version_not_found(self) -> None:
        catalog = AgentCatalog()
        with pytest.raises(KeyError, match="Agent not found"):
            catalog.add_version("nonexistent", AgentVersion())

    def test_contains(self) -> None:
        catalog = AgentCatalog()
        record = _make_record("test")
        catalog.create(record)
        assert record.agent_id in catalog
        assert "nonexistent" not in catalog

    def test_len(self) -> None:
        catalog = AgentCatalog()
        assert len(catalog) == 0
        catalog.create(_make_record("a"))
        assert len(catalog) == 1

    def test_catalog_does_not_store_runtime_objects(self) -> None:
        """Acceptance: catalog stores definitions, not runtime Agent objects."""
        catalog = AgentCatalog()
        record = _make_record("test")
        catalog.create(record)
        retrieved = catalog.get(record.agent_id)
        assert retrieved is not None
        assert isinstance(retrieved.definition, AgentDefinition)
        # The catalog should NOT have any runtime Agent objects
        assert not hasattr(retrieved, "_runtime_agent")
