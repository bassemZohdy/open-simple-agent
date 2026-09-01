"""Every example bundle loads, validates, and resolves cleanly (P3.4).

Examples are documentation: if one drifts from the schema or references a
missing resource, this test fails. The MCP example additionally references a
bundled stdio server so it is genuinely runnable offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from osa.generic_agent import (
    build_catalogs,
    load_bundle,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

_RUNNABLE_BUNDLES = sorted(path for path in EXAMPLES.iterdir() if path.is_dir() and (path / "agent.yaml").is_file())


def test_examples_exist() -> None:
    names = {path.name for path in _RUNNABLE_BUNDLES}
    assert {"smoke-bundle", "minimal", "native-tool", "memory", "mcp"} <= names


@pytest.mark.parametrize("bundle_dir", _RUNNABLE_BUNDLES, ids=lambda p: p.name)
def test_bundle_loads_and_resolves(bundle_dir: Path) -> None:
    bundle = load_bundle(bundle_dir)

    assert bundle.agent.metadata.name
    assert bundle.api_version == "osa/v1alpha1"

    # Reference resolution: every agent reference has a matching resource.
    catalogs = build_catalogs(bundle)
    spec = bundle.agent.spec
    if spec.model is not None:
        assert spec.model.ref in catalogs.model_catalog
    for tool_ref in spec.tools:
        assert tool_ref.ref in catalogs.tool_catalog
    for skill_ref in spec.skills:
        catalogs.skill_catalog.resolve(skill_ref.ref)
    for mcp_ref in spec.mcps:
        catalogs.mcp_catalog.resolve(mcp_ref.ref)
    if spec.memory.enabled and spec.memory.policy is not None:
        assert spec.memory.policy in catalogs.memory_policies


def test_native_tool_example_matches_builtin_implementation() -> None:
    """The native-tool example's calculator resolves to the builtin runtime tool."""
    from osa.generic_agent import CalculatorTool

    bundle = load_bundle(EXAMPLES / "native-tool")
    catalogs = build_catalogs(bundle)
    tool_ref = bundle.agent.spec.tools[0].ref
    definition = catalogs.tool_catalog.get_definition(tool_ref)
    assert definition.enabled is True
    assert CalculatorTool().name == tool_ref


def test_memory_example_policy_is_user_scoped() -> None:
    bundle = load_bundle(EXAMPLES / "memory")
    catalogs = build_catalogs(bundle)
    policy_ref = bundle.agent.spec.memory.policy
    assert policy_ref is not None
    policy = catalogs.memory_policies.resolve(policy_ref)
    assert policy.enabled is True
    assert policy.max_entries == 100


def test_mcp_example_server_is_importable() -> None:
    """The bundled stdio MCP server parses (offline sanity for the example)."""
    import ast

    source = (EXAMPLES / "mcp" / "server.py").read_text(encoding="utf-8")
    ast.parse(source)


def test_smoke_bundle_requires_fake_provider_optin(tmp_path: Path) -> None:
    """Documented behavior: the fake provider never runs without opt-in."""
    from osa.runtimes.adk.service import build_runtime

    bundle = load_bundle(EXAMPLES / "smoke-bundle")
    assert bundle.agent.metadata.name == "smoke-agent"
    # build_runtime with allow_fake_provider=False must fail deterministically
    # (model provider 'fake' has no adapter without explicit opt-in).
    import asyncio

    from osa.generic_agent import ModelConfigurationError

    with pytest.raises(ModelConfigurationError, match="provider 'fake'"):
        asyncio.run(build_runtime(EXAMPLES / "smoke-bundle", allow_fake_provider=False))
