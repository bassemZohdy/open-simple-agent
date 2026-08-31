"""Deployment bundle loading, validation, and secret resolution (P0.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from pydantic import ValidationError

from osa.generic_agent import (
    AgentDefinition,
    BundleMetadata,
    DeploymentBundle,
    DuplicateResourceError,
    EnvironmentSecretResolver,
    InvalidBundleError,
    SecretReference,
    SecretResolutionError,
    SecretSourceError,
    UnknownReferenceError,
    build_catalogs,
    collect_secret_references,
    load_agent_definition,
    load_bundle,
)

AGENT_YAML = """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: greeter
spec:
  instruction: Say hello.
  model:
    ref: default
  tools:
    - calculator
"""

MODEL_YAML = """
apiVersion: osa/v1alpha1
kind: Model
spec:
  name: default
  provider: fake
  model_id: fake-model
  is_default: true
"""

TOOL_YAML = """
apiVersion: osa/v1alpha1
kind: Tool
spec:
  name: calculator
  description: Basic arithmetic
  timeout_seconds: 5
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_bundle(**kwargs: object) -> DeploymentBundle:
    from osa.generic_agent.model import ModelDefinition
    from osa.generic_agent.tool import ToolDefinition

    agent = load_agent_definition(AGENT_YAML)
    defaults: dict[str, object] = {
        "metadata": BundleMetadata(name="b"),
        "agent": agent,
        "models": [ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True)],
        "tools": [ToolDefinition(name="calculator")],
    }
    defaults.update(kwargs)
    return DeploymentBundle(**defaults)  # type: ignore[arg-type]


class TestBundleFileLoading:
    def test_single_file_bundle(self, tmp_path: Path) -> None:
        bundle_file = _write(
            tmp_path / "bundle.yaml",
            """
apiVersion: osa/v1alpha1
kind: AgentBundle
metadata:
  name: test-bundle
agent:
  apiVersion: osa/v1alpha1
  kind: Agent
  metadata:
    name: greeter
  spec:
    instruction: Say hello.
models:
  - name: default
    provider: fake
    model_id: fake-model
""",
        )
        bundle = load_bundle(bundle_file)
        assert isinstance(bundle, DeploymentBundle)
        assert bundle.api_version == "osa/v1alpha1"
        assert bundle.kind == "AgentBundle"
        assert bundle.metadata.name == "test-bundle"
        assert bundle.agent.metadata.name == "greeter"
        assert [m.name for m in bundle.models] == ["default"]

    def test_missing_path_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_bundle(tmp_path / "nope.yaml")

    def test_wrong_bundle_kind_rejected(self, tmp_path: Path) -> None:
        bundle_file = _write(tmp_path / "bundle.yaml", "kind: NotABundle\nmetadata:\n  name: x\n")
        with pytest.raises(InvalidBundleError, match="expected kind 'AgentBundle'"):
            load_bundle(bundle_file)

    def test_unsupported_api_version_rejected(self, tmp_path: Path) -> None:
        bundle_file = _write(
            tmp_path / "bundle.yaml",
            "apiVersion: osa/v2beta2\nkind: AgentBundle\nmetadata:\n  name: x\n",
        )
        with pytest.raises(InvalidBundleError, match="unsupported apiVersion"):
            load_bundle(bundle_file)

    def test_non_mapping_document_rejected(self, tmp_path: Path) -> None:
        bundle_file = _write(tmp_path / "bundle.yaml", "- just\n- a\n- list\n")
        with pytest.raises(InvalidBundleError, match="must be a mapping"):
            load_bundle(bundle_file)


