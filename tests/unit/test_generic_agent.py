"""Tests for the generic agent domain model and configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from osa.generic_agent import (
    AbstractAgent,
    Agent,
    AgentCapabilities,
    AgentDefinition,
    AgentId,
    AgentMetadata,
    AgentMetadataConfig,
    AgentRequest,
    AgentResponse,
    AgentSpec,
    AgentStatus,
    McpRef,
    MemoryConfig,
    ModelRef,
    SecretReference,
    SessionConfig,
    SkillRef,
    ToolRef,
    load_agent_definition,
)


class TestAgentId:
    def test_generate_creates_unique_ids(self) -> None:
        a = AgentId.generate()
        b = AgentId.generate()
        assert a != b

    def test_from_string(self) -> None:
        raw = "12345678-1234-5678-1234-567812345678"
        aid = AgentId.from_string(raw)
        assert str(aid) == raw

    def test_from_string_rejects_invalid(self) -> None:
        with pytest.raises(ValueError):
            AgentId.from_string("not-a-uuid")

    def test_frozen(self) -> None:
        aid = AgentId.generate()
        with pytest.raises(AttributeError):
            aid.value = None


class TestAgentStatus:
    def test_all_values(self) -> None:
        assert AgentStatus.DRAFT == "draft"
        assert AgentStatus.RUNNING == "running"
        assert AgentStatus.ERROR == "error"


class TestAgentMetadata:
    def test_defaults(self) -> None:
        m = AgentMetadata(name="test")
        assert m.name == "test"
        assert m.version == "0.1.0"
        assert m.description == ""
        assert m.status == AgentStatus.DRAFT
        assert isinstance(m.agent_id, AgentId)
        assert m.labels == {}

    def test_custom_values(self) -> None:
        m = AgentMetadata(name="x", version="1.0.0", description="desc")
        assert m.version == "1.0.0"
        assert m.description == "desc"


class TestAgentCapabilities:
    def test_defaults(self) -> None:
        c = AgentCapabilities()
        assert c.streaming is False
        assert c.a2a is False
        assert c.memory is False


class TestAgentRequest:
    def test_minimal(self) -> None:
        r = AgentRequest(input="hello")
        assert r.input == "hello"
        assert r.session_id is None
        assert r.user_id is None

    def test_with_session(self) -> None:
        r = AgentRequest(input="hi", session_id="s1", user_id="u1")
        assert r.session_id == "s1"
        assert r.user_id == "u1"


class TestAgentResponse:
    def test_basic(self) -> None:
        from uuid import uuid4

        inv_id = uuid4()
        r = AgentResponse(output="ok", invocation_id=inv_id)
        assert r.output == "ok"
        assert r.invocation_id == inv_id
        assert r.error is None


class TestSecretReference:
    def test_create(self) -> None:
        s = SecretReference(source="vault", key="api-key")
        assert s.source == "vault"
        assert s.key == "api-key"
        assert s.env_var is None

    def test_with_env_var(self) -> None:
        s = SecretReference(source="env", key="key", env_var="MY_KEY")
        assert s.env_var == "MY_KEY"


class TestStrictModel:
    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            ModelRef(ref="default", unknown_field="bad")


class TestConfigModels:
    def test_model_ref(self) -> None:
        m = ModelRef(ref="default")
        assert m.ref == "default"
        assert m.parameters == {}

    def test_mcp_ref(self) -> None:
        m = McpRef(ref="crm")
        assert m.ref == "crm"
        assert m.tools_filter == []

    def test_tool_ref(self) -> None:
        t = ToolRef(ref="calculator")
        assert t.ref == "calculator"

    def test_skill_ref(self) -> None:
        s = SkillRef(ref="support")
        assert s.ref == "support"

    def test_memory_config_defaults(self) -> None:
        m = MemoryConfig()
        assert m.enabled is False
        assert m.policy is None

    def test_session_config_defaults(self) -> None:
        s = SessionConfig()
        assert s.persistence is False
        assert s.ttl_seconds is None


class TestAgentDefinition:
    def test_minimal_definition(self) -> None:
        d = AgentDefinition(
            metadata=AgentMetadataConfig(name="test-agent"),
            spec=AgentSpec(),
        )
        assert d.metadata.name == "test-agent"
        assert d.kind == "Agent"
        assert d.api_version == "osa/v1alpha1"

    def test_full_definition(self) -> None:
        d = AgentDefinition(
            metadata=AgentMetadataConfig(name="support", version="1.0.0", description="Support agent"),
            spec=AgentSpec(
                description="Handles support",
                instruction="Help users.",
                model=ModelRef(ref="default"),
                mcps=[McpRef(ref="crm")],
                tools=[ToolRef(ref="calculator")],
                skills=[SkillRef(ref="support")],
                memory=MemoryConfig(enabled=True, policy="user-memory"),
                session=SessionConfig(persistence=True),
            ),
        )
        assert d.spec.model is not None
        assert d.spec.model.ref == "default"
        assert len(d.spec.mcps) == 1
        assert d.spec.memory.enabled is True

    def test_rejects_unknown_properties(self) -> None:
        with pytest.raises(ValidationError):
            AgentDefinition(
                metadata=AgentMetadataConfig(name="test"),
                spec=AgentSpec(),
                unknown_field="bad",
            )


class TestLoadAgentDefinition:
    def test_load_from_yaml_string(self) -> None:
        yaml_str = """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: test-agent
  version: "1.0.0"
