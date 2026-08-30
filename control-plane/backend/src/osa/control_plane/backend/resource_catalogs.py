"""Resource Catalogs — unified CRUD for Control Plane resources.

Wraps the individual domain catalogs (Model, MCP, Tool, Skill, Memory Policy)
with validation and CRUD operations for the Control Plane API.
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

    def create_model(self, model: ModelDefinition) -> ModelDefinition:
        self.models.register(model)
        return model

    def get_model(self, name: str) -> ModelDefinition:
        return self.models.resolve(name)

    def list_models(self) -> list[ModelDefinition]:
        return self.models.list_models()

    def delete_model(self, name: str) -> bool:
        if name in self.models:
            del self.models._models[name]
            return True
        return False

    # --- MCP Catalog ---

    def create_mcp(self, mcp: McpDefinition) -> McpDefinition:
        self.mcps.register(mcp)
        return mcp

    def get_mcp(self, name: str) -> McpDefinition:
        return self.mcps.resolve(name)

    def list_mcps(self) -> list[McpDefinition]:
        return self.mcps.list_definitions()

    def delete_mcp(self, name: str) -> bool:
        if name in self.mcps:
            del self.mcps._definitions[name]
            return True
        return False

    # --- Tool Catalog ---

    def create_tool(self, tool: ToolDefinition) -> ToolDefinition:
        self.tools.register_definition(tool)
        return tool

    def get_tool(self, name: str) -> ToolDefinition:
        return self.tools.get_definition(name)

    def list_tools(self) -> list[ToolDefinition]:
        return self.tools.list_definitions()

    def delete_tool(self, name: str) -> bool:
        if name in self.tools:
            del self.tools._definitions[name]
            return True
        return False

    # --- Skill Catalog ---

    def create_skill(self, skill: SkillDefinition) -> SkillDefinition:
        self.skills.register(skill)
        return skill

    def get_skill(self, name: str) -> SkillDefinition:
        return self.skills.resolve(name)

    def list_skills(self) -> list[SkillDefinition]:
        return self.skills.list_definitions()

    def search_skills(self, query: str) -> list[SkillDefinition]:
        return self.skills.search(query)

    def delete_skill(self, name: str) -> bool:
        if name in self.skills:
            del self.skills._definitions[name]
            return True
        return False

    # --- Memory Policies ---

    def create_memory_policy(self, policy: MemoryPolicy) -> MemoryPolicy:
        self._memory_policies[policy.name] = policy
        return policy

    def get_memory_policy(self, name: str) -> MemoryPolicy:
        if name not in self._memory_policies:
            raise KeyError(f"Memory policy not found: {name}")
        return self._memory_policies[name]

    def list_memory_policies(self) -> list[MemoryPolicy]:
        return list(self._memory_policies.values())

    def delete_memory_policy(self, name: str) -> bool:
        return self._memory_policies.pop(name, None) is not None
