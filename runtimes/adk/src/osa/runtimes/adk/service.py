"""Runtime service lifecycle: bundle bootstrap, app factory, and CLI.

``create_runtime_app`` builds a FastAPI application whose lifespan loads a
deployment bundle, validates every reference and secret, and initializes the
runtime before the service reports ready. ``main`` is the ``osa-runtime``
console entry point (SIGTERM triggers uvicorn's graceful shutdown, which runs
the lifespan shutdown and closes the runtime).
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import asynccontextmanager
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI

import osa.runtimes.adk.api as runtime_api
from osa.generic_agent import (
    BundleError,
    CalculatorTool,
    EnvironmentSecretResolver,
    FakeModelProvider,
    InMemoryProvider,
    SecretError,
    SecretResolver,
    SessionManager,
    Tool,
    ToolCatalog,
    build_catalogs,
    collect_secret_references,
    load_bundle,
)
from osa.runtimes.adk import AdkRuntime, GenericAdkAgent, default_registry

_TRUTHY_VALUES = {"1", "true", "yes", "on"}
DEFAULT_BUNDLE_ENV_VAR = "OSA_BUNDLE"
ALLOW_FAKE_PROVIDER_ENV_VAR = "OSA_ALLOW_FAKE_PROVIDER"

# Native tool implementations shipped with the runtime image. A bundle
# declares tools as definitions; an agent referencing a definition without an
# implementation fails fast at construction (never a silent no-op).
BUILTIN_TOOLS: tuple[Tool, ...] = (CalculatorTool(),)


def _register_builtin_implementations(tool_catalog: ToolCatalog) -> None:
    for tool in BUILTIN_TOOLS:
        if tool.name in tool_catalog:
            tool_catalog.register_tool(tool)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY_VALUES


async def build_runtime(
    bundle_path: str | Path,
    *,
    secret_resolver: SecretResolver | None = None,
    allow_fake_provider: bool = False,
) -> tuple[AdkRuntime, GenericAdkAgent]:
    """Load a bundle and build a ready runtime.

    All validation happens here, before service readiness: unknown resources,
    duplicates, and unresolvable secrets raise before any traffic is served.

    Args:
        bundle_path: Deployment bundle file or directory.
        secret_resolver: Defaults to :class:`EnvironmentSecretResolver`.
        allow_fake_provider: Opt-in for the deterministic ``fake`` model
            provider (smoke tests and local development). It is never a
            production fallback and must be enabled explicitly.
    """
    bundle = load_bundle(bundle_path)
    resolver = secret_resolver or EnvironmentSecretResolver()

    # Fail fast on unresolvable secrets; values are discarded, never stored.
    for reference in collect_secret_references(bundle):
        resolver.resolve(reference)

    catalogs = build_catalogs(bundle)
    _register_builtin_implementations(catalogs.tool_catalog)
    adapters = default_registry(
        fake_provider=FakeModelProvider() if allow_fake_provider else None,
        secret_resolver=resolver,
    )

    runtime = AdkRuntime(
        model_catalog=catalogs.model_catalog,
        tool_catalog=catalogs.tool_catalog,
        skill_catalog=catalogs.skill_catalog,
        memory_provider=InMemoryProvider(),
        session_provider=SessionManager(),
        model_adapters=adapters,
    )
    agent = await runtime.create(bundle.agent)
    return runtime, agent


def create_runtime_app(
    bundle_path: str | Path,
    *,
    secret_resolver: SecretResolver | None = None,
    allow_fake_provider: bool | None = None,
) -> FastAPI:
    """Build the runtime API app with a bundle-driven lifespan.

    Startup failure (invalid bundle, missing references, unresolvable
    secrets) aborts process start; readiness is only reported once the agent
    is initialized. Shutdown closes sessions and the runtime gracefully.
    """
    resolved_path = Path(bundle_path)
    allow_fake = _env_flag(ALLOW_FAKE_PROVIDER_ENV_VAR) if allow_fake_provider is None else allow_fake_provider

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            runtime, agent = await build_runtime(
                resolved_path,
                secret_resolver=secret_resolver,
                allow_fake_provider=allow_fake,
            )
        except (BundleError, SecretError) as exc:
            runtime_api.set_start_error(str(exc))
            raise
        runtime_api.set_runtime(runtime, agent)
        yield
        await runtime.shutdown()
        runtime_api.reset_runtime()

    try:
        version = metadata.version("osa-adk-runtime")
    except metadata.PackageNotFoundError:
        version = "0"
    app = FastAPI(title="Open Simple Agent Runtime", version=version, lifespan=lifespan)
    return runtime_api.configure_runtime_app(app)


def main(argv: list[str] | None = None) -> int:
    """``osa-runtime`` console entry point."""
    parser = argparse.ArgumentParser(
        prog="osa-runtime",
        description="Run an Open Simple Agent runtime service from a deployment bundle.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get(DEFAULT_BUNDLE_ENV_VAR),
        help=f"Path to a deployment bundle file or directory (env: {DEFAULT_BUNDLE_ENV_VAR})",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: %(default)s)")
    args = parser.parse_args(argv)

    if not args.config:
        parser.error(f"--config is required (or set {DEFAULT_BUNDLE_ENV_VAR})")

    app = create_runtime_app(args.config)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
