"""Opt-in real-provider acceptance for the P0.2 ADK function-calling path."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from osa.generic_agent import AgentRequest
from osa.runtimes.adk.service import build_runtime

if TYPE_CHECKING:
    from pathlib import Path

LIVE_PROVIDER_KEY = "OSA_LIVE_PROVIDER_API_KEY"
DEFAULT_MODEL = "openai/gpt-4o-mini"

pytestmark = pytest.mark.skipif(
    not os.environ.get(LIVE_PROVIDER_KEY),
    reason=f"{LIVE_PROVIDER_KEY} is not configured; live-provider acceptance is opt-in",
)


def _write_json_yaml(path: Path, payload: dict[str, object]) -> None:
    """Write JSON, which is also valid YAML, without interpolating secrets."""
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _live_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "live-provider-bundle"
    (root / "models").mkdir(parents=True)
    (root / "tools").mkdir()
    _write_json_yaml(
        root / "agent.yaml",
        {
            "apiVersion": "osa/v1alpha1",
            "kind": "Agent",
            "metadata": {"name": "live-provider-acceptance"},
            "spec": {
                "instruction": (
                    "Use the calculator tool for arithmetic. For this test, call the calculator for the requested "
                    "addition and reply with the numeric result."
                ),
                "model": {"ref": "live"},
                "tools": ["calculator"],
                "runtime": {"timeout_seconds": 45, "max_iterations": 4},
            },
        },
    )
    _write_json_yaml(
        root / "models" / "live.yaml",
        {
            "apiVersion": "osa/v1alpha1",
            "kind": "Model",
            "spec": {
                "name": "live",
                "provider": "litellm",
                "model_id": os.environ.get("OSA_LIVE_PROVIDER_MODEL", DEFAULT_MODEL),
                "credential_ref": {
                    "source": "env",
                    "key": LIVE_PROVIDER_KEY,
                    "env_var": LIVE_PROVIDER_KEY,
                },
                "capabilities": {"function_calling": True},
                "runtime_settings": {"temperature": 0},
            },
        },
    )
    _write_json_yaml(
        root / "tools" / "calculator.yaml",
        {
            "apiVersion": "osa/v1alpha1",
            "kind": "Tool",
            "spec": {"name": "calculator", "description": "Perform basic arithmetic."},
        },
    )
    return root


@pytest.mark.asyncio
async def test_live_provider_executes_p02_runner_and_native_tool(tmp_path: Path) -> None:
    """Exercise the real LiteLLM adapter through ADK native function calling."""
    runtime, agent = await build_runtime(_live_bundle(tmp_path))
    try:
        response = await agent.invoke(
            AgentRequest(input="What is 2 + 3? Use the calculator tool and reply with only 5.")
        )
        assert response.error is None, response.error
        assert response.output.strip()
        assert "5" in response.output
    finally:
        await runtime.shutdown()
