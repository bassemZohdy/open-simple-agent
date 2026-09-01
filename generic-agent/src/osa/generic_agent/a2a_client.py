"""A2A client utilities (ADR-005).

Protocol-level helpers shared by all members: resolving an Agent Card from a
remote A2A agent, invoking a remote A2A agent with bounded timeout, and the
stable :class:`RemoteA2aError` mapping. Uses the pinned ``a2a-sdk`` 1.x
line; requires the optional ``a2a`` extra.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from osa.generic_agent.credentials import (
    CredentialResolutionError,
    ResolvedOutboundCredential,
    resolve_outbound_credential,
)
from osa.generic_agent.errors import OsaError

if TYPE_CHECKING:
    from osa.generic_agent.config import OutboundCredential, SecretReference
    from osa.generic_agent.secret import SecretResolver


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


async def resolve_agent_card(
    url: str,
    *,
    timeout_seconds: float = 10.0,
    credential: OutboundCredential | None = None,
    credential_ref: SecretReference | None = None,
    secret_resolver: SecretResolver | None = None,
) -> dict[str, Any]:
    """Fetch and summarize an Agent Card from a remote A2A agent.

    ``credential`` supports API-key, OAuth 2.0 client-credentials, and mTLS
    adapters.  ``credential_ref`` remains a compatibility shorthand for a
    bearer token reference.
    """
    _require_a2a_sdk()
    import contextlib

    import httpx
    from a2a.client import A2ACardResolver, A2AClientError, A2AClientTimeoutError

    client: httpx.AsyncClient | None = None
    try:
        material = await _resolve_http_credentials(credential, credential_ref, secret_resolver)
        client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers=material.headers,
            verify=material.verify if material.verify is not None else True,
            cert=material.cert,
        )
        resolver = A2ACardResolver(httpx_client=client, base_url=url.rstrip("/"))
        card = await resolver.get_agent_card()
    except A2AClientTimeoutError as exc:
        raise RemoteA2aError(url, "card resolution timed out", cause=exc) from exc
    except A2AClientError as exc:
        raise RemoteA2aError(url, f"card resolution failed: {exc}", cause=exc) from exc
    except CredentialResolutionError as exc:
        raise RemoteA2aError(url, str(exc), cause=exc) from exc
    except TimeoutError as exc:
        raise RemoteA2aError(url, "card resolution timed out", cause=exc) from exc
    except Exception as exc:
        raise RemoteA2aError(url, "card resolution failed", cause=exc) from exc
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()
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
    credential: OutboundCredential | None = None,
    credential_ref: SecretReference | None = None,
    secret_resolver: SecretResolver | None = None,
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
        material = await _resolve_http_credentials(credential, credential_ref, secret_resolver)
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            headers=material.headers,
            verify=material.verify if material.verify is not None else True,
            cert=material.cert,
        ) as http:
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


async def _resolve_http_credentials(
    credential: OutboundCredential | None,
    credential_ref: SecretReference | None,
    secret_resolver: SecretResolver | None,
) -> ResolvedOutboundCredential:
    """Resolve new credentials or the legacy bearer shorthand."""
    if credential is not None and credential_ref is not None:
        raise CredentialResolutionError("a2a", "credential and credential_ref cannot both be configured")
    if credential is not None:
        return await resolve_outbound_credential(credential, secret_resolver)
    if credential_ref is None:
        return ResolvedOutboundCredential()
    if secret_resolver is None:
        raise CredentialResolutionError("bearer", "a secret resolver is required")
    value = secret_resolver.resolve(credential_ref)
    if not value:
        raise CredentialResolutionError("bearer", "secret resolved to an empty value")
    if any(character in value for character in "\r\n"):
        raise CredentialResolutionError("bearer", "resolved value contains invalid header characters")
    return ResolvedOutboundCredential(headers={"Authorization": f"Bearer {value}"})
