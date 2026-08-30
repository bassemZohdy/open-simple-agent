"""Agent Templates — reusable starting points for creating agents."""

from __future__ import annotations

from dataclasses import dataclass, field

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentSpec,
    McpRef,
    MemoryConfig,
    ModelRef,
    SkillRef,
    ToolRef,
)


@dataclass
class AgentTemplate:
    """A template for creating agent definitions.

    Templates provide default values that can be overridden when
    creating a new agent. The resulting AgentDefinition is independent
    of the template — updating the template does not affect existing agents.
    """

    name: str
    description: str = ""
    default_instruction: str = ""
    default_model_ref: str | None = None
    default_mcps: list[str] = field(default_factory=list)
    default_tools: list[str] = field(default_factory=list)
    default_skills: list[str] = field(default_factory=list)
    default_memory_enabled: bool = False
    default_memory_policy: str | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def create_definition(
        self,
        name: str,
        version: str = "1.0.0",
        instruction: str | None = None,
        model_ref: str | None = None,
        mcps: list[str] | None = None,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        memory_enabled: bool | None = None,
        memory_policy: str | None = None,
        extra_labels: dict[str, str] | None = None,
    ) -> AgentDefinition:
        """Create an AgentDefinition from this template.

        User overrides take precedence over template defaults.
        The resulting definition is independent of the template.
        """
        final_instruction = instruction if instruction is not None else self.default_instruction
        final_model_ref = model_ref if model_ref is not None else self.default_model_ref
        final_mcps = mcps if mcps is not None else self.default_mcps
        final_tools = tools if tools is not None else self.default_tools
        final_skills = skills if skills is not None else self.default_skills
        final_memory_enabled = memory_enabled if memory_enabled is not None else self.default_memory_enabled
        final_memory_policy = memory_policy if memory_policy is not None else self.default_memory_policy

        labels = {**self.labels}
        if extra_labels:
            labels.update(extra_labels)

        return AgentDefinition(
            metadata=AgentMetadataConfig(
                name=name,
                version=version,
                description=self.description,
                labels=labels,
            ),
            spec=AgentSpec(
                instruction=final_instruction,
                model=ModelRef(ref=final_model_ref) if final_model_ref else None,
                mcps=[McpRef(ref=m) for m in final_mcps],
                tools=[ToolRef(ref=t) for t in final_tools],
                skills=[SkillRef(ref=s) for s in final_skills],
                memory=MemoryConfig(enabled=final_memory_enabled, policy=final_memory_policy),
            ),
        )


class TemplateCatalog:
    """In-memory catalog of agent templates."""

    def __init__(self) -> None:
        self._templates: dict[str, AgentTemplate] = {}

    def register(self, template: AgentTemplate) -> None:
        self._templates[template.name] = template

    def get(self, name: str) -> AgentTemplate:
        if name not in self._templates:
            raise KeyError(f"Template not found: {name}")
        return self._templates[name]

    def list_templates(self) -> list[AgentTemplate]:
        return list(self._templates.values())

    def __len__(self) -> int:
        return len(self._templates)

    def __contains__(self, name: str) -> bool:
        return name in self._templates


# Built-in templates
GENERIC_TEMPLATE = AgentTemplate(
    name="generic",
    description="A generic agent template.",
    default_instruction="Assist the user with their requests.",
)

SUPPORT_TEMPLATE = AgentTemplate(
    name="support",
    description="Customer support assistant.",
    default_instruction="Help customers resolve support requests. Be helpful and professional.",
    default_skills=["support", "case-resolution"],
    default_memory_enabled=True,
    default_memory_policy="user-memory",
)

RESEARCH_TEMPLATE = AgentTemplate(
    name="research",
    description="Research assistant.",
    default_instruction="Help with research tasks. Provide accurate, well-sourced information.",
    default_skills=["research", "analysis"],
)


def create_default_template_catalog() -> TemplateCatalog:
    """Create a catalog with all built-in templates."""
    catalog = TemplateCatalog()
    catalog.register(GENERIC_TEMPLATE)
    catalog.register(SUPPORT_TEMPLATE)
    catalog.register(RESEARCH_TEMPLATE)
    return catalog