class TestBundleDirectoryLoading:
    def _write_bundle(self, root: Path, agent_yaml: str = AGENT_YAML) -> Path:
        _write(root / "agent.yaml", agent_yaml)
        models = root / "models"
        models.mkdir(exist_ok=True)
        _write(models / "default.yaml", MODEL_YAML)
        tools = root / "tools"
        tools.mkdir(exist_ok=True)
        _write(tools / "calculator.yaml", TOOL_YAML)
        return root

    def test_directory_bundle(self, tmp_path: Path) -> None:
        root = self._write_bundle(tmp_path / "my-bundle")
        bundle = load_bundle(root)
        assert bundle.metadata.name == "my-bundle"
        assert bundle.agent.metadata.name == "greeter"
        assert [m.name for m in bundle.models] == ["default"]
        assert [t.name for t in bundle.tools] == ["calculator"]

    def test_bundle_metadata_file_overrides_directory_name(self, tmp_path: Path) -> None:
        root = self._write_bundle(tmp_path / "my-bundle")
        _write(
            root / "bundle.yaml",
            "apiVersion: osa/v1alpha1\nkind: AgentBundle\nmetadata:\n  name: custom-name\n",
        )
        bundle = load_bundle(root)
        assert bundle.metadata.name == "custom-name"

    def test_directory_without_agent_file_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "models").mkdir()
        with pytest.raises(InvalidBundleError, match="agent.yaml"):
            load_bundle(tmp_path)

    def test_resource_document_round_trip(self, tmp_path: Path) -> None:
        root = self._write_bundle(tmp_path / "b")
        bundle = load_bundle(root)
        model = bundle.models[0]
        assert model.is_default is True
        tool = bundle.tools[0]
        assert tool.timeout_seconds == 5


class TestBundleResourceValidation:
    def _bundle_with(self, tmp_path: Path, resource_yaml: str) -> Path:
        root = tmp_path / "b"
        root.mkdir()
        _write(root / "agent.yaml", AGENT_YAML)
        models = root / "models"
        models.mkdir()
        _write(models / "default.yaml", resource_yaml)
        return root

    def test_unknown_resource_kind_rejected(self, tmp_path: Path) -> None:
        root = self._bundle_with(
            tmp_path,
            "apiVersion: osa/v1alpha1\nkind: Widget\nspec:\n  name: w\n",
        )
        with pytest.raises(InvalidBundleError, match="unknown resource kind 'Widget'"):
            load_bundle(root)

    def test_resource_with_wrong_api_version_rejected(self, tmp_path: Path) -> None:
        root = self._bundle_with(
            tmp_path,
            "apiVersion: osa/v2\nkind: Model\nspec:\n  name: default\n  provider: fake\n  model_id: m\n",
        )
        with pytest.raises(InvalidBundleError, match="unsupported apiVersion"):
            load_bundle(root)

    def test_resource_without_spec_rejected(self, tmp_path: Path) -> None:
        root = self._bundle_with(
            tmp_path,
            "apiVersion: osa/v1alpha1\nkind: Model\nname: default\n",
        )
        with pytest.raises(InvalidBundleError, match="mapping 'spec'"):
            load_bundle(root)

    def test_resource_schema_violation_rejected(self, tmp_path: Path) -> None:
        root = self._bundle_with(
            tmp_path,
            "apiVersion: osa/v1alpha1\nkind: Model\nspec:\n  name: default\n  provider: fake\n  bogus: true\n",
        )
        with pytest.raises(InvalidBundleError, match="invalid Model resource"):
            load_bundle(root)

    def test_kind_mismatch_with_directory_rejected(self, tmp_path: Path) -> None:
        root = self._bundle_with(
            tmp_path,
            "apiVersion: osa/v1alpha1\nkind: Skill\nspec:\n  name: default\n",
        )
        with pytest.raises(InvalidBundleError, match="expected kind 'Model'"):
            load_bundle(root)


