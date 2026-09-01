"""Deployment bundles: an agent definition plus its referenced catalog resources.

A bundle is the externally configured unit the runtime loads at startup. It
may be a single ``AgentBundle`` YAML document or a directory::

    my-bundle/
    ├── agent.yaml          # standard AgentDefinition document
    ├── bundle.yaml         # optional bundle metadata (name, version, labels)
    ├── models/*.yaml       # resource envelopes: {apiVersion, kind, spec}
    ├── tools/*.yaml
    ├── skills/*.yaml
    ├── mcps/*.yaml
    └── memory-policies/*.yaml

Loading is fail-fast: unknown resource kinds, unsupported apiVersions,
duplicate resource names, and agent references to missing resources all raise
deterministic :class:`BundleError` subclasses before the bundle is usable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence


import yaml
from pydantic import Field, ValidationError, model_validator

from osa.generic_agent.config import AgentDefinition, SecretReference, StrictModel, load_agent_definition
from osa.generic_agent.credentials import credential_secret_references
from osa.generic_agent.mcp import McpCatalog, McpDefinition
from osa.generic_agent.memory import MemoryPolicy, MemoryPolicyCatalog
from osa.generic_agent.model import ModelCatalog, ModelDefinition
from osa.generic_agent.skill import SkillCatalog, SkillDefinition
from osa.generic_agent.tool import ToolCatalog, ToolDefinition


class NamedResource(Protocol):
    """Structural view of bundle resources that carry a unique name."""

    name: str


API_VERSION = "osa/v1alpha1"
BUNDLE_KIND = "AgentBundle"

# Directory names for file-layout bundles, mapped to their resource kind.
_KIND_DIRECTORY_NAMES: dict[str, str] = {
    "Model": "models",
    "Tool": "tools",
    "Skill": "skills",
    "Mcp": "mcps",
    "MemoryPolicy": "memory-policies",
}

# Resource kinds accepted in bundle documents, mapped to their domain model.
_KIND_TO_MODEL: dict[str, type[StrictModel]] = {
    "Model": ModelDefinition,
    "Tool": ToolDefinition,
    "Skill": SkillDefinition,
    "Mcp": McpDefinition,
    "MemoryPolicy": MemoryPolicy,
}

# Field names on DeploymentBundle for each resource kind.
_KIND_TO_FIELD: dict[str, str] = {
    "Model": "models",
    "Tool": "tools",
    "Skill": "skills",
    "Mcp": "mcps",
    "MemoryPolicy": "memory_policies",
}


class BundleError(Exception):
    """Base error for deployment bundle loading and validation failures."""


class InvalidBundleError(BundleError):
    """A bundle document or file layout could not be interpreted."""


class DuplicateResourceError(BundleError):
    """Two resources of the same kind in a bundle share a name."""

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(f"Duplicate {kind} resource name '{name}' in bundle")


class UnknownReferenceError(BundleError):
    """An agent definition references a resource absent from the bundle."""

    def __init__(self, kind: str, ref: str, agent_name: str) -> None:
        self.kind = kind
        self.ref = ref
        self.agent_name = agent_name
        super().__init__(
            f"Agent '{agent_name}' references unknown {kind.lower()} '{ref}': "
            f"the bundle does not contain a matching {kind} resource"
        )


class BundleMetadata(StrictModel):
    """Metadata section of a deployment bundle."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class DeploymentBundle(StrictModel):
    """A versioned deployment bundle: one agent plus its catalog resources."""

    api_version: str = Field(default=API_VERSION, alias="apiVersion")
    kind: str = BUNDLE_KIND
    metadata: BundleMetadata
    agent: AgentDefinition
    models: list[ModelDefinition] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    skills: list[SkillDefinition] = Field(default_factory=list)
    mcps: list[McpDefinition] = Field(default_factory=list)
    memory_policies: list[MemoryPolicy] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_supported_document(self) -> DeploymentBundle:
        if self.api_version != API_VERSION:
            raise ValueError(f"Unsupported apiVersion '{self.api_version}'; expected '{API_VERSION}'")
        if self.kind != BUNDLE_KIND:
            raise ValueError(f"Unsupported kind '{self.kind}'; expected '{BUNDLE_KIND}'")
        return self