spec:
  description: A test agent
  instruction: Help users.
  model:
    ref: default
  mcps:
    - ref: crm
  tools:
    - ref: calculator
  skills:
    - ref: support
  memory:
    enabled: true
    policy: user-memory
  session:
    persistence: true
"""
        d = load_agent_definition(yaml_str)
        assert d.metadata.name == "test-agent"
        assert d.spec.model is not None
        assert d.spec.model.ref == "default"
        assert len(d.spec.mcps) == 1
        assert d.spec.memory.enabled is True

    def test_invalid_yaml_fails(self) -> None:
        yaml_str = """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: test
spec:
  unknown_field: bad
"""
        with pytest.raises(ValidationError):
            load_agent_definition(yaml_str)

    def test_env_override_agent_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_str = """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: original
spec: {}
"""
        monkeypatch.setenv("OSA_AGENT_NAME", "overridden")
        d = load_agent_definition(yaml_str)
        assert d.metadata.name == "overridden"

    def test_env_override_model_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_str = """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: test
spec:
  model:
    ref: default
"""
        monkeypatch.setenv("OSA_MODEL_REF", "gpt-4")
        d = load_agent_definition(yaml_str)
        assert d.spec.model is not None
        assert d.spec.model.ref == "gpt-4"

    def test_env_override_boolean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_str = """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: test
spec:
  memory:
    enabled: false
"""
        monkeypatch.setenv("OSA_MEMORY_ENABLED", "true")
        d = load_agent_definition(yaml_str)
        assert d.spec.memory.enabled is True

    def test_load_from_path(self, tmp_path: Path) -> None:
        yaml_content = """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: file-agent
spec: {}
"""
        file_path = tmp_path / "agent.yaml"
        file_path.write_text(yaml_content)
        d = load_agent_definition(file_path)
        assert d.metadata.name == "file-agent"

    def test_load_from_missing_path_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            load_agent_definition(Path("/nonexistent/agent.yaml"))


class StubAgent(AbstractAgent):
    async def invoke(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(output="ok", invocation_id=request.invocation_id)

    async def shutdown(self) -> None:
        pass


class TestAgentProtocol:
    def test_concrete_agent_satisfies_protocol(self) -> None:
        definition = AgentDefinition(
            metadata=AgentMetadataConfig(name="stub"),
            spec=AgentSpec(),
        )
        agent = StubAgent(definition)
        assert isinstance(agent, Agent)

    def test_abstract_agent_forwards_labels(self) -> None:
        definition = AgentDefinition(
            metadata=AgentMetadataConfig(name="labeled", labels={"env": "prod"}),
            spec=AgentSpec(),
        )
        agent = StubAgent(definition)
        assert agent.metadata.labels == {"env": "prod"}
