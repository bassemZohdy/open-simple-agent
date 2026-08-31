"""Skill domain types and catalog."""

from __future__ import annotations

from pydantic import Field

from osa.generic_agent.config import StrictModel


class SkillDefinition(StrictModel):
    """Definition of a skill in the Skill Catalog.

    A Skill describes what an agent can accomplish.
    It is a semantic capability, not necessarily an executable component.
    """

    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str = ""
    input_metadata: dict[str, str] = Field(default_factory=dict)
    output_metadata: dict[str, str] = Field(default_factory=dict)
    policy_metadata: dict[str, str] = Field(default_factory=dict)


class SkillCatalog:
    """In-memory catalog of skill definitions."""

    def __init__(self) -> None:
        self._definitions: dict[str, SkillDefinition] = {}

    def register(self, definition: SkillDefinition) -> None:
        self._definitions[definition.name] = definition

    def resolve(self, ref: str) -> SkillDefinition:
        if ref not in self._definitions:
            raise KeyError(f"Skill not found: {ref}")
        return self._definitions[ref]

    def delete(self, name: str) -> bool:
        """Remove a skill definition. Returns True if it existed."""
        return self._definitions.pop(name, None) is not None

    def search(self, query: str) -> list[SkillDefinition]:
        """Search skills by name, description, or tags."""
        query_lower = query.lower()
        results = []
        for skill in self._definitions.values():
            if (
                query_lower in skill.name.lower()
                or query_lower in skill.description.lower()
                or any(query_lower in tag.lower() for tag in skill.tags)
            ):
                results.append(skill)
        return results

    def list_definitions(self) -> list[SkillDefinition]:
        return list(self._definitions.values())

    def __len__(self) -> int:
        return len(self._definitions)

    def __contains__(self, ref: str) -> bool:
        return ref in self._definitions