@dataclass
class BundleCatalogs:
    """Catalogs built from a bundle, ready for runtime construction."""

    model_catalog: ModelCatalog = field(default_factory=ModelCatalog)
    tool_catalog: ToolCatalog = field(default_factory=ToolCatalog)
    skill_catalog: SkillCatalog = field(default_factory=SkillCatalog)
    mcp_catalog: McpCatalog = field(default_factory=McpCatalog)
    memory_policies: MemoryPolicyCatalog = field(default_factory=MemoryPolicyCatalog)


def load_bundle(source: str | Path) -> DeploymentBundle:
    """Load a deployment bundle from a YAML file or a directory.

    Raises:
        FileNotFoundError: If the source path does not exist.
        InvalidBundleError: If the document or layout cannot be interpreted.
        pydantic.ValidationError: If a resource definition violates its schema.
    """
    path = Path(source)
    if path.is_dir():
        return _load_bundle_directory(path)
    if path.is_file():
        return _load_bundle_file(path)
    raise FileNotFoundError(f"Deployment bundle not found: {path}")


def build_catalogs(bundle: DeploymentBundle) -> BundleCatalogs:
    """Register bundle resources into catalogs, then validate agent references.

    Raises:
        DuplicateResourceError: If two resources of the same kind share a name.
        UnknownReferenceError: If the agent references a missing resource.
    """
    catalogs = BundleCatalogs()

    def register_all(kind: str, items: Sequence[NamedResource], register: Any) -> None:
        seen: set[str] = set()
        for item in items:
            name = item.name
            if name in seen:
                raise DuplicateResourceError(kind, name)
            seen.add(name)
            register(item)

    register_all("Model", bundle.models, catalogs.model_catalog.register)
    register_all("Tool", bundle.tools, catalogs.tool_catalog.register_definition)
    register_all("Skill", bundle.skills, catalogs.skill_catalog.register)
    register_all("Mcp", bundle.mcps, catalogs.mcp_catalog.register)
    for policy in bundle.memory_policies:
        if policy.name in catalogs.memory_policies:
            raise DuplicateResourceError("MemoryPolicy", policy.name)
        catalogs.memory_policies.register(policy)

    _validate_references(bundle, catalogs)
    return catalogs


def collect_secret_references(bundle: DeploymentBundle) -> list[SecretReference]:
    """Every secret reference the bundle needs at startup.

    Callers may resolve these (discarding values) to fail fast before service
    readiness; values must never be stored, logged, or surfaced in errors.
    """
    references: list[SecretReference] = []
    for model in bundle.models:
        if model.credential_ref is not None:
            references.append(model.credential_ref)
    for mcp in bundle.mcps:
        if mcp.credential_ref is not None:
            references.append(mcp.credential_ref)
        if mcp.credential is not None:
            references.extend(credential_secret_references(mcp.credential))
    return references


def _validate_references(bundle: DeploymentBundle, catalogs: BundleCatalogs) -> None:
    agent = bundle.agent
    agent_name = agent.metadata.name
    spec = agent.spec

    if spec.model is not None:
        try:
            catalogs.model_catalog.resolve(spec.model.ref)
        except KeyError:
            raise UnknownReferenceError("Model", spec.model.ref, agent_name) from None

    for tool_ref in spec.tools:
        if tool_ref.ref not in catalogs.tool_catalog:
            raise UnknownReferenceError("Tool", tool_ref.ref, agent_name)
    for skill_ref in spec.skills:
        try:
            catalogs.skill_catalog.resolve(skill_ref.ref)
        except KeyError:
            raise UnknownReferenceError("Skill", skill_ref.ref, agent_name) from None
    for mcp_ref in spec.mcps:
        try:
            catalogs.mcp_catalog.resolve(mcp_ref.ref)
        except KeyError:
            raise UnknownReferenceError("Mcp", mcp_ref.ref, agent_name) from None

    memory = spec.memory
    if memory.enabled and memory.policy is not None and memory.policy not in catalogs.memory_policies:
        raise UnknownReferenceError("MemoryPolicy", memory.policy, agent_name)


def _load_bundle_file(path: Path) -> DeploymentBundle:
    data = yaml.safe_load(path.read_text())
    return _validate_bundle_document(data, origin=str(path))


