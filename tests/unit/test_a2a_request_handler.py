"""Tests for the explicitly gated durable A2A request-handler adapter."""

from __future__ import annotations

import json
from importlib.util import find_spec
from typing import Any, cast

import pytest

pytestmark = pytest.mark.skipif(find_spec("a2a") is None, reason="a2a extra is not installed")


class _ActiveTaskRegistry:
    def __init__(self, active: object | None) -> None:
        self.active = active

    async def get(self, task_id: str) -> object | None:
        del task_id
        return self.active


class _TaskStore:
    def __init__(self, task: object | None) -> None:
        self.task = task
        self.get_calls: list[str] = []

    async def get(self, task_id: str, context: object) -> object | None:
        del context
        self.get_calls.append(task_id)
        return self.task


class _OwnershipStore:
    def __init__(self, ownership: object | None) -> None:
        self.ownership = ownership
        self.get_calls: list[tuple[str, str]] = []

    async def get(self, task_id: str, scope_key: str) -> object | None:
        self.get_calls.append((task_id, scope_key))
        return self.ownership


class _Relay:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    async def stream(self, **kwargs: object) -> Any:
        self.calls.append(kwargs)
        for event in self.events:
            yield event


class _Delegate:
    def __init__(self, active: object | None) -> None:
        self._active_task_registry = _ActiveTaskRegistry(active)
        self.events: list[object] = []
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def on_get_task(self, params: object, context: object) -> str:
        del params, context
        return "delegated"

    async def on_message_send_stream(self, params: object, context: object) -> Any:
        del params, context
        for event in self.events:
            yield event

    async def on_subscribe_to_task(self, params: object, context: object) -> Any:
        del params, context
        for event in self.events:
            yield event


def _task() -> Any:
    from a2a.types import Task

    return Task(id="task-1", context_id="context-1")


def _send_params() -> Any:
    from a2a.types import Message, Part, Role, SendMessageRequest

    return SendMessageRequest(
        message=Message(
            message_id="message-1",
            context_id="context-1",
            task_id="task-1",
            role=Role.ROLE_USER,
            parts=[Part(text="hello")],
        )
    )


def _subscribe_params() -> Any:
    from a2a.types import SubscribeToTaskRequest

    return SubscribeToTaskRequest(id="task-1")


@pytest.mark.asyncio
async def test_remote_message_stream_uses_durable_relay_without_starting_local_work() -> None:
    from a2a.server.context import ServerCallContext

    from osa.runtimes.adk.a2a import OsaA2aRequestHandler

    relay_event = _task()
    delegate = _Delegate(active=None)
    relay = _Relay([relay_event])
    handler = OsaA2aRequestHandler(
        delegate,
        event_relay=relay,
        ownership_store=_OwnershipStore(object()),
        task_store=_TaskStore(_task()),
    )

    events = [event async for event in handler.on_message_send_stream(_send_params(), ServerCallContext())]

    assert events == [relay_event]
    assert relay.calls == [{"scope_key": "anonymous", "task_id": "task-1"}]
    assert delegate.closed is False


@pytest.mark.asyncio
async def test_local_active_task_stays_on_sdk_handler() -> None:
    from a2a.server.context import ServerCallContext

    from osa.runtimes.adk.a2a import OsaA2aRequestHandler

    delegate_event = object()
    delegate = _Delegate(active=object())
    delegate.events = [delegate_event]
    relay = _Relay([object()])
    handler = OsaA2aRequestHandler(
        delegate,
        event_relay=relay,
        ownership_store=_OwnershipStore(object()),
        task_store=_TaskStore(_task()),
    )

    events = [event async for event in handler.on_subscribe_to_task(_subscribe_params(), ServerCallContext())]

    assert events == [delegate_event]
    assert relay.calls == []


@pytest.mark.asyncio
async def test_missing_task_delegates_so_sdk_returns_task_not_found() -> None:
    from a2a.server.context import ServerCallContext

    from osa.runtimes.adk.a2a import OsaA2aRequestHandler

    delegate_event = object()
    delegate = _Delegate(active=None)
    delegate.events = [delegate_event]
    relay = _Relay([object()])
    handler = OsaA2aRequestHandler(
        delegate,
        event_relay=relay,
        ownership_store=_OwnershipStore(object()),
        task_store=_TaskStore(None),
    )

    events = [event async for event in handler.on_subscribe_to_task(_subscribe_params(), ServerCallContext())]

    assert events == [delegate_event]
    assert relay.calls == []


@pytest.mark.asyncio
async def test_non_streaming_methods_and_shutdown_delegate_to_sdk_handler() -> None:
    from a2a.server.context import ServerCallContext

    from osa.runtimes.adk.a2a import OsaA2aRequestHandler

    delegate = _Delegate(active=None)
    handler = OsaA2aRequestHandler(
        delegate,
        event_relay=_Relay([]),
        ownership_store=_OwnershipStore(object()),
        task_store=_TaskStore(_task()),
    )

    assert await handler.on_get_task(object(), ServerCallContext()) == "delegated"
    await handler.aclose()
    assert delegate.closed is True


@pytest.mark.asyncio
async def test_subscribe_route_serializes_events_from_the_durable_adapter() -> None:
    from a2a.server import routes
    from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from osa.generic_agent import AgentDefinition, AgentMetadataConfig, AgentSpec
    from osa.runtimes.adk.a2a import OsaA2aRequestHandler, build_agent_card

    delegate = _Delegate(active=None)
    relay = _Relay(
        [
            Task(id="task-1", context_id="context-1"),
            TaskStatusUpdateEvent(
                task_id="task-1",
                context_id="context-1",
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            ),
        ]
    )
    handler = OsaA2aRequestHandler(
        delegate,
        event_relay=relay,
        ownership_store=_OwnershipStore(object()),
        task_store=_TaskStore(_task()),
    )
    definition = AgentDefinition(
        metadata=AgentMetadataConfig(name="route-agent"),
        spec=AgentSpec(),
    )
    card = build_agent_card(definition, [], "http://test/a2a", streaming=True)
    app = FastAPI()
    routes.add_a2a_routes_to_fastapi(
        app,
        jsonrpc_routes=routes.create_jsonrpc_routes(cast("Any", handler), rpc_url="/a2a"),
        agent_card_routes=routes.create_agent_card_routes(card, card_url="/.well-known/agent-card.json"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "stream-1",
                "method": "SubscribeToTask",
                "params": {"id": "task-1"},
            },
            headers={"A2A-Version": "1.0"},
        )
        send_response = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "stream-2",
                "method": "SendStreamingMessage",
                "params": {
                    "message": {
                        "messageId": "message-1",
                        "contextId": "context-1",
                        "taskId": "task-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello"}],
                    }
                },
            },
            headers={"A2A-Version": "1.0"},
        )

    assert response.status_code == 200
    payloads = [
        json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert len(payloads) == 2
    assert all(payload["id"] == "stream-1" for payload in payloads)
    assert all("error" not in payload for payload in payloads)
    assert send_response.status_code == 200
    send_payloads = [
        json.loads(line.removeprefix("data: ")) for line in send_response.text.splitlines() if line.startswith("data: ")
    ]
    assert len(send_payloads) == 2
    assert all(payload["id"] == "stream-2" for payload in send_payloads)
    assert all("error" not in payload for payload in send_payloads)
    assert relay.calls == [
        {"scope_key": "anonymous", "task_id": "task-1"},
        {"scope_key": "anonymous", "task_id": "task-1"},
    ]
