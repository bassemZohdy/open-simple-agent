"""A2A support for the ADK runtime (ADR-005).

Built on the pinned ``a2a-sdk`` 1.x line (protobuf-typed protocol messages):

- :func:`build_agent_card` generates an A2A Agent Card from a validated
  ``AgentDefinition`` plus resolved skills.
- :func:`attach_a2a_routes` exposes an agent over A2A on the runtime API:
  ``message/send`` maps to ``GenericAdkAgent.invoke`` — one task per
  invocation, completed with the agent's output as an artifact, failures
  mapped to task failure states. The A2A context id is used as the OSA
  session id so multi-turn conversations respect session ownership.
- When a database-backed task store is selected, a separate OSA ownership
  lease/fencing record serializes active execution and persists cancellation;
  the schema is provisioned by ``osa-a2a-migrate``.
- :func:`invoke_remote_agent` calls a remote A2A agent (managed or external)
  with a bounded timeout; :class:`RemoteA2aError` maps remote failures to a
  deterministic OSA error.

Requires the optional ``a2a`` extra (``osa-adk-runtime[a2a]``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import warnings
from datetime import UTC, datetime
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

if TYPE_CHECKING:
    from osa.generic_agent import AgentDefinition, AuthSettings, SkillDefinition

from osa.runtimes.adk.a2a_ownership import A2aTaskOwnershipStore, TaskOwnership, heartbeat_loop
from osa.runtimes.adk.a2a_task_store import FencedDatabaseTaskStore, bind_task_ownership

__all__ = [
    "A2aError",
    "A2aNotInstalledError",
    "A2A_WELL_KNOWN_PATH",
    "A2A_TASK_DATABASE_URL_ENV_VAR",
    "A2A_TASK_TABLE_ENV_VAR",
    "A2A_TASK_LEASE_SECONDS_ENV_VAR",
    "OsaA2aAgentExecutor",
    "RemoteA2aError",
    "attach_a2a_routes",
    "build_agent_card",
    "close_a2a_task_store",
    "invoke_remote_agent",
    "initialize_a2a_task_store",
    "resolve_agent_card",
]

A2A_WELL_KNOWN_PATH = "/.well-known/agent-card.json"
A2A_TASK_DATABASE_URL_ENV_VAR = "OSA_A2A_TASK_DATABASE_URL"
A2A_TASK_TABLE_ENV_VAR = "OSA_A2A_TASK_TABLE"
A2A_TASK_LEASE_SECONDS_ENV_VAR = "OSA_A2A_TASK_LEASE_SECONDS"
DEFAULT_A2A_TASK_TABLE = "osa_a2a_tasks"
DEFAULT_A2A_TASK_LEASE_SECONDS = 30
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
    auth_settings: AuthSettings | None = None,
) -> Any:
    """Build an A2A Agent Card from a definition, skills, and auth contract."""
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
    card = AgentCard(
        name=definition.metadata.name,
        description=definition.spec.description or definition.metadata.description or definition.metadata.name,
        version=definition.metadata.version,
        supported_interfaces=[AgentInterface(url=url, protocol_binding="JSONRPC")],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=DEFAULT_INPUT_MODES,
        default_output_modes=DEFAULT_OUTPUT_MODES,
        skills=agent_skills,
    )
    _add_authentication_contract(card, auth_settings)
    return card


def _add_authentication_contract(card: Any, auth_settings: AuthSettings | None) -> None:
    """Advertise and require the same bearer boundary enforced by FastAPI."""
    if auth_settings is None:
        return
    from osa.generic_agent import AuthMode

    protected = auth_settings.mode is AuthMode.REQUIRED or auth_settings.enforce_permissions
    if not protected:
        return

    from a2a.types import (
        HTTPAuthSecurityScheme,
        OpenIdConnectSecurityScheme,
        SecurityScheme,
    )

    scheme_name = "osa_oidc"
    if auth_settings.discovery_url is not None or (
        auth_settings.jwks_url is None and auth_settings.introspection_url is None
    ):
        issuer = auth_settings.issuer
        assert issuer is not None
        discovery_url = auth_settings.discovery_url or f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        scheme = SecurityScheme(
            open_id_connect_security_scheme=OpenIdConnectSecurityScheme(open_id_connect_url=discovery_url)
        )
    else:
        bearer_format = "access_token" if auth_settings.introspection_url is not None else "JWT"
        scheme = SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(
                scheme="bearer",
                bearer_format=bearer_format,
            )
        )
    card.security_schemes[scheme_name].CopyFrom(scheme)
    requirement = card.security_requirements.add()
    requirement.schemes[scheme_name].list.extend(auth_settings.required_scopes)


class OsaA2aAgentExecutor:
    """Maps A2A ``message/send`` to ``GenericAdkAgent.invoke``.

    Task lifecycle: submitted -> working -> completed (artifact carrying the
    agent output), failed (deterministic error text), or canceled. A2A context ids map
        to OSA sessions created on first contact, so multi-turn conversations
        keep one session per A2A conversation.
    """

    def __init__(
        self,
        agent: Any,
        *,
        ownership_store: A2aTaskOwnershipStore | None = None,
        task_store: Any | None = None,
    ) -> None:
        self._agent = agent
        self._sessions: dict[str, str] = {}
        self._ownership_store = ownership_store
        self._task_store = task_store

    async def execute(self, context: Any, event_queue: Any) -> None:
        from a2a.server.tasks import TaskUpdater
        from a2a.types import Part, Task, TaskState, TaskStatus

        user_text = context.get_user_input()
        context_id = context.context_id or str(uuid4())
        task_id = context.task_id or str(uuid4())
        scope_key = _a2a_task_owner(context)
        ownership_store = self._ownership_store
        ownership = await self._acquire_or_replay(context, event_queue, task_id, context_id, scope_key)
        if ownership_store is not None and ownership is None:
            return
        active_store = cast("A2aTaskOwnershipStore", ownership_store)

        if ownership is not None:
            bind_task_ownership(context.call_context, ownership)
        updater = TaskUpdater(event_queue, task_id, context_id)

        # The 1.x consumer requires the initial Task event before any
        # status/artifact updates. Publish it only after this worker owns the
        # durable fence; a losing worker must never create a late task row.
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )

        heartbeat_task: asyncio.Task[None] | None = None
        terminal_state = "released"
        if ownership is not None:
            heartbeat_task = asyncio.create_task(heartbeat_loop(active_store, ownership))
        try:
            if ownership is not None:
                stop_reason = await self._ownership_stop_reason(active_store, ownership)
                if stop_reason is not None:
                    if stop_reason == "canceled":
                        await updater.cancel()
                        terminal_state = "canceled"
                    return
            session_id = ownership.session_id if ownership is not None else self._sessions.get(context_id)
            request = self._build_request(user_text, session_id)
            response = await self._agent.invoke(request)
            if ownership is not None:
                stop_reason = await self._ownership_stop_reason(active_store, ownership)
                if stop_reason is not None:
                    if stop_reason == "canceled":
                        await updater.cancel()
                        terminal_state = "canceled"
                    return
            if response.error:
                await updater.failed(_failure_message(response.error))
                terminal_state = "failed"
                return
            if response.session_id is not None:
                self._sessions[context_id] = str(response.session_id)
                if ownership is not None and not await active_store.bind_session(ownership, str(response.session_id)):
                    return
            if ownership is not None:
                stop_reason = await self._ownership_stop_reason(active_store, ownership)
                if stop_reason is not None:
                    if stop_reason == "canceled":
                        await updater.cancel()
                        terminal_state = "canceled"
                    return
            await updater.add_artifact(
                parts=[Part(text=response.output)],
                artifact_id=str(uuid4()),
                name="response",
            )
            await updater.complete()
            terminal_state = "completed"
        except asyncio.CancelledError:
            if ownership is not None:
                current = await active_store.get(ownership.task_id, ownership.scope_key)
                if current is not None and current.cancel_requested:
                    terminal_state = "canceled"
            raise
        except Exception as exc:  # noqa: BLE001 - mapped into task failure
            if ownership is not None:
                stop_reason = await self._ownership_stop_reason(active_store, ownership)
                if stop_reason == "lost":
                    return
                if stop_reason == "canceled":
                    await updater.cancel()
                    terminal_state = "canceled"
                    return
            await updater.failed(_failure_message(f"agent execution failed: {exc}"))
            terminal_state = "failed"
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            if ownership is not None:
                if terminal_state == "released":
                    current = await active_store.get(ownership.task_id, ownership.scope_key)
                    if current is not None and current.cancel_requested:
                        terminal_state = "canceled"
                await active_store.release(ownership, terminal_state)

    @staticmethod
    async def _ownership_stop_reason(store: A2aTaskOwnershipStore, ownership: TaskOwnership) -> str | None:
        current = await store.get(ownership.task_id, ownership.scope_key)
        if (
            current is None
            or current.fence != ownership.fence
            or current.owner_id != store.owner_id
            or current.state != "running"
            or current.lease_until is None
            or current.lease_until <= datetime.now(UTC)
        ):
            return "lost"
        return "canceled" if current.cancel_requested else None

    async def _acquire_or_replay(
        self,
        context: Any,
        event_queue: Any,
        task_id: str,
        context_id: str,
        scope_key: str,
    ) -> TaskOwnership | None:
        """Claim a task or replay the terminal event produced by its owner."""
        if self._ownership_store is None:
            return None
        ownership = await self._ownership_store.acquire(task_id, context_id, scope_key)
        if ownership is not None:
            return ownership
        if self._task_store is None:
            return None

        from a2a.types import TaskState

        terminal_states = {
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_CANCELED,
        }
        while True:
            task = await self._task_store.get(task_id, context.call_context)
            if task is not None and task.status.state in terminal_states:
                await event_queue.enqueue_event(task)
                return None
            current = await self._ownership_store.get(task_id, scope_key)
            if current is None or current.state == "released":
                ownership = await self._ownership_store.acquire(task_id, context_id, scope_key)
                if ownership is not None:
                    return ownership
            elif current.state in {"completed", "failed", "canceled"}:
                if task is not None:
                    await event_queue.enqueue_event(task)
                return None
            await asyncio.sleep(0.25)

    @staticmethod
    def _build_request(user_text: str, session_id: str | None) -> Any:
        from osa.generic_agent import AgentRequest, current_principal

        principal = current_principal()
        if principal is None:
            return AgentRequest(input=user_text, session_id=session_id)
        metadata = {"caller_subject": principal.subject}
        if principal.tenant_id is not None:
            metadata["tenant_id"] = principal.tenant_id
        return AgentRequest(
            input=user_text,
            session_id=session_id,
            user_id=principal.subject,
            metadata=metadata,
        )

    async def cancel(self, context: Any, event_queue: Any) -> None:
        """Publish the protocol cancellation state for this invocation.

        The SDK cancels the producer task before calling this method. The
        updater event is therefore the single terminal transition emitted by
        the executor; late agent output cannot be published after cancellation.
        With durable ownership, the cancel request is persisted for the active
        worker and takeover path. Local cancellation publishes under the active
        ownership fence; a remote requester records the flag and the remaining
        cross-replica terminal-event wait is part of the distributed-runtime
        acceptance contract.
        """
        from a2a.server.tasks import TaskUpdater

        context_id = context.context_id or str(uuid4())
        task_id = context.task_id or str(uuid4())
        if self._ownership_store is not None:
            scope_key = _a2a_task_owner(context)
            await self._ownership_store.request_cancel(task_id, scope_key)
            current = await self._ownership_store.get(task_id, scope_key)
            if current is not None and current.owner_id == self._ownership_store.owner_id:
                # A local cancellation request may publish the terminal event
                # under the active worker's fence. A remote requester only
                # records the durable flag; the owner must publish the event.
                bind_task_ownership(context.call_context, current)
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.cancel()


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


def _a2a_task_owner(context: Any) -> str:
    """Resolve a task owner from the shared OSA identity boundary.

    The A2A SDK's default resolver only uses its protocol user name. OSA's
    bearer middleware already validated the subject and tenant, so use both
    when available to prevent a task lookup crossing tenant boundaries. The
    SDK context remains the fallback for unauthenticated or embedded callers.
    """
    from osa.generic_agent import current_principal

    principal = current_principal()
    if principal is not None:
        tenant = principal.tenant_id or "-"
        return f"tenant:{tenant}:subject:{principal.subject}"
    user = getattr(context, "user", None)
    user_name = getattr(user, "user_name", None)
    return str(user_name or "anonymous")


def _validate_task_table_name(table_name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
        raise ValueError(f"{A2A_TASK_TABLE_ENV_VAR} must be a simple SQL identifier")
    return table_name


def _task_lease_seconds() -> int:
    raw = os.environ.get(A2A_TASK_LEASE_SECONDS_ENV_VAR, str(DEFAULT_A2A_TASK_LEASE_SECONDS))
    try:
        lease_seconds = int(raw)
    except ValueError as exc:
        raise ValueError(f"{A2A_TASK_LEASE_SECONDS_ENV_VAR} must be an integer") from exc
    if lease_seconds < 5:
        raise ValueError(f"{A2A_TASK_LEASE_SECONDS_ENV_VAR} must be at least 5 seconds")
    return lease_seconds


def _task_store_and_engine(app: Any) -> tuple[Any, Any | None]:
    """Return the configured SDK task store and its optional owned engine."""
    existing_store = getattr(app.state, "osa_a2a_task_store", None)
    if existing_store is not None:
        return existing_store, getattr(app.state, "osa_a2a_task_engine", None)

    database_url = os.environ.get(A2A_TASK_DATABASE_URL_ENV_VAR)
    if not database_url:
        from a2a.server.tasks import InMemoryTaskStore

        store: Any = InMemoryTaskStore(owner_resolver=_a2a_task_owner)
        app.state.osa_a2a_task_store = store
        app.state.osa_a2a_task_engine = None
        app.state.osa_a2a_ownership_store = None
        return store, None

    _require_a2a_sdk()
    try:
        from a2a.server.tasks import DatabaseTaskStore
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError as exc:  # pragma: no cover - guarded by optional extras
        raise A2aNotInstalledError(
            "PostgreSQL-backed A2A task state requires the runtime postgres and a2a extras"
        ) from exc

    table_name = _validate_task_table_name(os.environ.get(A2A_TASK_TABLE_ENV_VAR, DEFAULT_A2A_TASK_TABLE))
    engine = create_async_engine(database_url, pool_pre_ping=True)
    # The SDK creates a dynamic ORM class for custom table names and emits a
    # harmless registry replacement warning because that class intentionally
    # reuses its standard model name.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="This declarative base already contains a class with the same class name",
        )
        sdk_store = DatabaseTaskStore(
            engine,
            create_table=False,
            table_name=table_name,
            owner_resolver=_a2a_task_owner,
        )
    ownership_store = A2aTaskOwnershipStore(
        engine,
        table_name=f"{table_name}_ownership",
        lease_seconds=_task_lease_seconds(),
    )
    store = FencedDatabaseTaskStore(sdk_store, ownership_store)
    app.state.osa_a2a_task_store = store
    app.state.osa_a2a_task_engine = engine
    app.state.osa_a2a_ownership_store = ownership_store
    return store, engine


async def initialize_a2a_task_store(app: Any) -> None:
    """Initialize configured durable task state before runtime readiness."""
    store, _ = _task_store_and_engine(app)
    initialize = getattr(store, "initialize", None)
    if initialize is not None:
        await initialize()
    ownership_store = getattr(app.state, "osa_a2a_ownership_store", None)
    if ownership_store is not None:
        from osa.runtimes.adk.a2a_migrations import ensure_a2a_schema

        task_table_name = os.environ.get(A2A_TASK_TABLE_ENV_VAR, DEFAULT_A2A_TASK_TABLE)
        await ensure_a2a_schema(
            app.state.osa_a2a_task_engine,
            task_table_name=task_table_name,
            ownership_store=ownership_store,
        )


async def close_a2a_task_store(app: Any) -> None:
    """Drain the handler and dispose its database engine, if any.

    The SDK's request handler owns the process-local active-task registry and
    its producer/consumer tasks.  Draining that handler before disposing the
    SQLAlchemy engine prevents shutdown from leaving work that can still try
    to persist through a closed database connection.
    """
    handler = getattr(app.state, "osa_a2a_handler", None)
    close_handler = getattr(handler, "aclose", None)
    if close_handler is not None:
        await close_handler()

    engine = getattr(app.state, "osa_a2a_task_engine", None)
    if engine is not None:
        await engine.dispose()
    app.state.osa_a2a_handler = None
    app.state.osa_a2a_task_store = None
    app.state.osa_a2a_task_engine = None
    app.state.osa_a2a_ownership_store = None


def attach_a2a_routes(
    app: Any,
    agent: Any,
    url: str,
    *,
    auth_settings: AuthSettings | None = None,
) -> Any:
    """Attach A2A JSON-RPC + Agent Card routes for ``agent`` to ``app``.

    The card URL is the A2A well-known path; JSON-RPC lives at ``/a2a``.
    """
    _require_a2a_sdk()
    from a2a.server import routes as a2a_routes
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.request_handlers import DefaultRequestHandler

    # OsaA2aAgentExecutor implements the executor surface; register it with
    # the SDK's ABC (defined lazily so the optional extra stays optional).
    AgentExecutor.register(OsaA2aAgentExecutor)

    # The interface URL is the client-facing JSON-RPC endpoint.
    interface_url = url.rstrip("/") + "/a2a"
    card = build_agent_card(agent.definition, agent.skills, interface_url, auth_settings=auth_settings)
    task_store, _ = _task_store_and_engine(app)
    handler = DefaultRequestHandler(
        agent_executor=cast(
            "AgentExecutor",
            OsaA2aAgentExecutor(
                agent,
                ownership_store=getattr(app.state, "osa_a2a_ownership_store", None),
                task_store=task_store,
            ),
        ),
        task_store=task_store,
        agent_card=card,
    )
    app.state.osa_a2a_handler = handler
    a2a_routes.add_a2a_routes_to_fastapi(
        app,
        jsonrpc_routes=a2a_routes.create_jsonrpc_routes(handler, rpc_url="/a2a"),
        agent_card_routes=a2a_routes.create_agent_card_routes(card, card_url=A2A_WELL_KNOWN_PATH),
    )
    return card