def _load_bundle_directory(path: Path) -> DeploymentBundle:
    agent_file = _first_existing(path, ("agent.yaml", "agent.yml"))
    if agent_file is None:
        raise InvalidBundleError(f"Bundle directory '{path}' must contain an agent.yaml AgentDefinition document")
    agent = load_agent_definition(agent_file)

    resources: dict[str, list[StrictModel]] = {kind: [] for kind in _KIND_TO_MODEL}
    for kind, directory_name in _KIND_DIRECTORY_NAMES.items():
        directory = path / directory_name
        if not directory.is_dir():
            continue
        for resource_file in sorted((*directory.glob("*.yaml"), *directory.glob("*.yml"))):
            document = yaml.safe_load(resource_file.read_text())
            document_kind, parsed = _parse_resource_document(document, origin=str(resource_file))
            if document_kind != kind:
                raise InvalidBundleError(
                    f"{resource_file}: expected kind '{kind}' in directory '{directory_name}', "
                    f"found kind '{document_kind}'"
                )
            resources[document_kind].append(parsed)

    metadata_document = _first_existing(path, ("bundle.yaml", "bundle.yml"))
    metadata = _load_bundle_metadata(metadata_document, fallback_name=path.name)

    fields: dict[str, Any] = {_KIND_TO_FIELD[kind]: items for kind, items in resources.items()}
    return DeploymentBundle.model_validate({"metadata": metadata, "agent": agent, **fields})


def _load_bundle_metadata(path: Path | None, *, fallback_name: str) -> BundleMetadata:
    if path is None:
        return BundleMetadata(name=fallback_name)
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise InvalidBundleError(f"{path}: bundle metadata document must be a mapping")
    kind = data.get("kind")
    if kind is not None and kind != BUNDLE_KIND:
        raise InvalidBundleError(f"{path}: expected kind '{BUNDLE_KIND}', found kind '{kind}'")
    try:
        return BundleMetadata.model_validate(data.get("metadata") or {"name": fallback_name})
    except ValidationError as exc:
        raise InvalidBundleError(f"{path}: invalid bundle metadata: {exc}") from exc


def _validate_bundle_document(data: object, *, origin: str) -> DeploymentBundle:
    if not isinstance(data, dict):
        raise InvalidBundleError(f"{origin}: bundle document must be a mapping")
    kind = data.get("kind")
    if kind is not None and kind != BUNDLE_KIND:
        raise InvalidBundleError(f"{origin}: expected kind '{BUNDLE_KIND}', found kind '{kind}'")
    if data.get("apiVersion", API_VERSION) != API_VERSION:
        raise InvalidBundleError(
            f"{origin}: unsupported apiVersion '{data.get('apiVersion')}'; expected '{API_VERSION}'"
        )
    try:
        return DeploymentBundle.model_validate(data)
    except ValidationError as exc:
        raise InvalidBundleError(f"{origin}: invalid deployment bundle: {exc}") from exc


def parse_resource_document(data: object, *, origin: str = "<resource>") -> tuple[str, StrictModel]:
    """Parse a resource envelope (``{apiVersion, kind, spec}``) into its
    domain model. Returns ``(kind, definition)``; raises
    :class:`InvalidBundleError` for unknown kinds, bad apiVersions, or
    invalid specs."""
    return _parse_resource_document(data, origin=origin)


def _parse_resource_document(data: object, *, origin: str) -> tuple[str, StrictModel]:
    if not isinstance(data, dict):
        raise InvalidBundleError(f"{origin}: resource document must be a mapping")
    kind = data.get("kind")
    if kind not in _KIND_TO_MODEL:
        supported = ", ".join(sorted(_KIND_TO_MODEL))
        raise InvalidBundleError(f"{origin}: unknown resource kind '{kind}'. Supported kinds: {supported}")
    if data.get("apiVersion") != API_VERSION:
        raise InvalidBundleError(
            f"{origin}: unsupported apiVersion '{data.get('apiVersion')}'; expected '{API_VERSION}'"
        )
    spec = data.get("spec")
    if not isinstance(spec, dict):
        raise InvalidBundleError(f"{origin}: resource document must contain a mapping 'spec'")
    try:
        return kind, _KIND_TO_MODEL[kind].model_validate(spec)
    except ValidationError as exc:
        raise InvalidBundleError(f"{origin}: invalid {kind} resource: {exc}") from exc


def _first_existing(directory: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None
