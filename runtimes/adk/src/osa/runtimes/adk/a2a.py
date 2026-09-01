"""A2A support for the ADK runtime (ADR-005).

Built on the pinned ``a2a-sdk`` 1.x line (protobuf-typed protocol messages):

- :func:`build_agent_card` generates an A2A Agent Card from a validated
  ``AgentDefinition`` plus resolved skills.
- :func:`attach_a2a_routes` exposes an agent over A2A on the runtime API:
  ``message/send`` maps to ``GenericAdkAgent.invoke`` — one task per
  invocation, completed with the agent's output as an artifact, failures
  mapped to task failure states. The A2A context id is used as the OSA
  session id so multi-turn conversations respect session ownership.
- :func:`invoke_remote_agent` calls a remote A2A agent (managed or external)
  with a bounded timeout; :class:`RemoteA2aError` maps remote failures to a
  deterministic OSA error.

Requires the optional ``a2a`` extra (``osa-adk-runtime[a2a]``).
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

if TYPE_CHECKING:
    from osa.generic_agent import AgentDefinition, SkillDefinition

__all__ = [
    "A2aError",
    "A2aNotInstalledError",
    "A2A_WELL_KNOWN_PATH",
    "OsaA2aAgentExecutor",
    "RemoteA2aError",
    "attach_a2a_routes",
    "build_agent_card",
    "invoke_remote_agent",
    "resolve_agent_card",
]

A2A_WELL_KNOWN_PATH = "/.well-known/agent-card.json"
DEFAULT_INPUT_MODES = ["text/plain"]
DEFAULT_OUTPUT_MODES = ["text/plain"]

from osa.generic_agent.a2a_client import (  # noqa: E402, F401 - re-exported
    A2aError,
    A2aNotInstalledError,
    RemoteA2aError,
    invoke_remote_agent,
    resolve_agent_card,
)


def _require_a2a_sdk() -> None:
    if find_spec("a2a") is None or find_spec("a2a.server") is None:
        raise A2aNotInstalledError(
            "A2A server support requires the optional 'a2a-sdk[http-server]' "
            "dependency; install the 'osa-adk-runtime[a2a]' extra"
        )


def build_agent_card(
    definition: AgentDefinition,
    skills: list[SkillDefinition],
    url: str,
) -> Any:
    """Build an A2A Agent Card from a validated definition + resolved skills."""
    _require_a2a_sdk()
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

    agent_skills = [
        AgentSkill(
            id=skill.name,
            name=skill.name,
            description=skill.description or skill.name,
            tags=list(skill.tags) or [skill.name],
            examples=list(skill.input_metadata.values()) or None,
        )
        for skill in skills
    ]
    return AgentCard(
        name=definition.metadata.name,
        description=definition.spec.description or definition.metadata.description or definition.metadata.name,
        version=definition.metadata.version,
        supported_interfaces=[AgentInterface(url=url, protocol_binding="JSONRPC")],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=DEFAULT_INPUT_MODES,
        default_output_modes=DEFAULT_OUTPUT_MODES,
        skills=agent_skills,
    )


class OsaA2aAgentExecutor:
    """Maps A2A ``message/send`` to ``GenericAdkAgent.invoke``.

    Task lifecycle: submitted -> working -> completed (artifact carrying the
    agent output) or failed (deterministic error text). A2A context ids map
    to OSA sessions created on first contact, so multi-turn conversations
    keep one session per A2A conversation.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._sessions: dict[str, str] = {}

    async def execute(self, context: Any, event_queue: Any) -> None:
        from a2a.server.tasks import TaskUpdater
        from a2a.types import Part, Task, TaskState, TaskStatus

        user_text = context.get_user_input()
        context_id = context.context_id or str(uuid4())
        task_id = context.task_id or str(uuid4())
        updater = TaskUpdater(event_queue, task_id, context_id)

        # The 1.x consumer requires the initial Task event before any
        # status/artifact updates.
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )

        try:
            session_id = self._sessions.get(context_id)
            request = self._build_request(user_text, session_id)
            response = await self._agent.invoke(request)
            if response.error:
                await updater.failed(_failure_message(response.error))
                return
            self._sessions[context_id] = str(response.session_id)
            await updater.add_artifact(
                parts=[Part(text=response.output)],
                artifact_id=str(uuid4()),
                name="response",
            )
            await updater.complete()
        except Exception as exc:  # noqa: BLE001 - mapped into task failure
            await updater.failed(_failure_message(f"agent execution failed: {exc}"))

    @staticmethod
    def _build_request(user_text: str, session_id: str | None) -> Any:
        from osa.generic_agent import AgentRequest

        return AgentRequest(input=user_text, session_id=session_id)

    async def cancel(self, context: Any, event_queue: Any) -> None:
        from a2a.server.tasks import TaskUpdater

        context_id = context.context_id or str(uuid4())
        task_id = context.task_id or str(uuid4())
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.failed(_failure_message("cancellation is not supported for OSA agents"))


def _text_message(text: str) -> Any:
    from a2a.types import Part

    return Part(text=text)


def _failure_message(text: str) -> Any:
    from a2a.types import Message, Part, Role

    return Message(
        role=Role.ROLE_AGENT,
        message_id=str(uuid4()),
        parts=[Part(text=text)],
    )


def attach_a2a_routes(app: Any, agent: Any, url: str) -> Any:
    """Attach A2A JSON-RPC + Agent Card routes for ``agent`` to ``app``.

    The card URL is the A2A well-known path; JSON-RPC lives at ``/a2a``.
    """
    _require_a2a_sdk()
    from a2a.server import routes as a2a_routes
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore

    # OsaA2aAgentExecutor implements the executor surface; register it with
    # the SDK's ABC (defined lazily so the optional extra stays optional).
    AgentExecutor.register(OsaA2aAgentExecutor)

    # The interface URL is the client-facing JSON-RPC endpoint.
    interface_url = url.rstrip("/") + "/a2a"
    card = build_agent_card(agent.definition, agent.skills, interface_url)
    handler = DefaultRequestHandler(
        agent_executor=cast("AgentExecutor", OsaA2aAgentExecutor(agent)),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    a2a_routes.add_a2a_routes_to_fastapi(
        app,
        jsonrpc_routes=a2a_routes.create_jsonrpc_routes(handler, rpc_url="/a2a"),
        agent_card_routes=a2a_routes.create_agent_card_routes(card, card_url=A2A_WELL_KNOWN_PATH),
    )
    return card
