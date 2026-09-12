"""A2A and external-agent tests (P2.1, ADR-005).

Offline: the A2A server is exercised over a real localhost HTTP server
(uvicorn thread) with a scripted agent, matching the deterministic pattern
used by the MCP protocol tests.

Requires the ``a2a`` extra (``uv sync --all-packages --extra a2a``); skipped
otherwise so default test runs stay clean without protocol dependencies.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from importlib.util import find_spec
from typing import TYPE_CHECKING

import pytest
import uvicorn

if TYPE_CHECKING:
    from pathlib import Path

from osa.generic_agent import (
    A2AConfig,
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    ApiKeyCredential,
    AuthMode,
    AuthSettings,
    FakeModelProvider,
    ModelCatalog,
    ModelDefinition,
    ModelRef,
    SecretReference,
    SkillCatalog,
    SkillDefinition,
    SkillRef,
    Tool,
    ToolCatalog,
    ToolDefinition,
    ToolRef,
    ToolResult,
)
from osa.runtimes.adk import GenericAdkAgent
from osa.runtimes.adk.a2a import build_agent_card, invoke_remote_agent

pytestmark = [
    pytest.mark.skipif(
        find_spec("a2a") is None,
        reason="a2a extra not installed (uv sync --all-packages --extra a2a); A2A tests skipped",
    ),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(port: int) -> None:
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.01)
    raise RuntimeError(f"localhost server on port {port} did not start")


def _catalog() -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))
    return catalog


def _make_agent(
    name: str,
    provider: FakeModelProvider | None = None,
    skills: list[str] | None = None,
) -> GenericAdkAgent:
    skill_catalog = SkillCatalog()
    for skill in skills or []:
        skill_catalog.register(SkillDefinition(name=skill, description=f"{skill} capability"))
    definition = AgentDefinition(
        metadata=AgentMetadataConfig(name=name, description=f"{name} agent", version="2.0.0"),
        spec=AgentSpec(
            instruction="Help.",
            description=f"{name} description",
            model=ModelRef(ref="default"),
            skills=[SkillRef(ref=skill) for skill in skills or []],
            a2a=A2AConfig(enabled=True),
        ),
    )
    return GenericAdkAgent(
        definition=definition,
        model_provider=provider or FakeModelProvider(response="ok"),
        model_catalog=_catalog(),
        skill_catalog=skill_catalog,
    )


class TestAgentCardGeneration:
    def test_card_from_definition_and_skills(self) -> None:
        definition = AgentDefinition(
            metadata=AgentMetadataConfig(name="card-agent", description="meta", version="3.1.0"),
            spec=AgentSpec(instruction="Help.", description="spec-level"),
        )
        skills = [SkillDefinition(name="support", description="support capability", tags=["tier1"])]
        card = build_agent_card(definition, skills, "http://127.0.0.1:9/")

        assert card.name == "card-agent"
        assert card.version == "3.1.0"
        assert card.description == "spec-level"
        assert card.skills[0].id == "support"
        assert card.skills[0].tags == ["tier1"]
        assert card.supported_interfaces[0].url == "http://127.0.0.1:9/"
        assert card.default_input_modes == ["text/plain"]

    def test_card_advertises_required_oidc_bearer_security(self) -> None:
        definition = AgentDefinition(
            metadata=AgentMetadataConfig(name="secure-card"),
            spec=AgentSpec(instruction="Help."),
        )
        settings = AuthSettings(
            mode=AuthMode.REQUIRED,
            issuer="https://issuer.example.test/",
            audience="osa-api",
        )
        card = build_agent_card(definition, [], "https://agent.example.test/a2a", auth_settings=settings)

        assert "osa_oidc" in card.security_schemes
        scheme = card.security_schemes["osa_oidc"]
        assert scheme.WhichOneof("scheme") == "open_id_connect_security_scheme"
        assert len(card.security_requirements) == 1
        assert "osa_oidc" in card.security_requirements[0].schemes


class TestA2aTaskStore:
    async def test_close_drains_handler_before_disposing_engine(self) -> None:
        from fastapi import FastAPI

        from osa.runtimes.adk.a2a import close_a2a_task_store

        class Handler:
            def __init__(self) -> None:
                self.closed = False

            async def aclose(self) -> None:
                self.closed = True

        class Engine:
            def __init__(self, handler: Handler) -> None:
                self.handler = handler
                self.disposed_after_handler = False

            async def dispose(self) -> None:
                self.disposed_after_handler = self.handler.closed

        app = FastAPI()
        handler = Handler()
        engine = Engine(handler)
        app.state.osa_a2a_handler = handler
        app.state.osa_a2a_task_engine = engine
        app.state.osa_a2a_task_store = object()

        await close_a2a_task_store(app)

        assert handler.closed
        assert engine.disposed_after_handler
        assert app.state.osa_a2a_handler is None
        assert app.state.osa_a2a_task_store is None
        assert app.state.osa_a2a_task_engine is None

    async def test_executor_publishes_canceled_terminal_state(self) -> None:
        from a2a.server.events import EventQueueLegacy
        from a2a.types import TaskState

        from osa.runtimes.adk.a2a import OsaA2aAgentExecutor

        class Context:
            context_id = "context-cancel"
            task_id = "task-cancel"

        queue = EventQueueLegacy()
        try:
            await OsaA2aAgentExecutor(agent=object()).cancel(Context(), queue)
            event = await queue.dequeue_event()
            assert event.status.state == TaskState.TASK_STATE_CANCELED
        finally:
            queue.task_done()
            await queue.close(immediate=True)

    async def test_database_task_store_persists_and_scopes_by_tenant_and_subject(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import FastAPI

        from osa.generic_agent import AuthenticatedPrincipal, reset_current_principal, set_current_principal
        from osa.runtimes.adk.a2a import (
            _task_store_and_engine,
            close_a2a_task_store,
            initialize_a2a_task_store,
        )
        from osa.runtimes.adk.a2a_migrations import migrate_a2a_schema
        from osa.runtimes.adk.a2a_task_store import bind_task_ownership

        monkeypatch.setenv(
            "OSA_A2A_TASK_DATABASE_URL",
            f"sqlite+aiosqlite:///{tmp_path / 'a2a-tasks.db'}",
        )
        app = FastAPI()
        store, engine = _task_store_and_engine(app)
        assert engine is not None
        await migrate_a2a_schema(
            engine,
            task_table_name="osa_a2a_tasks",
            task_store=store,
            ownership_store=app.state.osa_a2a_ownership_store,
        )
        await initialize_a2a_task_store(app)

        from a2a.server.context import ServerCallContext
        from a2a.types import Task, TaskState, TaskStatus

        task = Task(
            id="task-tenant-a",
            context_id="context-tenant-a",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        principal_a = AuthenticatedPrincipal(
            subject="subject-a",
            issuer="https://issuer.example.test",
            audience=("osa",),
            scopes=frozenset(),
            tenant_id="tenant-a",
        )
        token_a = set_current_principal(principal_a)
        try:
            call_context = ServerCallContext()
            ownership = await app.state.osa_a2a_ownership_store.acquire(
                task.id,
                task.context_id,
                "tenant:tenant-a:subject:subject-a",
            )
            assert ownership is not None
            bind_task_ownership(call_context, ownership)
            await store.save(task, call_context)
            assert (await store.get(task.id, call_context)) is not None
            assert await app.state.osa_a2a_ownership_store.release(ownership, "completed")
        finally:
            reset_current_principal(token_a)

        principal_b = AuthenticatedPrincipal(
            subject="subject-a",
            issuer="https://issuer.example.test",
            audience=("osa",),
            scopes=frozenset(),
            tenant_id="tenant-b",
        )
        token_b = set_current_principal(principal_b)
        try:
            assert (await store.get(task.id, ServerCallContext())) is None
        finally:
            reset_current_principal(token_b)
            await close_a2a_task_store(app)

    async def test_database_task_store_requires_explicit_migration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import FastAPI

        from osa.runtimes.adk.a2a import (
            _task_store_and_engine,
            close_a2a_task_store,
            initialize_a2a_task_store,
        )

        monkeypatch.setenv(
            "OSA_A2A_TASK_DATABASE_URL",
            f"sqlite+aiosqlite:///{tmp_path / 'unmigrated-a2a.db'}",
        )
        monkeypatch.setenv("OSA_A2A_TASK_TABLE", "unmigrated_a2a_tasks")
        app = FastAPI()
        _task_store_and_engine(app)
        with pytest.raises(RuntimeError, match="osa-a2a-migrate"):
            await initialize_a2a_task_store(app)
        await close_a2a_task_store(app)

    async def test_database_task_store_rejects_stale_ownership_fence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reclaimed task cannot be mutated by the previous replica."""
        from datetime import UTC, datetime, timedelta

        from fastapi import FastAPI
        from sqlalchemy import update

        from osa.runtimes.adk.a2a import (
            _task_store_and_engine,
            close_a2a_task_store,
            initialize_a2a_task_store,
        )
        from osa.runtimes.adk.a2a_migrations import migrate_a2a_schema
        from osa.runtimes.adk.a2a_task_store import (
            A2aTaskOwnershipLostError,
            bind_task_ownership,
        )

        monkeypatch.setenv(
            "OSA_A2A_TASK_DATABASE_URL",
            f"sqlite+aiosqlite:///{tmp_path / 'stale-fence.db'}",
        )
        monkeypatch.setenv("OSA_A2A_TASK_TABLE", "stale_fence_tasks")
        app = FastAPI()
        store, engine = _task_store_and_engine(app)
        assert engine is not None
        ownership_store = app.state.osa_a2a_ownership_store
        await migrate_a2a_schema(
            engine,
            task_table_name="stale_fence_tasks",
            task_store=store,
            ownership_store=ownership_store,
        )
        await initialize_a2a_task_store(app)

        from a2a.server.context import ServerCallContext
        from a2a.types import Task, TaskState, TaskStatus

        task = Task(
            id="task-stale-fence",
            context_id="context-stale-fence",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        old_lease = await ownership_store.acquire(
            task.id,
            task.context_id,
            "anonymous",
        )
        assert old_lease is not None
        old_context = ServerCallContext()
        bind_task_ownership(old_context, old_lease)
        await store.save(task, old_context)

        async with engine.begin() as connection:
            await connection.execute(
                update(ownership_store._table)  # noqa: SLF001 - expiry is test setup
                .where(ownership_store._table.c.task_id == task.id)
                .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
            )

        from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore

        successor_store = A2aTaskOwnershipStore(
            engine,
            table_name=ownership_store.table_name,
            lease_seconds=5,
        )
        successor = await successor_store.acquire(
            task.id,
            task.context_id,
            "anonymous",
        )
        assert successor is not None
        task.status.state = TaskState.TASK_STATE_COMPLETED
        with pytest.raises(A2aTaskOwnershipLostError):
            await store.save(task, old_context)
        assert await successor_store.release(successor, "completed")
        await close_a2a_task_store(app)

    def test_database_task_table_name_is_validated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi import FastAPI

        from osa.runtimes.adk.a2a import _task_store_and_engine

        monkeypatch.setenv("OSA_A2A_TASK_DATABASE_URL", "sqlite+aiosqlite:///ignored.db")
        monkeypatch.setenv("OSA_A2A_TASK_TABLE", "tasks;drop")
        with pytest.raises(ValueError, match="simple SQL identifier"):
            _task_store_and_engine(FastAPI())


class TestA2aServer:
    @staticmethod
    def _serve(agent: GenericAdkAgent, *, require_api_key: str | None = None) -> int:
        """Serve an agent over A2A on a localhost port; returns the port."""
        from fastapi import FastAPI

        from osa.runtimes.adk.a2a import attach_a2a_routes

        port = _free_port()
        app = FastAPI()
        if require_api_key is not None:

            @app.middleware("http")
            async def require_key(request, call_next):  # noqa: ANN001
                if request.headers.get("x-api-key") != require_api_key:
                    from starlette.responses import JSONResponse

                    return JSONResponse({"error": "unauthorized"}, status_code=401)
                return await call_next(request)

        attach_a2a_routes(app, agent, url=f"http://127.0.0.1:{port}")
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True).start()
        _wait_for_server(port)
        return port

    async def test_card_served_at_well_known_path(self) -> None:
        import httpx

        port = self._serve(_make_agent("card-server", skills=["support"]))
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as http:
            response = await http.get("/.well-known/agent-card.json")
            assert response.status_code == 200
            card = response.json()
            assert card["name"] == "card-server"
            assert any(s["id"] == "support" for s in card["skills"])

    async def test_message_send_completes_task_with_output(self) -> None:
        port = self._serve(_make_agent("echo-server", provider=FakeModelProvider(response="a2a answer")))
        output = await invoke_remote_agent(f"http://127.0.0.1:{port}", "hello over a2a", timeout_seconds=20)
        assert output == "a2a answer"

    async def test_agent_error_maps_to_failed_task(self) -> None:
        from osa.generic_agent import ModelResponse
        from osa.generic_agent.a2a_client import RemoteA2aError

        class ErrorModel(FakeModelProvider):
            async def generate(self, prompt: str, model_id: str, **kwargs: object) -> ModelResponse:
                raise RuntimeError("boom")

        port = self._serve(_make_agent("error-server", provider=ErrorModel()))
        with pytest.raises(RemoteA2aError, match="boom"):
            await invoke_remote_agent(f"http://127.0.0.1:{port}", "trigger", timeout_seconds=20)

    async def test_api_key_credential_is_sent_to_remote_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from osa.generic_agent import EnvironmentSecretResolver

        monkeypatch.setenv("REMOTE_A2A_KEY", "api-key")
        port = self._serve(
            _make_agent("secure-server", provider=FakeModelProvider(response="secure answer")),
            require_api_key="api-key",
        )
        output = await invoke_remote_agent(
            f"http://127.0.0.1:{port}",
            "hello",
            timeout_seconds=20,
            credential=ApiKeyCredential(secret_ref=SecretReference(source="env", key="REMOTE_A2A_KEY")),
            secret_resolver=EnvironmentSecretResolver(),
        )
        assert output == "secure answer"


