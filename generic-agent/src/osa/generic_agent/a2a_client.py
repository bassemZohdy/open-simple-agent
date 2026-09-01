"""A2A client utilities (ADR-005).

Protocol-level helpers shared by all members: resolving an Agent Card from a
remote A2A agent, invoking a remote A2A agent with bounded timeout, and the
stable :class:`RemoteA2aError` mapping. Uses the pinned ``a2a-sdk`` 1.x
line; requires the optional ``a2a`` extra.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any
from uuid import uuid4

from osa.generic_agent.errors import OsaError


class A2aError(OsaError):
    """Base error for A2A support."""

    code = "a2a_error"


class A2aNotInstalledError(A2aError):
    """A2A support requires the optional a2a-sdk dependency."""

    code = "a2a_not_installed"


class RemoteA2aError(A2aError):
    """A remote A2A agent could not be reached or failed the call."""

    code = "a2a_remote_failed"

    def __init__(self, url: str, message: str, cause: Exception | None = None) -> None:
        self.url = url
        self.cause = cause
        super().__init__(f"A2A agent at '{url}': {message}")


def _require_a2a_sdk() -> None:
    if find_spec("a2a") is None:
        raise A2aNotInstalledError(
            "A2A support requires the optional 'a2a-sdk' dependency; "
            "install the 'osa-generic-agent[a2a]' (or 'osa-adk-runtime[a2a]') extra"
        )


async def resolve_agent_card(url: str, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
    """Fetch and summarize an Agent Card from a remote A2A agent."""
    _require_a2a_sdk()
    import contextlib

    import httpx
    from a2a.client import A2ACardResolver, A2AClientError, A2AClientTimeoutError

    resolver = A2ACardResolver(
        httpx_client=httpx.AsyncClient(timeout=timeout_seconds),
        base_url=url.rstrip("/"),
    )
    try:
        card = await resolver.get_agent_card()
    except A2AClientTimeoutError as exc:
        raise RemoteA2aError(url, "card resolution timed out", cause=exc) from exc
    except A2AClientError as exc:
        raise RemoteA2aError(url, f"card resolution failed: {exc}", cause=exc) from exc
    finally:
        with contextlib.suppress(Exception):
            await resolver.httpx_client.aclose()
    return {
        "name": card.name,
        "description": card.description,
        "version": card.version,
        "url": url,
        "skills": [{"id": s.id, "name": s.name, "description": s.description} for s in card.skills],
    }


async def invoke_remote_agent(
    url: str,
    message: str,
    *,
    timeout_seconds: float = 30.0,
) -> str:
    """Send ``message`` to a remote A2A agent; return its response text.

    Consumes the task stream until completion and concatenates response
    artifact text; failures map to :class:`RemoteA2aError`.
    """
    _require_a2a_sdk()
    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.types import Message, Part, Role, SendMessageRequest, TaskState

    base_url = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as http:
            resolver = A2ACardResolver(httpx_client=http, base_url=base_url)
            card = await resolver.get_agent_card()
            config = ClientConfig(httpx_client=http, streaming=False)
            client = ClientFactory(config).create(card)
            request = SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    message_id=str(uuid4()),
                    parts=[Part(text=message)],
                )
            )
            output_parts: list[str] = []
            failure: str | None = None
            async for response in client.send_message(request):
                if response.task is not None:
                    status = response.task.status
                    if status.message is not None and status.message.parts:
                        first_text = getattr(status.message.parts[0], "text", None)
                        if status.state == TaskState.TASK_STATE_FAILED and first_text:
                            failure = first_text
                    for artifact in response.task.artifacts or []:
                        for part in artifact.parts or []:
                            part_text = getattr(part, "text", None)
                            if isinstance(part_text, str):
                                output_parts.append(part_text)
                elif response.message is not None:
                    for part in response.message.parts or []:
                        part_text = getattr(part, "text", None)
                        if isinstance(part_text, str):
                            output_parts.append(part_text)
            if failure is not None and not output_parts:
                raise RemoteA2aError(url, failure)
            if not output_parts:
                raise RemoteA2aError(url, "the remote agent returned no response text")
            return "\n".join(output_parts)
    except RemoteA2aError:
        raise
    except TimeoutError as exc:
        raise RemoteA2aError(url, "invocation timed out", cause=exc) from exc
    except Exception as exc:
        raise RemoteA2aError(url, f"invocation failed: {exc}", cause=exc) from exc
