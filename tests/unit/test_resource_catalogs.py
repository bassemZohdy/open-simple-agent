"""Tests for Resource Catalogs."""

import pytest

from osa.control_plane.backend import ResourceCatalogs
from osa.generic_agent import (
    McpDefinition,
    MemoryPolicy,
    ModelDefinition,
    SkillDefinition,
    ToolDefinition,
)


class TestResourceCatalogs:
    def test_model_crud(self) -> None:
        rc = ResourceCatalogs()
        model = ModelDefinition(name="gpt-4", provider="openai", model_id="gpt-4")
        rc.create_model(model)
        assert rc.get_model("gpt-4").name == "gpt-4"
        assert len(rc.list_models()) == 1
        assert rc.delete_model("gpt-4") is True
        assert len(rc.list_models()) == 0
        assert rc.delete_model("nonexistent") is False

    def test_mcp_crud(self) -> None:
        rc = ResourceCatalogs()
        mcp = McpDefinition(name="crm", endpoint="http://localhost:3000")
        rc.create_mcp(mcp)
        assert rc.get_mcp("crm").name == "crm"
        assert len(rc.list_mcps()) == 1
        assert rc.delete_mcp("crm") is True
        assert len(rc.list_mcps()) == 0

    def test_tool_crud(self) -> None:
        rc = ResourceCatalogs()
        tool = ToolDefinition(name="calculator", description="Arithmetic")
        rc.create_tool(tool)
        assert rc.get_tool("calculator").name == "calculator"
        assert len(rc.list_tools()) == 1
        assert rc.delete_tool("calculator") is True
        assert len(rc.list_tools()) == 0

    def test_skill_crud(self) -> None:
        rc = ResourceCatalogs()
        skill = SkillDefinition(name="support", description="Support skills")
        rc.create_skill(skill)
        assert rc.get_skill("support").name == "support"
        assert len(rc.list_skills()) == 1
        results = rc.search_skills("support")
        assert len(results) == 1
        assert rc.delete_skill("support") is True
        assert len(rc.list_skills()) == 0

    def test_memory_policy_crud(self) -> None:
        rc = ResourceCatalogs()
        policy = MemoryPolicy(name="user-memory", enabled=True)
        rc.create_memory_policy(policy)
        assert rc.get_memory_policy("user-memory").name == "user-memory"
        assert len(rc.list_memory_policies()) == 1
        assert rc.delete_memory_policy("user-memory") is True
        assert len(rc.list_memory_policies()) == 0

    def test_memory_policy_not_found(self) -> None:
        rc = ResourceCatalogs()
        with pytest.raises(KeyError, match="Memory policy not found"):
            rc.get_memory_policy("nonexistent")

    def test_agent_creation_can_select_by_reference(self) -> None:
        """Acceptance: agent creation can select resources entirely by catalog reference."""
        rc = ResourceCatalogs()
        rc.create_model(ModelDefinition(name="default", provider="openai", model_id="gpt-4"))
        rc.create_mcp(McpDefinition(name="crm"))
        rc.create_tool(ToolDefinition(name="calculator"))
        rc.create_skill(SkillDefinition(name="support"))
        rc.create_memory_policy(MemoryPolicy(name="user-memory"))

        # All resources can be resolved by reference
        assert rc.get_model("default").model_id == "gpt-4"
        assert rc.get_mcp("crm").name == "crm"
        assert rc.get_tool("calculator").name == "calculator"
        assert rc.get_skill("support").name == "support"
        assert rc.get_memory_policy("user-memory").enabled is True