class TestBundleCatalogs:
    def test_build_catalogs_resolves_references(self) -> None:
        catalogs = build_catalogs(_make_bundle())
        assert catalogs.model_catalog.resolve("default").model_id == "fake-model"
        assert "calculator" in catalogs.tool_catalog

    def test_duplicate_model_names_rejected(self) -> None:
        from osa.generic_agent.model import ModelDefinition

        bundle = _make_bundle(
            models=[
                ModelDefinition(name="default", provider="fake", model_id="a"),
                ModelDefinition(name="default", provider="fake", model_id="b"),
            ]
        )
        with pytest.raises(DuplicateResourceError, match="Model resource name 'default'"):
            build_catalogs(bundle)

    def test_duplicate_tool_names_rejected(self) -> None:
        from osa.generic_agent.tool import ToolDefinition

        bundle = _make_bundle(tools=[ToolDefinition(name="calculator"), ToolDefinition(name="calculator")])
        with pytest.raises(DuplicateResourceError, match="Tool resource name 'calculator'"):
            build_catalogs(bundle)

    def test_unknown_model_reference_rejected(self, tmp_path: Path) -> None:
        agent_yaml = AGENT_YAML.replace("ref: default", "ref: missing")
        root = tmp_path / "b"
        root.mkdir()
        _write(root / "agent.yaml", agent_yaml)
        bundle = load_bundle(root)
        with pytest.raises(UnknownReferenceError, match="unknown model 'missing'"):
            build_catalogs(bundle)

    def test_unknown_tool_reference_rejected(self) -> None:
        broken = load_agent_definition(AGENT_YAML.replace("- calculator", "- nonexistent"))
        bundle = _make_bundle(agent=broken)
        with pytest.raises(UnknownReferenceError, match="unknown tool 'nonexistent'"):
            build_catalogs(bundle)

    def test_unknown_memory_policy_reference_rejected(self) -> None:
        agent = load_agent_definition(
            AGENT_YAML
            + """
  memory:
    enabled: true
    policy: nope
"""
        )
        bundle = _make_bundle(agent=agent)
        with pytest.raises(UnknownReferenceError, match="unknown memorypolicy 'nope'"):
            build_catalogs(bundle)


class TestSecretResolver:
    def test_environment_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        resolver = EnvironmentSecretResolver()
        reference = SecretReference(source="env", key="OPENAI_API_KEY")
        assert resolver.resolve(reference) == "sk-test"

    def test_explicit_env_var_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_SECRET_VAR", "value")
        resolver = EnvironmentSecretResolver()
        reference = SecretReference(source="env", key="logical-name", env_var="MY_SECRET_VAR")
        assert resolver.resolve(reference) == "value"

    def test_missing_secret_raises_without_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        resolver = EnvironmentSecretResolver()
        reference = SecretReference(source="env", key="OPENAI_API_KEY")
        with pytest.raises(SecretResolutionError) as excinfo:
            resolver.resolve(reference)
        message = str(excinfo.value)
        assert "OPENAI_API_KEY" in message
        assert "sk-" not in message

    def test_unsupported_source_rejected(self) -> None:
        resolver = EnvironmentSecretResolver()
        reference = SecretReference(source="vault", key="db-password")
        with pytest.raises(SecretSourceError, match="vault"):
            resolver.resolve(reference)

    def test_collect_secret_references(self) -> None:
        from osa.generic_agent.mcp import McpDefinition
        from osa.generic_agent.model import ModelDefinition

        bundle = _make_bundle(
            models=[
                ModelDefinition(
                    name="default",
                    provider="litellm",
                    model_id="openai/gpt-4o-mini",
                    credential_ref=SecretReference(source="env", key="OPENAI_API_KEY"),
                )
            ],
            mcps=[
                McpDefinition(
                    name="crm",
                    credential_ref=SecretReference(source="env", key="CRM_TOKEN"),
                )
            ],
        )
        references = collect_secret_references(bundle)
        assert [ref.key for ref in references] == ["OPENAI_API_KEY", "CRM_TOKEN"]


