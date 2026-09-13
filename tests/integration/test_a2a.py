"""A2A and external-agent tests (P2.1, ADR-005).

Offline: the A2A server is exercised over a real localhost HTTP server
(uvicorn thread) with a scripted agent, matching the deterministic pattern
used by the MCP protocol tests.

Requires the ``a2a`` extra (``uv sync --all-packages --extra a2a``); skipped
otherwise so default test runs stay clean without protocol dependencies.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing as mp
import os
import socket
import threading
import time
from importlib.util import find_spec
from queue import Empty
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
import uvicorn

if TYPE_CHECKING:
    from pathlib import Path

from osa.generic_agent import (
    A2AConfig,
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentResponse,
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


class _ProcessControlledAgent:
    """Small process-local agent used by the PostgreSQL handler acceptance."""

    def __init__(self, name: str, release_event: Any) -> None:
        self.definition = _make_agent(name).definition
        self.skills: list[Any] = []
        self.release_event = release_event
        self.started = asyncio.Event()
        self.call_count = 0

    async def invoke(self, request: object) -> AgentResponse:
        del request
        self.call_count += 1
        self.started.set()
        while not self.release_event.is_set():
            await asyncio.sleep(0.05)
        return AgentResponse(output="process response", invocation_id=uuid4())

    async def shutdown(self) -> None:
        return None


async def _read_process_result(result_queue: Any, timeout: float = 10.0) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(result_queue.get, True, timeout)
    except Empty as exc:
        raise AssertionError("A2A process worker did not report within the timeout") from exc
    if not isinstance(result, dict):
        raise AssertionError(f"A2A process worker returned an invalid result: {result!r}")
    if result.get("event") == "error":
        raise AssertionError(f"A2A process worker failed: {result.get('error')}")
    return result


async def _run_a2a_process_worker(
    role: str,
    database_url: str,
    task_table_name: str,
    ownership_table_name: str,
    command_queue: Any,
    result_queue: Any,
    release_event: Any,
) -> None:
    os.environ["OSA_A2A_OWNER_ID"] = f"process-acceptance-{role}"
    from a2a.server.context import ServerCallContext
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import DatabaseTaskStore
    from a2a.types import (
        CancelTaskRequest,
        GetTaskRequest,
        Message,
        Part,
        Role,
        SendMessageConfiguration,
        SendMessageRequest,
        Task,
    )
    from sqlalchemy.ext.asyncio import create_async_engine

    from osa.runtimes.adk.a2a import OsaA2aAgentExecutor, _a2a_task_owner, build_agent_card
    from osa.runtimes.adk.a2a_event_relay import A2aTaskEventRelay
    from osa.runtimes.adk.a2a_event_store import A2aTaskEventStore, event_table_name
    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore
    from osa.runtimes.adk.a2a_task_store import FencedDatabaseTaskStore

    engine = create_async_engine(database_url, pool_pre_ping=True)
    ownership_store = A2aTaskOwnershipStore(
        engine,
        table_name=ownership_table_name,
        lease_seconds=5,
    )
    sdk_store = DatabaseTaskStore(
        engine,
        create_table=False,
        table_name=task_table_name,
        owner_resolver=_a2a_task_owner,
    )
    event_store = A2aTaskEventStore(
        engine,
        table_name=event_table_name(task_table_name),
    )
    task_store = FencedDatabaseTaskStore(sdk_store, ownership_store, event_store)
    event_relay = A2aTaskEventRelay(event_store, ownership_store, timeout_seconds=15)
    agent = _ProcessControlledAgent(f"process-{role}", release_event)
    card = build_agent_card(agent.definition, [], "http://127.0.0.1/a2a")
    handler = DefaultRequestHandler(
        agent_executor=cast(
            "Any",
            OsaA2aAgentExecutor(
                agent,
                ownership_store=ownership_store,
                task_store=task_store,
                event_store=event_store,
            ),
        ),
        task_store=cast("Any", task_store),
        agent_card=card,
    )

    await task_store.initialize()
    result_queue.put({"event": "ready", "role": role})
    stream_tasks: set[asyncio.Task[None]] = set()

    async def stream_events(task_id: str) -> None:
        from a2a.types import TaskStatusUpdateEvent

        event_types: list[str] = []
        states: list[int] = []
        async for event in event_relay.stream(scope_key="anonymous", task_id=task_id, timeout_seconds=15):
            event_types.append(type(event).__name__)
            if isinstance(event, TaskStatusUpdateEvent):
                states.append(int(event.status.state))
        result_queue.put(
            {
                "event": "streamed",
                "task_id": task_id,
                "event_types": event_types,
                "states": states,
            }
        )

    try:
        while True:
            command = await asyncio.to_thread(command_queue.get)
            operation = command.get("op")
            if operation == "start":
                request = SendMessageRequest(
                    message=Message(
                        message_id=f"process-{role}-message",
                        role=Role.ROLE_USER,
                        parts=[Part(text="start process task")],
                    ),
                    configuration=SendMessageConfiguration(return_immediately=True),
                )
                initial = await handler.on_message_send(request, ServerCallContext())
                if not isinstance(initial, Task):
                    raise AssertionError("process acceptance did not create an A2A task")
                await asyncio.wait_for(agent.started.wait(), timeout=10)
                result_queue.put({"event": "started", "role": role, "task_id": initial.id})
            elif operation == "stream":
                stream_task = asyncio.create_task(stream_events(str(command["task_id"])))
                stream_tasks.add(stream_task)
                stream_task.add_done_callback(stream_tasks.discard)
                result_queue.put({"event": "stream-started", "task_id": command["task_id"]})
            elif operation == "lookup":
                task = await handler.on_get_task(
                    GetTaskRequest(id=command["task_id"]),
                    ServerCallContext(),
                )
                result_queue.put(
                    {
                        "event": "lookup",
                        "task_id": command["task_id"],
                        "state": int(task.status.state) if task is not None else None,
                    }
                )
            elif operation == "cancel":
                task = await handler.on_cancel_task(
                    CancelTaskRequest(id=command["task_id"]),
                    ServerCallContext(),
                )
                if not isinstance(task, Task):
                    raise AssertionError("process acceptance cancellation did not return a task")
                result_queue.put(
                    {
                        "event": "cancelled",
                        "task_id": command["task_id"],
                        "state": int(task.status.state),
                        "agent_calls": agent.call_count,
                    }
                )
            elif operation == "shutdown":
                return
            else:
                raise AssertionError(f"unknown A2A process command: {operation!r}")
    finally:
        for stream_task in stream_tasks:
            stream_task.cancel()
        for stream_task in stream_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
        await handler.aclose()
        await engine.dispose()


def _a2a_process_worker(
    role: str,
    database_url: str,
    task_table_name: str,
    ownership_table_name: str,
    command_queue: Any,
    result_queue: Any,
    release_event: Any,
) -> None:
    try:
        asyncio.run(
            _run_a2a_process_worker(
                role,
                database_url,
                task_table_name,
                ownership_table_name,
                command_queue,
                result_queue,
                release_event,
            )
        )
    except Exception as exc:
        result_queue.put({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        raise


async def _run_a2a_relay_process_worker(
    role: str,
    database_url: str,
    task_table_name: str,
    ownership_table_name: str,
    command_queue: Any,
    result_queue: Any,
) -> None:
    """Exercise durable relay events from one independent worker process."""
    os.environ["OSA_A2A_OWNER_ID"] = f"relay-process-{role}"
    from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
    from sqlalchemy.ext.asyncio import create_async_engine

    from osa.runtimes.adk.a2a_event_relay import A2aTaskEventRelay
    from osa.runtimes.adk.a2a_event_store import A2aTaskEventStore, event_table_name
    from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore

    engine = create_async_engine(database_url, pool_pre_ping=True)
    ownership_store = A2aTaskOwnershipStore(
        engine,
        table_name=ownership_table_name,
        lease_seconds=5,
    )
    event_store = A2aTaskEventStore(
        engine,
        table_name=event_table_name(task_table_name),
    )
    event_relay = A2aTaskEventRelay(
        event_store,
        ownership_store,
        poll_interval_seconds=0.01,
        timeout_seconds=10,
    )
    leases: dict[str, Any] = {}
    result_queue.put({"event": "ready", "role": role})

    try:
        while True:
            command = await asyncio.to_thread(command_queue.get)
            operation = command.get("op")
            if operation == "acquire":
                task_id = str(command["task_id"])
                ownership = await ownership_store.acquire(
                    task_id,
                    str(command["context_id"]),
                    str(command["scope_key"]),
                )
                if ownership is None:
                    raise AssertionError(f"{role} could not acquire {task_id}")
                leases[task_id] = ownership
                result_queue.put({"event": "acquired", "fence": ownership.fence})
            elif operation == "append":
                task_id = str(command["task_id"])
                ownership = leases.get(task_id)
                if ownership is None:
                    raise AssertionError(f"{role} has no lease for {task_id}")
                context_id = str(command["context_id"])
                state_name = str(command["state"])
                if command["kind"] == "Task":
                    event = Task(
                        id=task_id,
                        context_id=context_id,
                        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    )
                else:
                    state = {
                        "working": TaskState.TASK_STATE_WORKING,
                        "completed": TaskState.TASK_STATE_COMPLETED,
                        "failed": TaskState.TASK_STATE_FAILED,
                    }.get(state_name)
                    if state is None:
                        raise AssertionError(f"unknown relay event state: {state_name}")
                    event = TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(state=state),
                    )
                sequence = await event_store.append_if_owned(
                    ownership,
                    event_type=type(event).__name__,
                    payload=event.SerializeToString(deterministic=True),
                    ownership_store=ownership_store,
                )
                result_queue.put(
                    {
                        "event": "appended",
                        "accepted": sequence is not None,
                        "sequence": sequence,
                    }
                )
            elif operation == "stream":
                event_types: list[str] = []
                states: list[int] = []
                async for event in event_relay.stream(
                    scope_key=str(command["scope_key"]),
                    task_id=str(command["task_id"]),
                    after_sequence=int(command.get("after_sequence", 0)),
                    timeout_seconds=10,
                ):
                    event_types.append(type(event).__name__)
                    if isinstance(event, (Task, TaskStatusUpdateEvent)):
                        states.append(int(event.status.state))
                result_queue.put(
                    {
                        "event": "streamed",
                        "event_types": event_types,
                        "states": states,
                    }
                )
            elif operation == "release":
                task_id = str(command["task_id"])
                ownership = leases.get(task_id)
                if ownership is None:
                    raise AssertionError(f"{role} has no lease for {task_id}")
                assert await ownership_store.release(ownership, str(command["state"]))
                result_queue.put({"event": "released"})
            elif operation == "shutdown":
                return
            else:
                raise AssertionError(f"unknown A2A relay process command: {operation!r}")
    finally:
        await engine.dispose()


def _a2a_relay_process_worker(
    role: str,
    database_url: str,
    task_table_name: str,
    ownership_table_name: str,
    command_queue: Any,
    result_queue: Any,
) -> None:
    try:
        asyncio.run(
            _run_a2a_relay_process_worker(
                role,
                database_url,
                task_table_name,
                ownership_table_name,
                command_queue,
                result_queue,
            )
        )
    except Exception as exc:
        result_queue.put({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        raise


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

    @pytest.mark.skipif(
        not os.environ.get("OSA_TEST_DATABASE_URL"),
        reason="OSA_TEST_DATABASE_URL not configured; PostgreSQL A2A replica acceptance skipped",
    )
    async def test_postgres_replica_task_state_and_tenant_isolation(self) -> None:
        """Two ownership workers share task state without crossing tenants."""
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        from a2a.server.context import ServerCallContext
        from a2a.server.tasks import DatabaseTaskStore
        from a2a.types import Task, TaskState, TaskStatus
        from sqlalchemy import update
        from sqlalchemy.ext.asyncio import create_async_engine

        from osa.generic_agent import (
            AuthenticatedPrincipal,
            reset_current_principal,
            set_current_principal,
        )
        from osa.runtimes.adk.a2a import _a2a_task_owner
        from osa.runtimes.adk.a2a_migrations import migrate_a2a_schema
        from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore
        from osa.runtimes.adk.a2a_task_store import (
            A2aTaskOwnershipLostError,
            FencedDatabaseTaskStore,
            bind_task_ownership,
        )

        suffix = uuid4().hex[:12]
        task_table_name = f"osa_a2a_replica_{suffix}"
        engine = create_async_engine(
            os.environ["OSA_TEST_DATABASE_URL"],
            pool_pre_ping=True,
        )
        ownership_one = A2aTaskOwnershipStore(
            engine,
            table_name=f"{task_table_name}_ownership",
            lease_seconds=5,
        )
        ownership_two = A2aTaskOwnershipStore(
            engine,
            table_name=f"{task_table_name}_ownership",
            lease_seconds=5,
        )
        # The SDK's dynamic ORM registry cannot define the same table twice in
        # one Python process; two fenced adapters still model independent
        # workers against the same durable task table.
        sdk_store = DatabaseTaskStore(
            engine,
            create_table=False,
            table_name=task_table_name,
            owner_resolver=_a2a_task_owner,
        )
        store_one = FencedDatabaseTaskStore(sdk_store, ownership_one)
        store_two = FencedDatabaseTaskStore(sdk_store, ownership_two)

        try:
            await migrate_a2a_schema(
                engine,
                task_table_name=task_table_name,
                task_store=store_one,
                ownership_store=ownership_one,
            )
            await store_one.initialize()

            principal_a = AuthenticatedPrincipal(
                subject="replica-user",
                issuer="https://issuer.example.test",
                audience=("osa",),
                scopes=frozenset(),
                tenant_id="tenant-a",
            )
            token_a = set_current_principal(principal_a)
            try:
                context = ServerCallContext()
                scope_key = _a2a_task_owner(context)
                task = Task(
                    id=f"task-complete-{suffix}",
                    context_id=f"context-complete-{suffix}",
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                )
                lease = await ownership_one.acquire(task.id, task.context_id, scope_key)
                assert lease is not None
                assert await ownership_two.acquire(task.id, task.context_id, scope_key) is None
                bind_task_ownership(context, lease)
                await store_one.save(task, context)
                task.status.state = TaskState.TASK_STATE_COMPLETED
                await store_one.save(task, context)
                assert await ownership_one.release(lease, "completed")

                replica_task = await store_two.get(task.id, context)
                assert replica_task is not None
                assert replica_task.status.state == TaskState.TASK_STATE_COMPLETED

                principal_b = AuthenticatedPrincipal(
                    subject="replica-user",
                    issuer="https://issuer.example.test",
                    audience=("osa",),
                    scopes=frozenset(),
                    tenant_id="tenant-b",
                )
                token_b = set_current_principal(principal_b)
                try:
                    assert await store_two.get(task.id, ServerCallContext()) is None
                finally:
                    reset_current_principal(token_b)

                failed_task = Task(
                    id=f"task-failed-{suffix}",
                    context_id=f"context-failed-{suffix}",
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
                failed_lease = await ownership_two.acquire(
                    failed_task.id,
                    failed_task.context_id,
                    scope_key,
                )
                assert failed_lease is not None
                failed_context = ServerCallContext()
                bind_task_ownership(failed_context, failed_lease)
                await store_two.save(failed_task, failed_context)
                failed_task.status.state = TaskState.TASK_STATE_FAILED
                await store_two.save(failed_task, failed_context)
                assert await ownership_two.release(failed_lease, "failed")
                failed_replica_task = await store_one.get(failed_task.id, context)
                assert failed_replica_task is not None
                assert failed_replica_task.status.state == TaskState.TASK_STATE_FAILED

                recovery_task = Task(
                    id=f"task-recovery-{suffix}",
                    context_id=f"context-recovery-{suffix}",
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
                old_context = ServerCallContext()
                old_lease = await ownership_one.acquire(
                    recovery_task.id,
                    recovery_task.context_id,
                    scope_key,
                )
                assert old_lease is not None
                bind_task_ownership(old_context, old_lease)
                await store_one.save(recovery_task, old_context)
                async with engine.begin() as connection:
                    await connection.execute(
                        update(ownership_one._table)  # noqa: SLF001 - expiry is acceptance setup
                        .where(ownership_one._table.c.task_id == recovery_task.id)
                        .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                    )
                new_lease = await ownership_two.acquire(
                    recovery_task.id,
                    recovery_task.context_id,
                    scope_key,
                )
                assert new_lease is not None
                new_context = ServerCallContext()
                bind_task_ownership(new_context, new_lease)
                recovery_task.status.state = TaskState.TASK_STATE_COMPLETED
                await store_two.save(recovery_task, new_context)
                with pytest.raises(A2aTaskOwnershipLostError):
                    await store_one.save(recovery_task, old_context)
                assert await ownership_two.release(new_lease, "completed")
            finally:
                reset_current_principal(token_a)
        finally:
            await engine.dispose()

    def test_database_task_table_name_is_validated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi import FastAPI

        from osa.runtimes.adk.a2a import _task_store_and_engine

        monkeypatch.setenv("OSA_A2A_TASK_DATABASE_URL", "sqlite+aiosqlite:///ignored.db")
        monkeypatch.setenv("OSA_A2A_TASK_TABLE", "tasks;drop")
        with pytest.raises(ValueError, match="simple SQL identifier"):
            _task_store_and_engine(FastAPI())

        monkeypatch.setenv("OSA_A2A_TASK_TABLE", "x" * 54)
        with pytest.raises(ValueError, match="at most 53"):
            _task_store_and_engine(FastAPI())


class TestA2aDistributedHandlerAcceptance:
    async def test_independent_handlers_lookup_and_cancel_shared_active_task(self, tmp_path: Path) -> None:
        """A second handler cancels a task owned by the first handler."""
        from uuid import uuid4

        from a2a.server.context import ServerCallContext
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.tasks import DatabaseTaskStore
        from a2a.types import (
            CancelTaskRequest,
            GetTaskRequest,
            Message,
            Part,
            Role,
            SendMessageConfiguration,
            SendMessageRequest,
            Task,
            TaskState,
            TaskStatus,
        )
        from sqlalchemy import update
        from sqlalchemy.ext.asyncio import create_async_engine

        from osa.generic_agent import AgentResponse
        from osa.runtimes.adk.a2a import (
            OsaA2aAgentExecutor,
            _a2a_task_owner,
            build_agent_card,
        )
        from osa.runtimes.adk.a2a_migrations import migrate_a2a_schema
        from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore
        from osa.runtimes.adk.a2a_task_store import FencedDatabaseTaskStore

        class ControlledAgent:
            def __init__(self, name: str) -> None:
                definition = _make_agent(name).definition
                self.definition = definition
                self.skills: list[Any] = []
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.call_count = 0

            async def invoke(self, request: object) -> AgentResponse:
                del request
                self.call_count += 1
                self.started.set()
                await self.release.wait()
                return AgentResponse(output="controlled response", invocation_id=uuid4())

            async def shutdown(self) -> None:
                return None

        database_url = f"sqlite+aiosqlite:///{tmp_path / 'shared-a2a.db'}"
        engine_one = create_async_engine(database_url, pool_pre_ping=True)
        engine_two = create_async_engine(database_url, pool_pre_ping=True)
        ownership_one = A2aTaskOwnershipStore(
            engine_one,
            table_name="handler_acceptance_ownership",
            lease_seconds=5,
        )
        ownership_two = A2aTaskOwnershipStore(
            engine_two,
            table_name="handler_acceptance_ownership",
            lease_seconds=5,
        )
        sdk_store_one = DatabaseTaskStore(
            engine_one,
            create_table=False,
            table_name="tasks",
            owner_resolver=_a2a_task_owner,
        )
        sdk_store_two = DatabaseTaskStore(
            engine_two,
            create_table=False,
            table_name="tasks",
            owner_resolver=_a2a_task_owner,
        )
        store_one = FencedDatabaseTaskStore(sdk_store_one, ownership_one)
        store_two = FencedDatabaseTaskStore(sdk_store_two, ownership_two)
        await migrate_a2a_schema(
            engine_one,
            task_table_name="tasks",
            task_store=store_one,
            ownership_store=ownership_one,
        )
        await store_one.initialize()
        await store_two.initialize()

        agent_one = ControlledAgent("handler-one")
        agent_two = ControlledAgent("handler-two")
        card = build_agent_card(agent_one.definition, [], "http://test/a2a")
        handler_one = DefaultRequestHandler(
            agent_executor=cast(
                "Any",
                OsaA2aAgentExecutor(
                    agent_one,
                    ownership_store=ownership_one,
                    task_store=store_one,
                ),
            ),
            task_store=cast("Any", store_one),
            agent_card=card,
        )
        handler_two = DefaultRequestHandler(
            agent_executor=cast(
                "Any",
                OsaA2aAgentExecutor(
                    agent_two,
                    ownership_store=ownership_two,
                    task_store=store_two,
                ),
            ),
            task_store=cast("Any", store_two),
            agent_card=card,
        )

        try:
            request = SendMessageRequest(
                message=Message(
                    message_id="handler-acceptance-message",
                    role=Role.ROLE_USER,
                    parts=[Part(text="start long task")],
                ),
                configuration=SendMessageConfiguration(return_immediately=True),
            )
            initial = await handler_one.on_message_send(request, ServerCallContext())
            assert isinstance(initial, Task)
            await asyncio.wait_for(agent_one.started.wait(), timeout=2)

            observed = await handler_two.on_get_task(
                GetTaskRequest(id=initial.id),
                ServerCallContext(),
            )
            assert observed is not None
            assert observed.status.state == TaskState.TASK_STATE_SUBMITTED

            cancel_operation = asyncio.create_task(
                handler_two.on_cancel_task(
                    CancelTaskRequest(id=initial.id),
                    ServerCallContext(),
                )
            )
            await asyncio.sleep(0.2)
            assert not cancel_operation.done()
            assert agent_two.call_count == 0

            agent_one.release.set()
            canceled = await asyncio.wait_for(cancel_operation, timeout=5)
            assert isinstance(canceled, Task)
            assert canceled.status.state == TaskState.TASK_STATE_CANCELED
            assert agent_one.call_count == 1
            assert agent_two.call_count == 0

            final = await handler_two.on_get_task(
                GetTaskRequest(id=initial.id),
                ServerCallContext(),
            )
            assert final is not None
            assert final.status.state == TaskState.TASK_STATE_CANCELED

            recovery_task = Task(
                id="handler-acceptance-recovery",
                context_id="handler-acceptance-recovery-context",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
            old_context = ServerCallContext()
            old_lease = await ownership_one.acquire(
                recovery_task.id,
                recovery_task.context_id,
                "anonymous",
            )
            assert old_lease is not None
            from osa.runtimes.adk.a2a_task_store import bind_task_ownership

            bind_task_ownership(old_context, old_lease)
            await store_one.save(recovery_task, old_context)
            from datetime import UTC, datetime, timedelta

            async with engine_one.begin() as connection:
                await connection.execute(
                    update(ownership_one._table)  # noqa: SLF001 - expiry is acceptance setup
                    .where(ownership_one._table.c.task_id == recovery_task.id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            recovered = await handler_two.on_cancel_task(
                CancelTaskRequest(id=recovery_task.id),
                ServerCallContext(),
            )
            assert isinstance(recovered, Task)
            assert recovered.status.state == TaskState.TASK_STATE_CANCELED
            assert agent_two.call_count == 0

            canceled_owner_loss_task = Task(
                id="handler-acceptance-canceled-owner-loss",
                context_id="handler-acceptance-canceled-owner-loss-context",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
            canceled_owner_loss_lease = await ownership_one.acquire(
                canceled_owner_loss_task.id,
                canceled_owner_loss_task.context_id,
                "anonymous",
            )
            assert canceled_owner_loss_lease is not None
            canceled_owner_loss_context = ServerCallContext()
            bind_task_ownership(canceled_owner_loss_context, canceled_owner_loss_lease)
            await store_one.save(canceled_owner_loss_task, canceled_owner_loss_context)
            assert await ownership_one.request_cancel(canceled_owner_loss_task.id, "anonymous")
            async with engine_one.begin() as connection:
                await connection.execute(
                    update(ownership_one._table)  # noqa: SLF001 - expiry is acceptance setup
                    .where(ownership_one._table.c.task_id == canceled_owner_loss_task.id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            canceled_owner_loss_result = await handler_two.on_message_send(
                SendMessageRequest(
                    message=Message(
                        message_id="handler-acceptance-canceled-owner-loss-message",
                        role=Role.ROLE_USER,
                        task_id=canceled_owner_loss_task.id,
                        context_id=canceled_owner_loss_task.context_id,
                        parts=[Part(text="recover canceled abandoned task")],
                    ),
                ),
                ServerCallContext(),
            )
            assert isinstance(canceled_owner_loss_result, Task)
            assert canceled_owner_loss_result.status.state == TaskState.TASK_STATE_CANCELED
            assert agent_two.call_count == 0

            owner_loss_task = Task(
                id="handler-acceptance-owner-loss",
                context_id="handler-acceptance-owner-loss-context",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
            owner_loss_context = ServerCallContext()
            owner_loss_lease = await ownership_one.acquire(
                owner_loss_task.id,
                owner_loss_task.context_id,
                "anonymous",
            )
            assert owner_loss_lease is not None
            bind_task_ownership(owner_loss_context, owner_loss_lease)
            await store_one.save(owner_loss_task, owner_loss_context)
            async with engine_one.begin() as connection:
                await connection.execute(
                    update(ownership_one._table)  # noqa: SLF001 - expiry is acceptance setup
                    .where(ownership_one._table.c.task_id == owner_loss_task.id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )

            owner_loss_result = await handler_two.on_message_send(
                SendMessageRequest(
                    message=Message(
                        message_id="handler-acceptance-owner-loss-message",
                        role=Role.ROLE_USER,
                        task_id=owner_loss_task.id,
                        context_id=owner_loss_task.context_id,
                        parts=[Part(text="retry abandoned task")],
                    ),
                ),
                ServerCallContext(),
            )
            assert isinstance(owner_loss_result, Task)
            assert owner_loss_result.status.state == TaskState.TASK_STATE_FAILED
            assert "automatic replay is disabled" in owner_loss_result.status.message.parts[0].text
            assert agent_two.call_count == 0
            durable_owner_loss = await store_two.get(owner_loss_task.id, ServerCallContext())
            assert durable_owner_loss is not None
            assert durable_owner_loss.status.state == TaskState.TASK_STATE_FAILED
        finally:
            await handler_one.aclose()
            await handler_two.aclose()
            await engine_one.dispose()
            await engine_two.dispose()


class TestA2aPostgresProcessAcceptance:
    @pytest.mark.skipif(
        not os.environ.get("OSA_TEST_DATABASE_URL"),
        reason="OSA_TEST_DATABASE_URL not configured; PostgreSQL process acceptance skipped",
    )
    async def test_independent_process_handlers_share_lookup_cancel_and_recovery(self) -> None:
        """Separate processes coordinate an active task through PostgreSQL."""
        from a2a.server.tasks import DatabaseTaskStore
        from a2a.types import TaskState
        from sqlalchemy.ext.asyncio import create_async_engine

        from osa.runtimes.adk.a2a import _a2a_task_owner
        from osa.runtimes.adk.a2a_migrations import migrate_a2a_schema
        from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore
        from osa.runtimes.adk.a2a_task_store import FencedDatabaseTaskStore

        suffix = uuid4().hex[:12]
        task_table_name = f"osa_a2a_process_{suffix}"
        ownership_table_name = f"{task_table_name}_ownership"
        database_url = os.environ["OSA_TEST_DATABASE_URL"]
        setup_engine = create_async_engine(database_url, pool_pre_ping=True)
        setup_ownership = A2aTaskOwnershipStore(
            setup_engine,
            table_name=ownership_table_name,
            lease_seconds=5,
        )
        setup_sdk_store = DatabaseTaskStore(
            setup_engine,
            create_table=False,
            table_name=task_table_name,
            owner_resolver=_a2a_task_owner,
        )
        setup_task_store = FencedDatabaseTaskStore(setup_sdk_store, setup_ownership)
        await migrate_a2a_schema(
            setup_engine,
            task_table_name=task_table_name,
            task_store=setup_task_store,
            ownership_store=setup_ownership,
        )
        await setup_task_store.initialize()
        await setup_engine.dispose()

        process_context = mp.get_context("spawn")
        owner_commands = process_context.Queue()
        owner_results = process_context.Queue()
        owner_release = process_context.Event()
        observer_commands = process_context.Queue()
        observer_results = process_context.Queue()
        observer_release = process_context.Event()
        owner_process = process_context.Process(
            target=_a2a_process_worker,
            args=(
                "owner",
                database_url,
                task_table_name,
                ownership_table_name,
                owner_commands,
                owner_results,
                owner_release,
            ),
        )
        observer_process = process_context.Process(
            target=_a2a_process_worker,
            args=(
                "observer",
                database_url,
                task_table_name,
                ownership_table_name,
                observer_commands,
                observer_results,
                observer_release,
            ),
        )
        processes = [owner_process, observer_process]

        try:
            owner_process.start()
            observer_process.start()
            assert (await _read_process_result(owner_results))["event"] == "ready"
            assert (await _read_process_result(observer_results))["event"] == "ready"

            owner_commands.put({"op": "start"})
            started = await _read_process_result(owner_results)
            assert started["event"] == "started"
            task_id = str(started["task_id"])

            observer_commands.put({"op": "stream", "task_id": task_id})
            stream_started = await _read_process_result(observer_results)
            assert stream_started["event"] == "stream-started"

            observer_commands.put({"op": "lookup", "task_id": task_id})
            observed = await _read_process_result(observer_results)
            assert observed["event"] == "lookup"
            assert observed["state"] in {
                int(TaskState.TASK_STATE_SUBMITTED),
                int(TaskState.TASK_STATE_WORKING),
            }

            observer_commands.put({"op": "cancel", "task_id": task_id})
            await asyncio.sleep(0.4)
            assert observer_process.is_alive()
            try:
                unexpected_result = await asyncio.to_thread(observer_results.get, True, 0.2)
            except Empty:
                pass
            else:
                raise AssertionError(f"remote cancellation completed before owner release: {unexpected_result!r}")

            owner_release.set()
            observer_results_seen: dict[str, dict[str, Any]] = {}
            while {"cancelled", "streamed"} - observer_results_seen.keys():
                result = await _read_process_result(observer_results, timeout=15)
                observer_results_seen[str(result["event"])] = result
            canceled = observer_results_seen["cancelled"]
            streamed = observer_results_seen["streamed"]
            assert canceled["task_id"] == task_id
            assert canceled["state"] == int(TaskState.TASK_STATE_CANCELED)
            assert canceled["agent_calls"] == 0
            assert streamed["task_id"] == task_id
            assert streamed["event_types"] == ["Task", "TaskStatusUpdateEvent"]
            assert streamed["states"][-1] == int(TaskState.TASK_STATE_CANCELED)

            observer_commands.put({"op": "lookup", "task_id": task_id})
            final = await _read_process_result(observer_results)
            assert final["state"] == int(TaskState.TASK_STATE_CANCELED)

            owner_commands.put({"op": "shutdown"})
            observer_commands.put({"op": "shutdown"})
            owner_process.join(timeout=10)
            observer_process.join(timeout=10)
            assert owner_process.exitcode == 0
            assert observer_process.exitcode == 0

            crashed_commands = process_context.Queue()
            crashed_results = process_context.Queue()
            crashed_release = process_context.Event()
            crashed_process = process_context.Process(
                target=_a2a_process_worker,
                args=(
                    "crashed-owner",
                    database_url,
                    task_table_name,
                    ownership_table_name,
                    crashed_commands,
                    crashed_results,
                    crashed_release,
                ),
            )
            processes.append(crashed_process)
            crashed_process.start()
            assert (await _read_process_result(crashed_results))["event"] == "ready"
            crashed_commands.put({"op": "start"})
            crashed_started = await _read_process_result(crashed_results)
            assert crashed_started["event"] == "started"
            crashed_task_id = str(crashed_started["task_id"])
            crashed_process.terminate()
            crashed_process.join(timeout=10)
            assert not crashed_process.is_alive()

            await asyncio.sleep(6)
            observer_process = process_context.Process(
                target=_a2a_process_worker,
                args=(
                    "recovery-observer",
                    database_url,
                    task_table_name,
                    ownership_table_name,
                    observer_commands,
                    observer_results,
                    observer_release,
                ),
            )
            processes.append(observer_process)
            observer_process.start()
            assert (await _read_process_result(observer_results))["event"] == "ready"
            observer_commands.put({"op": "cancel", "task_id": crashed_task_id})
            recovered = await _read_process_result(observer_results, timeout=15)
            assert recovered["event"] == "cancelled"
            assert recovered["task_id"] == crashed_task_id
            assert recovered["state"] == int(TaskState.TASK_STATE_CANCELED)
            assert recovered["agent_calls"] == 0
        finally:
            owner_release.set()
            observer_release.set()
            for process, commands in (
                (owner_process, owner_commands),
                (observer_process, observer_commands),
            ):
                if process.is_alive():
                    commands.put({"op": "shutdown"})
            for process in processes:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)

    @pytest.mark.skipif(
        not os.environ.get("OSA_TEST_DATABASE_URL"),
        reason="OSA_TEST_DATABASE_URL not configured; PostgreSQL process acceptance skipped",
    )
    async def test_independent_process_workers_reject_late_events_and_replay_cursor(self) -> None:
        """Prove takeover fencing and resumable event reads across processes."""
        from a2a.server.tasks import DatabaseTaskStore
        from a2a.types import TaskState
        from sqlalchemy import update
        from sqlalchemy.ext.asyncio import create_async_engine

        from osa.runtimes.adk.a2a import _a2a_task_owner
        from osa.runtimes.adk.a2a_event_store import A2aTaskEventStore, event_table_name
        from osa.runtimes.adk.a2a_migrations import migrate_a2a_schema
        from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore
        from osa.runtimes.adk.a2a_task_store import FencedDatabaseTaskStore

        suffix = uuid4().hex[:12]
        task_table_name = f"osa_a2a_relay_{suffix}"
        ownership_table_name = f"{task_table_name}_ownership"
        database_url = os.environ["OSA_TEST_DATABASE_URL"]
        setup_engine = create_async_engine(database_url, pool_pre_ping=True)
        setup_ownership = A2aTaskOwnershipStore(
            setup_engine,
            table_name=ownership_table_name,
            lease_seconds=5,
        )
        setup_sdk_store = DatabaseTaskStore(
            setup_engine,
            create_table=False,
            table_name=task_table_name,
            owner_resolver=_a2a_task_owner,
        )
        setup_event_store = A2aTaskEventStore(
            setup_engine,
            table_name=event_table_name(task_table_name),
        )
        setup_task_store = FencedDatabaseTaskStore(setup_sdk_store, setup_ownership, setup_event_store)
        await migrate_a2a_schema(
            setup_engine,
            task_table_name=task_table_name,
            task_store=setup_task_store,
            ownership_store=setup_ownership,
            event_store=setup_event_store,
        )
        await setup_task_store.initialize()
        await setup_engine.dispose()

        process_context = mp.get_context("spawn")
        writer_a_commands = process_context.Queue()
        writer_a_results = process_context.Queue()
        writer_b_commands = process_context.Queue()
        writer_b_results = process_context.Queue()
        reader_commands = process_context.Queue()
        reader_results = process_context.Queue()
        worker_args = (database_url, task_table_name, ownership_table_name)
        writer_a = process_context.Process(
            target=_a2a_relay_process_worker,
            args=("writer-a", *worker_args, writer_a_commands, writer_a_results),
        )
        writer_b = process_context.Process(
            target=_a2a_relay_process_worker,
            args=("writer-b", *worker_args, writer_b_commands, writer_b_results),
        )
        reader = process_context.Process(
            target=_a2a_relay_process_worker,
            args=("reader", *worker_args, reader_commands, reader_results),
        )
        processes = [writer_a, writer_b, reader]
        task_id = f"relay-process-{suffix}"
        context_id = f"relay-context-{suffix}"
        scope_key = "tenant:relay-tenant:subject:relay-user"
        started_processes: list[Any] = []

        try:
            for process, results in (
                (writer_a, writer_a_results),
                (writer_b, writer_b_results),
                (reader, reader_results),
            ):
                process.start()
                started_processes.append(process)
                assert (await _read_process_result(results))["event"] == "ready"

            writer_a_commands.put(
                {"op": "acquire", "task_id": task_id, "context_id": context_id, "scope_key": scope_key}
            )
            first_lease = await _read_process_result(writer_a_results)
            assert first_lease == {"event": "acquired", "fence": 1}
            for kind, state in (("Task", "submitted"), ("TaskStatusUpdateEvent", "working")):
                writer_a_commands.put(
                    {
                        "op": "append",
                        "kind": kind,
                        "state": state,
                        "task_id": task_id,
                        "context_id": context_id,
                    }
                )
                appended = await _read_process_result(writer_a_results)
                assert appended["accepted"] is True

            from datetime import UTC, datetime, timedelta

            expiry_engine = create_async_engine(database_url, pool_pre_ping=True)
            async with expiry_engine.begin() as connection:
                await connection.execute(
                    update(setup_ownership._table)  # noqa: SLF001 - test controls lease expiry
                    .where(setup_ownership._table.c.task_id == task_id)
                    .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                )
            await expiry_engine.dispose()

            writer_b_commands.put(
                {"op": "acquire", "task_id": task_id, "context_id": context_id, "scope_key": scope_key}
            )
            second_lease = await _read_process_result(writer_b_results)
            assert second_lease == {"event": "acquired", "fence": 2}
            writer_b_commands.put(
                {
                    "op": "append",
                    "kind": "TaskStatusUpdateEvent",
                    "state": "completed",
                    "task_id": task_id,
                    "context_id": context_id,
                }
            )
            completed = await _read_process_result(writer_b_results)
            assert completed == {"event": "appended", "accepted": True, "sequence": 3}

            writer_a_commands.put(
                {
                    "op": "append",
                    "kind": "TaskStatusUpdateEvent",
                    "state": "failed",
                    "task_id": task_id,
                    "context_id": context_id,
                }
            )
            late = await _read_process_result(writer_a_results)
            assert late == {"event": "appended", "accepted": False, "sequence": None}

            reader_commands.put({"op": "stream", "task_id": task_id, "scope_key": scope_key})
            streamed = await _read_process_result(reader_results)
            assert streamed["event_types"] == ["Task", "TaskStatusUpdateEvent", "TaskStatusUpdateEvent"]
            assert streamed["states"][-1] == int(TaskState.TASK_STATE_COMPLETED)

            reader_commands.put({"op": "stream", "task_id": task_id, "scope_key": scope_key, "after_sequence": 2})
            replayed = await _read_process_result(reader_results)
            assert replayed["event_types"] == ["TaskStatusUpdateEvent"]
            assert replayed["states"] == [int(TaskState.TASK_STATE_COMPLETED)]

            writer_b_commands.put({"op": "release", "task_id": task_id, "state": "completed"})
            assert (await _read_process_result(writer_b_results))["event"] == "released"
        finally:
            for commands in (writer_a_commands, writer_b_commands, reader_commands):
                commands.put({"op": "shutdown"})
            for process in processes:
                if process.pid is None:
                    continue
                process.join(timeout=15)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)
            assert all(process.exitcode == 0 for process in started_processes)


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

    async def test_durable_streaming_is_explicit_and_requires_migrated_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The relay handler is review-gated and never enabled by default."""
        from fastapi import FastAPI

        from osa.runtimes.adk.a2a import (
            OsaA2aRequestHandler,
            _task_store_and_engine,
            attach_a2a_routes,
            close_a2a_task_store,
            initialize_a2a_task_store,
        )
        from osa.runtimes.adk.a2a_migrations import migrate_a2a_schema

        local_app = FastAPI()
        local_card = attach_a2a_routes(local_app, _make_agent("local-card"), url="http://test")
        assert local_card.capabilities.streaming is False
        await close_a2a_task_store(local_app)

        monkeypatch.setenv(
            "OSA_A2A_TASK_DATABASE_URL",
            f"sqlite+aiosqlite:///{tmp_path / 'durable-streaming.db'}",
        )
        monkeypatch.setenv("OSA_A2A_TASK_TABLE", "durable_streaming_tasks")
        durable_app = FastAPI()
        task_store, engine = _task_store_and_engine(durable_app)
        assert engine is not None
        await migrate_a2a_schema(
            engine,
            task_table_name="durable_streaming_tasks",
            task_store=task_store,
            ownership_store=durable_app.state.osa_a2a_ownership_store,
            event_store=durable_app.state.osa_a2a_event_store,
        )
        await initialize_a2a_task_store(durable_app)

        durable_card = attach_a2a_routes(
            durable_app,
            _make_agent("durable-card"),
            url="http://test",
            enable_durable_streaming=True,
        )
        assert durable_card.capabilities.streaming is True
        assert isinstance(durable_app.state.osa_a2a_handler, OsaA2aRequestHandler)
        await close_a2a_task_store(durable_app)

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
