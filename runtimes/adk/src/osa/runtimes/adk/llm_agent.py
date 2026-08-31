"""ADK LlmAgent and Runner construction from an AgentDefinition.

Builds the Google ADK objects for a definition. Two model paths exist:

- Live models (e.g. the ``litellm`` provider): ADK model instances built by
  :mod:`osa.runtimes.adk.model_adapter`, invoked natively through the Runner
  with ADK function calling.
- Deterministic ``ModelProvider`` instances (tests/offline): bridged by
  :class:`ProviderBackedLlm`, which flattens the ADK request into the prompt
  string the provider contract expects.
"""

from __future__ import annotations

import concurrent.futures
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.memory import InMemoryMemoryService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.function_tool import FunctionTool
from google.genai import types
from pydantic import ConfigDict

from osa.generic_agent import ModelProvider  # noqa: TC001 - pydantic field type
from osa.generic_agent.errors import ModelConfigurationError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from google.adk.models.llm_request import LlmRequest
    from google.adk.sessions.base_session_service import BaseSessionService

    from osa.generic_agent import AgentDefinition, Tool, ToolDefinition

AdkTool = Callable[..., Any] | BaseTool


def adk_name(name: str) -> str:
    """Sanitize a name into a valid ADK node name (a Python identifier)."""
    sanitized = re.sub(r"\W", "_", name)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


class ProviderBackedLlm(BaseLlm):
    """Deterministic ADK model backed by the OSA ``ModelProvider`` contract.

    Flattens the ADK request (system instruction plus conversation text) into
    the single prompt string ``ModelProvider.generate`` expects. Used for the
    fake provider path — deterministic, offline, never a production fallback.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: ModelProvider

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        prompt = _flatten_request(llm_request)
        response = await self.provider.generate(prompt=prompt, model_id=self.model)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=response.text)]))


def _flatten_request(llm_request: LlmRequest) -> str:
    texts: list[str] = []
    config = llm_request.config
    if config is not None and config.system_instruction:
        instruction = config.system_instruction
        if isinstance(instruction, str):
            texts.append(instruction)
        elif isinstance(instruction, types.Content) and instruction.parts:
            texts.extend(part.text for part in instruction.parts if part.text)
    for content in llm_request.contents:
        if content.parts:
            texts.extend(part.text for part in content.parts if part.text)
    return "\n\n".join(text for text in texts if text)


def _validate_tool_arguments(tool_name: str, schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Validate tool arguments against a JSON-schema subset.

    Checks ``required`` and property types (string/number/integer/boolean).
    Returns an error message, or None when the arguments are acceptable.
    """
    required = schema.get("required")
    if isinstance(required, list):
        missing = [str(field) for field in required if field not in arguments]
        if missing:
            return f"Missing required parameter(s): {', '.join(missing)}"
    properties = schema.get("properties")
    type_map: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    if isinstance(properties, dict):
        for name, spec in properties.items():
            if name not in arguments or not isinstance(spec, dict):
                continue
            expected = type_map.get(str(spec.get("type")))
            if (
                expected is not None
                and not isinstance(arguments[name], bool)
                and not isinstance(arguments[name], expected)
            ):
                return f"Parameter '{name}' must be of type {spec.get('type')}"
    return None