class TestExternalAgentRecords:
    async def test_registered_credential_is_used_and_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from httpx import ASGITransport, AsyncClient

        from osa.control_plane.backend.external_agents import ExternalAgentCatalog
        from osa.control_plane.backend.service import create_control_plane_app
        from osa.generic_agent import EnvironmentSecretResolver

        monkeypatch.setenv("PARTNER_A2A_KEY", "api-key")
        agent = _make_agent("secure-external", provider=FakeModelProvider(response="remote reply"))
        port = TestA2aServer._serve(agent, require_api_key="api-key")
        app = create_control_plane_app(secret_resolver=EnvironmentSecretResolver())
        app.state.external_agent_catalog = ExternalAgentCatalog()

        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
        ):
            registered = await c.post(
                "/external-agents",
                json={
                    "name": "partner",
                    "url": f"http://127.0.0.1:{port}",
                    "credential": {
                        "type": "api_key",
                        "secret_ref": {"source": "env", "key": "PARTNER_A2A_KEY"},
                    },
                },
            )
            assert registered.status_code == 201, registered.text
            assert "credential" not in registered.json()

            external_id = registered.json()["external_id"]
            invoked = await c.post(f"/external-agents/{external_id}/invoke", params={"message": "ping"})
            assert invoked.status_code == 200
            assert invoked.json()["output"] == "remote reply"

    async def test_register_refresh_invoke_and_health(self) -> None:
        from httpx import ASGITransport, AsyncClient

        from osa.control_plane.backend.external_agents import ExternalAgentCatalog
        from osa.control_plane.backend.service import create_control_plane_app

        agent = _make_agent("external-target", provider=FakeModelProvider(response="remote reply"))
        port = TestA2aServer._serve(agent)

        catalog = ExternalAgentCatalog()
        app = create_control_plane_app()
        app.state.external_agent_catalog = catalog

        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
        ):
            registered = await c.post(
                "/external-agents",
                json={"name": "partner", "url": f"http://127.0.0.1:{port}"},
            )
            assert registered.status_code == 201, registered.text
            body = registered.json()
            assert body["status"] == "healthy"
            assert body["card_name"] == "external-target"
            assert body["agent_type"] == "external"

            listed = await c.get("/external-agents", params={"status": "healthy"})
            assert listed.json()[0]["name"] == "partner"

            external_id = body["external_id"]
            invoked = await c.post(
                f"/external-agents/{external_id}/invoke",
                params={"message": "ping"},
            )
            assert invoked.status_code == 200
            assert invoked.json()["output"] == "remote reply"

            refreshed = await c.post(f"/external-agents/{external_id}/refresh")
            assert refreshed.json()["status"] == "healthy"

            assert (await c.delete(f"/external-agents/{external_id}")).status_code == 204
            assert (await c.get(f"/external-agents/{external_id}")).status_code == 404

    async def test_register_unreachable_agent_is_422(self) -> None:
        from httpx import ASGITransport, AsyncClient

        from osa.control_plane.backend.external_agents import ExternalAgentCatalog
        from osa.control_plane.backend.service import create_control_plane_app

        app = create_control_plane_app()
        app.state.external_agent_catalog = ExternalAgentCatalog()

        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
        ):
            response = await c.post(
                "/external-agents",
                json={"name": "dead", "url": "http://127.0.0.1:1"},
            )
            assert response.status_code == 422

    async def test_duplicate_name_is_409(self) -> None:
        from httpx import ASGITransport, AsyncClient

        from osa.control_plane.backend.external_agents import ExternalAgentCatalog
        from osa.control_plane.backend.service import create_control_plane_app

        agent = _make_agent("dup-target", provider=FakeModelProvider(response="ok"))
        port = TestA2aServer._serve(agent)
        catalog = ExternalAgentCatalog()
        app = create_control_plane_app()
        app.state.external_agent_catalog = catalog

        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
        ):
            first = await c.post("/external-agents", json={"name": "same", "url": f"http://127.0.0.1:{port}"})
            assert first.status_code == 201
            second = await c.post("/external-agents", json={"name": "same", "url": f"http://127.0.0.1:{port}"})
            assert second.status_code == 409


