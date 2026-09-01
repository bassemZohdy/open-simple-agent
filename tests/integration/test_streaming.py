"""Streaming and replica-behavior tests (P2.4).

Covers the SSE event contract through the runtime API, client-cancellation
propagation into the ADK run, timeout bounding, cross-replica session
consistency through a shared SessionProvider, isolation (no event or session
leakage), and concurrent-stream load.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from osa.generic_agent import (
    AgentDefinition,
    AgentMetadataConfig,
    AgentRequest,
    AgentSpec,
    FakeModelProvider,
    ModelCatalog,
    ModelDefinition,
    ModelRef,
    SessionAccessError,
    SessionManager,
)
from osa.runtimes.adk import GenericAdkAgent
from tests.integration.test_native_function_calling import (
    ScriptedLlm,
    _final,
    scripted_registry,
)


def _catalog() -> ModelCatalog:
    catalog = ModelCatalog()
    catalog.register(ModelDefinition(name="default", provider="fake", model_id="fake-model", is_default=True))
    return catalog


def _agent(name: str, model: ScriptedLlm) -> GenericAdkAgent:
    return GenericAdkAgent(
        definition=AgentDefinition(
            metadata=AgentMetadataConfig(name=name),
            spec=AgentSpec(instruction="Help.", model=ModelRef(ref="default")),
        ),
        model_provider=FakeModelProvider(),
        model_catalog=_catalog(),
        model_adapters=scripted_registry(model),
    )


def _agent_definition(name: str, **spec_overrides: object) -> AgentDefinition:
    spec: dict[str, Any] = {"instruction": "Help.", "model": ModelRef(ref="default")}
    spec.update(spec_overrides)
    return AgentDefinition(metadata=AgentMetadataConfig(name=name), spec=AgentSpec(**spec))


class TestStreamInvokeContract:
    async def test_stream_emits_started_and_final_message(self) -> None:
        model = ScriptedLlm(model="fake-model", script=[_final("the full answer")])
        agent = _agent("stream-agent", model)

        events = [event async for event in agent.stream_invoke(AgentRequest(input="hi"))]

        assert [e.type for e in events] == ["osa.started", "osa.message"]
        assert events[0].invocation_id == events[1].invocation_id
        assert events[0].session_id == events[1].session_id
        assert events[1].seq == 1
        assert events[1].text == "the full answer"
        assert events[0].to_payload()["type"] == "osa.started"

    async def test_deltas_appear_before_final_message(self) -> None:
        """A model round emitting partial text then the final response maps to
        delta events followed by the terminal osa.message."""
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        class PartialThenFinal(ScriptedLlm):
            async def generate_content_async(self, llm_request: Any, stream: bool = False) -> Any:
                self._requests.append(llm_request)
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text="partial ")]),
                    partial=True,
                )
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="answer")]))

        agent = _agent("delta-agent", PartialThenFinal(model="fake-model"))

        events = [event async for event in agent.stream_invoke(AgentRequest(input="hi"))]

        types_seen = [e.type for e in events]
        assert types_seen == ["osa.started", "osa.message.delta", "osa.message"]
        assert events[1].text == "partial "
        # Parity: osa.message carries exactly what invoke would return (the
        # final runner response), while deltas carried the earlier rounds.
        assert events[2].text == "answer"

    async def test_error_stream_is_terminal(self) -> None:
        model = ScriptedLlm(model="fake-model", delay_seconds=30.0, script=[_final("never")])
        agent = GenericAdkAgent(
            definition=_agent_definition("timeout-stream", runtime={"timeout_seconds": 1}),
            model_provider=FakeModelProvider(),
            model_catalog=_catalog(),
            model_adapters=scripted_registry(model),
        )

        events = [event async for event in agent.stream_invoke(AgentRequest(input="hi"))]

        assert events[-1].type == "osa.error"
        assert "timed out" in events[-1].text


class TestCancellation:
    async def test_client_disconnect_cancels_the_run(self) -> None:
        """A client disconnect (task cancellation) cancels the model call.

        Mirrors what Starlette does to the response generator when the HTTP
        client goes away mid-stream.
        """
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class WatchfulModel(ScriptedLlm):
            async def generate_content_async(self, llm_request: Any, stream: bool = False) -> Any:
                started.set()
                try:
                    await asyncio.sleep(30)
                except (asyncio.CancelledError, GeneratorExit):
                    cancelled.set()
                    raise
                yield _final("never")

        agent = _agent("cancel-agent", WatchfulModel(model="fake-model"))

        stream = agent.stream_invoke(AgentRequest(input="hi"))
        assert (await anext(stream)).type == "osa.started"

        # Drive the generator until the model call is in flight, then cut it.
        consumer = asyncio.ensure_future(anext(stream))
        await asyncio.wait_for(started.wait(), timeout=5)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        await asyncio.wait_for(cancelled.wait(), timeout=5)
        assert cancelled.is_set()
        await stream.aclose()


class TestReplicaConsistency:
    async def test_shared_provider_context_survives_replica_switch(self) -> None:
        """Two replicas over one shared SessionProvider share conversations."""
        from osa.runtimes.adk import OsaAdkSessionService

        shared = SessionManager()
        first_provider = FakeModelProvider(response="ack-one")
        second_provider = FakeModelProvider(response="ack-two")
        replica_a = GenericAdkAgent(
            definition=_agent_definition("replica"),
            model_provider=first_provider,
            model_catalog=_catalog(),
            session_provider=shared,
        )
        replica_b = GenericAdkAgent(
            definition=_agent_definition("replica"),
            model_provider=second_provider,
            model_catalog=_catalog(),
            session_provider=shared,
        )
        # Keep the session service wired like production construction.
        from osa.runtimes.adk import build_runner

        for replica in (replica_a, replica_b):
            replica.runner = build_runner(replica.llm_agent, session_service=OsaAdkSessionService(shared))

        first = await replica_a.invoke(AgentRequest(input="remember-marker", user_id="u1"))
        second = await replica_b.invoke(AgentRequest(input="what marker?", user_id="u1", session_id=first.session_id))

        # Replica B saw replica A's turn through the shared session store.
        second_prompt = second_provider.calls[-1]["prompt"]
        assert isinstance(second_prompt, str)
        assert "remember-marker" in second_prompt
        assert second.session_id == first.session_id

    async def test_ownership_enforced_across_replicas(self) -> None:
        shared = SessionManager()
        replica_a = GenericAdkAgent(
            definition=_agent_definition("guarded"),
            model_provider=FakeModelProvider(response="ok"),
            model_catalog=_catalog(),
            session_provider=shared,
        )
        replica_b = GenericAdkAgent(
            definition=_agent_definition("guarded"),
            model_provider=FakeModelProvider(response="ok"),
            model_catalog=_catalog(),
            session_provider=shared,
        )
        first = await replica_a.invoke(AgentRequest(input="hi", user_id="alice"))

        with pytest.raises(SessionAccessError):
            await replica_b.invoke(AgentRequest(input="hi", session_id=first.session_id, user_id="mallory"))

    async def test_streams_do_not_leak_other_sessions(self) -> None:
        """Concurrent streams carry only their own invocation/session ids."""
        model_a = ScriptedLlm(model="fake-model", script=[_final("answer-a")])
        model_b = ScriptedLlm(model="fake-model", script=[_final("answer-b")])
        agent_a = _agent("iso-a", model_a)
        agent_b = _agent("iso-b", model_b)

        async def collect(agent: GenericAdkAgent, text: str) -> list[Any]:
            return [event async for event in agent.stream_invoke(AgentRequest(input=text))]

        events_a, events_b = await asyncio.gather(
            collect(agent_a, "q-a"),
            collect(agent_b, "q-b"),
        )

        ids_a = {e.invocation_id for e in events_a}
        ids_b = {e.invocation_id for e in events_b}
        assert ids_a.isdisjoint(ids_b)
        assert [e.text for e in events_a if e.type == "osa.message"] == ["answer-a"]
        assert [e.text for e in events_b if e.type == "osa.message"] == ["answer-b"]


class TestConcurrentLoad:
    async def test_many_concurrent_streams_complete(self) -> None:
        models = [ScriptedLlm(model="fake-model", script=[_final(f"out-{i}")]) for i in range(8)]
        agents = [_agent(f"load-{i}", models[i]) for i in range(8)]

        async def run(agent: GenericAdkAgent, index: int) -> str:
            events = [e async for e in agent.stream_invoke(AgentRequest(input=f"q-{index}"))]
            return events[-1].text

        outputs = await asyncio.gather(*(run(agents[i], i) for i in range(8)))
        assert outputs == [f"out-{i}" for i in range(8)]


class TestSseEndpoint:
    async def test_stream_endpoint_sends_sse_frames(self) -> None:
        from osa.runtimes.adk import api as runtime_api

        model = ScriptedLlm(model="fake-model", script=[_final("sse answer")])
        runtime_api.set_runtime(*_make_runtime_pair("sse-agent", model))
        try:
            async with AsyncClient(transport=ASGITransport(app=runtime_api.runtime_app), base_url="http://test") as c:
                response = await c.post(
                    "/v1/invoke/stream",
                    json={"input": "hello", "user_id": "sse-user"},
                )
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                body = response.text
                assert "event: osa.started" in body
                assert "event: osa.message" in body
                data_lines = [line for line in body.splitlines() if line.startswith("data: ")]
                payloads = [json.loads(line.removeprefix("data: ")) for line in data_lines]
                assert payloads[-1]["text"] == "sse answer"
                assert payloads[-1]["type"] == "osa.message"
        finally:
            runtime_api.reset_runtime()

    async def test_stream_endpoint_respects_auth(self) -> None:
        """When auth is required, the stream route is not anonymous."""
        from osa.runtimes.adk import api as runtime_api

        model = ScriptedLlm(model="fake-model", script=[_final("x")])
        runtime_api.set_runtime(*_make_runtime_pair("auth-stream-agent", model))
        try:
            settings = _auth_required_settings()
            app = runtime_api.configure_runtime_app(FastAPI(title="auth-test"), auth_settings=settings)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.post("/v1/invoke/stream", json={"input": "hello", "user_id": "u"})
                assert response.status_code == 401
        finally:
            runtime_api.reset_runtime()


def _make_runtime_pair(name: str, model: ScriptedLlm) -> tuple[Any, GenericAdkAgent]:
    from osa.runtimes.adk import AdkRuntime

    runtime = AdkRuntime(model_provider=FakeModelProvider(), model_catalog=_catalog())
    agent = GenericAdkAgent(
        definition=_agent_definition(name),
        model_provider=FakeModelProvider(),
        model_catalog=_catalog(),
        model_adapters=scripted_registry(model),
    )
    return runtime, agent


def _auth_required_settings() -> Any:
    from osa.generic_agent.auth import AuthMode, AuthSettings

    return AuthSettings(
        mode=AuthMode.REQUIRED,
        issuer="https://issuer.test",
        audience="osa",
        # No JWKS configured: any token fails validation, which is enough to
        # prove the stream route is not anonymous.
    )