def _execute_bounded(tool: Tool, timeout_seconds: float | None, **kwargs: Any) -> dict[str, Any]:
    """Execute a tool synchronously, enforcing its configured timeout.

    Runs inside the ADK tool loop (a worker thread), so a timeout returns an
    error payload for the model instead of raising into the event loop.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(tool.execute, **kwargs)
        try:
            result = future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            return {
                "success": False,
                "output": "",
                "error": f"Tool '{tool.name}' timed out after {timeout_seconds}s",
            }
    finally:
        executor.shutdown(wait=False)
    return {"success": result.success, "output": result.output, "error": result.error}


class OsaFunctionTool(FunctionTool):
    """ADK function tool carrying the OSA ``ToolDefinition`` contract.

    The parameter declaration is generated from
    ``ToolDefinition.capabilities[0].parameters_schema`` when present (the
    declared contract wins over signature inference); arguments are validated
    against that schema before execution; the tool's timeout is enforced.
    """

    def __init__(self, tool: Tool, tool_definition: ToolDefinition | None) -> None:
        self._osa_tool = tool
        self._osa_timeout = tool_definition.timeout_seconds if tool_definition else None
        self._osa_schema = (
            tool_definition.capabilities[0].parameters_schema
            if tool_definition and tool_definition.capabilities
            else {}
        )
        super().__init__(func=self._make_function())

    def _make_function(self) -> Callable[..., dict[str, Any]]:
        tool = self._osa_tool
        schema = self._osa_schema
        timeout = self._osa_timeout

        def tool_fn(**kwargs: Any) -> dict[str, Any]:
            if schema:
                violation = _validate_tool_arguments(tool.name, schema, kwargs)
                if violation is not None:
                    return {"success": False, "output": "", "error": violation}
            return _execute_bounded(tool, timeout, **kwargs)

        tool_fn.__name__ = tool.name
        tool_fn.__doc__ = tool.description or f"Execute the {tool.name} tool."
        return tool_fn

    def _prepare_invocation_args(self, args: dict[str, Any], tool_context: Any) -> dict[str, Any]:
        """Forward the model's arguments unchanged.

        ADK's default filters arguments against the wrapped callable's
        signature; OSA tools take ``**kwargs`` and are validated against the
        declared ``parameters_schema`` in :meth:`_make_function` instead.
        """
        return dict(args)

    def _get_declaration(self) -> types.FunctionDeclaration | None:
        declaration = build_tool_declaration(self._osa_tool.name, self._osa_schema, self._osa_tool.description)
        if declaration is not None:
            return declaration
        return super()._get_declaration()


def build_tool_declaration(name: str, schema: dict[str, Any], description: str) -> types.FunctionDeclaration | None:
    """Build an ADK function declaration from a JSON schema, or None if empty."""
    if not schema:
        return None
    try:
        parameters = types.Schema.model_validate(schema)
    except Exception as exc:
        raise ModelConfigurationError(f"Tool '{name}' has an invalid parameters schema: {exc}") from exc
    return types.FunctionDeclaration(
        name=name,
        description=description or f"Execute the {name} tool.",
        parameters=parameters,
    )


def build_function_tools(
    tools: dict[str, Tool], tool_definitions: dict[str, ToolDefinition] | None = None
) -> list[AdkTool]:
    """Wrap resolved runtime tools as ADK function tools.

    Declarations come from the matching ``ToolDefinition.capabilities`` when
    available, otherwise ADK derives them from the wrapped callable.
    """
    definitions = tool_definitions or {}
    wrapped: list[AdkTool] = [OsaFunctionTool(tool, definitions.get(tool.name)) for tool in tools.values()]
    return wrapped


def build_llm_agent(
    definition: AgentDefinition,
    model: str | BaseLlm,
    tools: dict[str, Tool],
    tool_definitions: dict[str, ToolDefinition] | None = None,
    toolsets: list[Any] | None = None,
) -> LlmAgent:
    """Build an ADK ``LlmAgent`` from an AgentDefinition.

    ``model`` is either an ADK model identifier string or an ADK model
    instance built by a :class:`~osa.runtimes.adk.model_adapter.ModelAdapter`.
    ``toolsets`` (e.g. MCP toolsets) are resolved by ADK per invocation.
    The agent name is sanitized to satisfy ADK's identifier requirement
    (``customer-support`` becomes ``customer_support``).
    """
    all_tools: list[Any] = list(build_function_tools(tools, tool_definitions))
    all_tools.extend(toolsets or [])
    return LlmAgent(
        name=adk_name(definition.metadata.name),
        description=definition.spec.description or "",
        model=model,
        instruction=definition.spec.instruction or "",
        tools=all_tools,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def build_runner(
    llm_agent: LlmAgent,
    *,
    session_service: BaseSessionService | None = None,
    memory_service: InMemoryMemoryService | None = None,
) -> Runner:
    """Configure an ADK ``Runner`` with a session and memory service.

    Pass :class:`~osa.runtimes.adk.session_service.OsaAdkSessionService` to
    back sessions with the OSA ``SessionProvider``; the default in-memory
    services are for tests and throwaway runs.
    """
    return Runner(
        app_name=llm_agent.name,
        agent=llm_agent,
        session_service=session_service or InMemorySessionService(),
        memory_service=memory_service or InMemoryMemoryService(),
    )