class TestExternalAgentsNotDeployable:
    async def test_external_agent_type_rejected_on_deploy(self) -> None:
        """External records are structurally barred from deployment."""
        from httpx import ASGITransport, AsyncClient

        from osa.control_plane.backend.agent_catalog import AgentCatalog, AgentRecord
        from osa.control_plane.backend.service import create_control_plane_app

        catalog = AgentCatalog()
        catalog.create(AgentRecord(agent_id="ext-1", name="external-one", agent_type="external"))
        app = create_control_plane_app()
        app.state.agent_repository._catalog = catalog  # noqa: SLF001 - test injection

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/agents/ext-1/deploy", json={})
            assert response.status_code == 422
            assert "external" in response.json()["error"]["message"]


class TestAInvokesBAcceptance:
    async def test_managed_agent_invokes_managed_agent_over_a2a(self) -> None:
        """Managed agent A invokes served agent B through the A2A protocol.

        A's tool performs the A2A call (sync tool bridge running the async
        client in a fresh loop — legal because tools execute in a worker
        thread inside the ADK loop), and A's model consumes B's answer.
        """
        from tests.integration.test_native_function_calling import (
            ScriptedLlm,
            _call,
            _final,
            scripted_registry,
        )

        b_port = TestA2aServer._serve(_make_agent("agent-b", provider=FakeModelProvider(response="B's answer")))
        received: list[str] = []

        class AskAgentBTool(Tool):
            name: str = "ask_agent_b"
            description: str = "Ask agent B over A2A"

            def execute(self, **kwargs: object) -> ToolResult:
                message = str(kwargs.get("message", ""))
                received.append(message)
                output = asyncio.run(invoke_remote_agent(f"http://127.0.0.1:{b_port}", message, timeout_seconds=20))
                return ToolResult(success=True, output=output)

        model = ScriptedLlm(
            model="fake-model",
            script=[
                _call("ask_agent_b", {"message": "give me the answer"}),
                _final("B said: B's answer"),
            ],
        )
        tool_catalog = ToolCatalog()
        tool_catalog.register_definition(ToolDefinition(name="ask_agent_b", description="Ask B"))
        tool_catalog.register_tool(AskAgentBTool())

        agent_a = GenericAdkAgent(
            definition=AgentDefinition(
                metadata=AgentMetadataConfig(name="agent-a"),
                spec=AgentSpec(
                    instruction="Delegate to B.",
                    model=ModelRef(ref="default"),
                    tools=[ToolRef(ref="ask_agent_b")],
                ),
            ),
            model_provider=FakeModelProvider(),
            model_catalog=_catalog(),
            tool_catalog=tool_catalog,
            model_adapters=scripted_registry(model),
        )

        response = await agent_a.invoke(AgentRequest(input="delegate"))

        assert response.output == "B said: B's answer"
        assert response.error is None
        assert received == ["give me the answer"]
