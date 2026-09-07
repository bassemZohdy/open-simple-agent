"""Bundle bootstrap helpers for the LangGraph runtime.

The existing OSA HTTP surface is currently packaged with the ADK service.
This module provides the same fail-fast bundle construction for programmatic
LangGraph use while a framework-neutral runtime service surface is designed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from osa.generic_agent import (
    CalculatorTool,
    EnvironmentSecretResolver,
    FakeModelProvider,
    InMemoryProvider,
    Observability,
    SecretResolver,
    Tool,
    ToolCatalog,
    build_catalogs,
    collect_secret_references,
    load_bundle,
)
from osa.runtimes.langgraph import LangGraphRuntime, OsaLangGraphAgent
from osa.runtimes.langgraph.model_adapter import default_registry

if TYPE_CHECKING:
    from pathlib import Path


BUILTIN_TOOLS: tuple[Tool, ...] = (CalculatorTool(),)


def _register_builtin_implementations(tool_catalog: ToolCatalog) -> None:
    for tool in BUILTIN_TOOLS:
        if tool.name in tool_catalog:
            tool_catalog.register_tool(tool)


async def build_runtime(
    bundle_path: str | Path,
    *,
    secret_resolver: SecretResolver | None = None,
    allow_fake_provider: bool = False,
    observability: Observability | None = None,
) -> tuple[LangGraphRuntime, OsaLangGraphAgent]:
    """Load and validate a bundle, then create a ready LangGraph agent."""
    bundle = load_bundle(bundle_path)
    resolver = secret_resolver or EnvironmentSecretResolver()
    for reference in collect_secret_references(bundle):
        resolver.resolve(reference)

    catalogs = build_catalogs(bundle)
    _register_builtin_implementations(catalogs.tool_catalog)
    adapters = default_registry(
        fake_provider=FakeModelProvider() if allow_fake_provider else None,
        secret_resolver=resolver,
        observability=observability,
    )
    runtime = LangGraphRuntime(
        model_catalog=catalogs.model_catalog,
        tool_catalog=catalogs.tool_catalog,
        skill_catalog=catalogs.skill_catalog,
        mcp_catalog=catalogs.mcp_catalog,
        memory_policies=catalogs.memory_policies,
        memory_provider=InMemoryProvider(),
        model_adapters=adapters,
        secret_resolver=resolver,
        observability=observability,
    )
    agent = await runtime.create(bundle.agent)
    return runtime, agent


__all__ = ["build_runtime"]
