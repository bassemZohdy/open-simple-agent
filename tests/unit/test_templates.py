"""Tests for Agent Templates."""

import pytest

from osa.control_plane.backend import (
    GENERIC_TEMPLATE,
    RESEARCH_TEMPLATE,
    SUPPORT_TEMPLATE,
    AgentTemplate,
    TemplateCatalog,
    create_default_template_catalog,
)


class TestAgentTemplate:
    def test_create_definition_with_defaults(self) -> None:
        template = AgentTemplate(
            name="test",
            description="Test template",
            default_instruction="Do something.",
            default_model_ref="default",
        )
        definition = template.create_definition(name="my-agent")
        assert definition.metadata.name == "my-agent"
        assert definition.spec.instruction == "Do something."
        assert definition.spec.model is not None
        assert definition.spec.model.ref == "default"

    def test_create_definition_with_overrides(self) -> None:
        template = AgentTemplate(
            name="test",
            default_instruction="Default instruction",
            default_model_ref="default",
        )
        definition = template.create_definition(
            name="my-agent",
            instruction="Custom instruction",
            model_ref="gpt-4",
        )
        assert definition.spec.instruction == "Custom instruction"
        assert definition.spec.model is not None
        assert definition.spec.model.ref == "gpt-4"

    def test_create_definition_with_mcps(self) -> None:
        template = AgentTemplate(
            name="test",
            default_mcps=["crm", "payments"],
        )
        definition = template.create_definition(name="my-agent")
        assert len(definition.spec.mcps) == 2
        assert definition.spec.mcps[0].ref == "crm"

    def test_create_definition_with_skills(self) -> None:
        template = AgentTemplate(
            name="test",
            default_skills=["support"],
        )
        definition = template.create_definition(name="my-agent")
        assert len(definition.spec.skills) == 1
        assert definition.spec.skills[0].ref == "support"

    def test_create_definition_with_memory(self) -> None:
        template = AgentTemplate(
            name="test",
            default_memory_enabled=True,
            default_memory_policy="user-memory",
        )
        definition = template.create_definition(name="my-agent")
        assert definition.spec.memory.enabled is True
        assert definition.spec.memory.policy == "user-memory"

    def test_create_definition_with_labels(self) -> None:
        template = AgentTemplate(
            name="test",
            labels={"env": "prod"},
        )
        definition = template.create_definition(
            name="my-agent",
            extra_labels={"team": "support"},
        )
        assert definition.metadata.labels["env"] == "prod"
        assert definition.metadata.labels["team"] == "support"

    def test_definition_is_independent_of_template(self) -> None:
        """Acceptance: updating template does not modify existing agents."""
        template = AgentTemplate(
            name="test",
            default_instruction="Original instruction",
        )
        definition = template.create_definition(name="my-agent")
        template.default_instruction = "Changed instruction"
        assert definition.spec.instruction == "Original instruction"


class TestBuiltInTemplates:
    def test_generic_template(self) -> None:
        definition = GENERIC_TEMPLATE.create_definition(name="my-generic")
        assert definition.spec.instruction == "Assist the user with their requests."

    def test_support_template(self) -> None:
        definition = SUPPORT_TEMPLATE.create_definition(name="my-support")
        assert definition.spec.memory.enabled is True
        assert len(definition.spec.skills) == 2

    def test_research_template(self) -> None:
        definition = RESEARCH_TEMPLATE.create_definition(name="my-research")
        assert "research" in [s.ref for s in definition.spec.skills]


class TestTemplateCatalog:
    def test_register_and_get(self) -> None:
        catalog = TemplateCatalog()
        template = AgentTemplate(name="test")
        catalog.register(template)
        assert catalog.get("test").name == "test"

    def test_get_not_found(self) -> None:
        catalog = TemplateCatalog()
        with pytest.raises(KeyError, match="Template not found"):
            catalog.get("nonexistent")

    def test_list_templates(self) -> None:
        catalog = TemplateCatalog()
        catalog.register(AgentTemplate(name="a"))
        catalog.register(AgentTemplate(name="b"))
        assert len(catalog.list_templates()) == 2

    def test_contains(self) -> None:
        catalog = TemplateCatalog()
        catalog.register(AgentTemplate(name="x"))
        assert "x" in catalog
        assert "y" not in catalog

    def test_create_default_catalog(self) -> None:
        catalog = create_default_template_catalog()
        assert len(catalog) == 3
        assert "generic" in catalog
        assert "support" in catalog
        assert "research" in catalog