class TestAgentDefinitionDocumentValidation:
    def test_unsupported_api_version_rejected(self) -> None:
        with pytest.raises(ValidationError, match="apiVersion"):
            load_agent_definition(
                """
apiVersion: osa/v2
kind: Agent
metadata:
  name: a
spec: {}
"""
            )

    def test_unsupported_kind_rejected(self) -> None:
        with pytest.raises(ValidationError, match="kind"):
            load_agent_definition(
                """
apiVersion: osa/v1alpha1
kind: Workflow
metadata:
  name: a
spec: {}
"""
            )


class TestRangeValidation:
    @pytest.mark.parametrize(
        ("field_path", "invalid"),
        [
            ("runtime.timeout_seconds", 0),
            ("runtime.timeout_seconds", -5),
            ("runtime.max_iterations", 0),
            ("session.ttl_seconds", 0),
            ("memory.max_entries", 0),
        ],
    )
    def test_spec_ranges_rejected(self, field_path: str, invalid: int) -> None:
        section, field_name = field_path.split(".")
        yaml_text = f"""
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: a
spec:
  {section}:
    {field_name}: {invalid}
"""
        with pytest.raises(ValidationError):
            load_agent_definition(yaml_text)

    def test_model_runtime_setting_ranges(self) -> None:
        from osa.generic_agent.model import ModelRuntimeSettings

        with pytest.raises(ValidationError):
            ModelRuntimeSettings(temperature=2.5)
        with pytest.raises(ValidationError):
            ModelRuntimeSettings(top_p=1.5)
        with pytest.raises(ValidationError):
            ModelRuntimeSettings(max_tokens=-1)
        valid = ModelRuntimeSettings(temperature=1.0, top_p=0.9, max_tokens=100)
        assert valid.temperature == 1.0

    def test_tool_timeout_must_be_positive(self) -> None:
        from osa.generic_agent.tool import ToolDefinition

        with pytest.raises(ValidationError):
            ToolDefinition(name="t", timeout_seconds=0)


class TestEnvOverrideModelRefBareString:
    def test_env_override_replaces_bare_string_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OSA_MODEL_REF", "override-model")
        definition = load_agent_definition(
            """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: a
spec:
  model: default
"""
        )
        assert definition.spec.model is not None
        assert definition.spec.model.ref == "override-model"

    def test_env_override_applies_when_model_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OSA_MODEL_REF", "fallback-model")
        definition = load_agent_definition(
            """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: a
spec: {}
"""
        )
        assert definition.spec.model is not None
        assert definition.spec.model.ref == "fallback-model"

    def test_env_override_wins_over_mapping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OSA_MODEL_REF", "env-model")
        definition = load_agent_definition(
            """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: a
spec:
  model:
    ref: file-model
"""
        )
        assert definition.spec.model is not None
        assert definition.spec.model.ref == "env-model"


class TestModelResolutionFailFast:
    def test_missing_model_reference_raises(self) -> None:
        from osa.runtimes.adk import GenericAdkAgent

        definition = AgentDefinition.model_validate(
            {
                "apiVersion": "osa/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "a"},
                "spec": {"model": {"ref": "missing"}},
            }
        )
        from osa.generic_agent import FakeModelProvider, ModelCatalog

        with pytest.raises(ValueError, match="Model 'missing'"):
            GenericAdkAgent(
                definition=definition,
                model_provider=FakeModelProvider(),
                model_catalog=ModelCatalog(),
            )

    def test_default_model_used_when_present(self) -> None:
        from osa.generic_agent import FakeModelProvider, ModelCatalog, ModelDefinition
        from osa.runtimes.adk import GenericAdkAgent

        definition = AgentDefinition.model_validate(
            {
                "apiVersion": "osa/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "a"},
                "spec": {},
            }
        )
        catalog = ModelCatalog()
        catalog.register(ModelDefinition(name="gpt", provider="fake", model_id="gpt-x", is_default=True))
        agent = GenericAdkAgent(definition=definition, model_provider=FakeModelProvider(), model_catalog=catalog)
        adk_model = agent.llm_agent.model
        assert not isinstance(adk_model, str)
        assert adk_model.model == "gpt-x"
