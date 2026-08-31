"""Resource Catalogs — unified CRUD for Control Plane resources.

Wraps the individual domain catalogs (Model, MCP, Tool, Skill, Memory Policy)
with explicit contracts for the Control Plane API: create-or-replace
registration, lookup, listing, and deletion — no direct mutation of the
domain catalogs' internal dictionaries.
"""

from __future__ import annotations

from osa.generic_agent import (
    McpCatalog,
    McpDefinition,
    MemoryPolicy,
    ModelCatalog,
    ModelDefinition,
    SkillCatalog,
    SkillDefinition,
    ToolCatalog,
    ToolDefinition,
)


class ResourceCatalogs:
    """Unified access to all resource catalogs.

    Provides CRUD operations for models, MCPs, tools, skills,
    and memory policies used by the Control Plane.
    """

    def __init__(self) -> None:
        self.models = ModelCatalog()
        self.mcps = McpCatalog()
        self.tools = ToolCatalog()
        self.skills = SkillCatalog()
        self._memory_policies: dict[str, MemoryPolicy] = {}

    # --- Model Catalog ---

    def register_model(self, model: ModelDefinition) -> None:
        """Create or replace a model definition."""
        self.models.register(model)

    def has_model(self, name: str) -> bool:
        return name in self.models

    def get_model(self, name: str) -> ModelDefinition:
        return self.models.resolve(name)

    def list_models(self) -> list[ModelDefinition]:
        return self.models.list_models()

    def delete_model(self, name: str) -> bool:
        """Delete a model definition. Returns True if it existed."""
        return self.models.delete(name)

    # --- MCP Catalog ---

    def register_mcp(self, mcp: McpDefinition) -> None:
        """Create or replace an MCP server definition."""
        self.mcps.register(mcp)

    def has_mcp(self, name: str) -> bool:
        return name in self.mcps

    def get_mcp(self, name: str) -> McpDefinition:
        return self.mcps.resolve(name)

    def list_mcps(self) -> list[McpDefinition]:
        return self.mcps.list_definitions()

    def delete_mcp(self, name: str) -> bool:
        return self.mcps.delete(name)

    # --- Tool Catalog ---

    def register_tool(self, tool: ToolDefinition) -> None:
        """Create or replace a tool definition."""
        self.tools.register_definition(tool)

    def has_tool(self, name: str) -> bool:
        return name in self.tools

    def get_tool(self, name: str) -> ToolDefinition:
        return self.tools.get_definition(name)

    def list_tools(self) -> list[ToolDefinition]:
        return self.tools.list_definitions()

    def delete_tool(self, name: str) -> bool:
        return self.tools.delete(name)

    # --- Skill Catalog ---

    def register_skill(self, skill: SkillDefinition) -> None:
        """Create or replace a skill definition."""
        self.skills.register(skill)

    def has_skill(self, name: str) -> bool:
        return name in self.skills

    def get_skill(self, name: str) -> SkillDefinition:
        return self.skills.resolve(name)

    def list_skills(self) -> list[SkillDefinition]:
        return self.skills.list_definitions()

    def delete_skill(self, name: str) -> bool:
        return self.skills.delete(name)

    def search_skills(self, query: str) -> list[SkillDefinition]:
        return self.skills.search(query)

    # --- Memory Policies ---

    def register_memory_policy(self, policy: MemoryPolicy) -> None:
        """Create or replace a memory policy."""
        self._memory_policies[policy.name] = policy

    def has_memory_policy(self, name: str) -> bool:
        return name in self._memory_policies

    def get_memory_policy(self, name: str) -> MemoryPolicy:
        if name not in self._memory_policies:
            raise KeyError(f"Memory policy not found: {name}")
        return self._memory_policies[name]

    def list_memory_policies(self) -> list[MemoryPolicy]:
        return list(self._memory_policies.values())

    def delete_memory_policy(self, name: str) -> bool:
        return self._memory_policies.pop(name, None) is not None
