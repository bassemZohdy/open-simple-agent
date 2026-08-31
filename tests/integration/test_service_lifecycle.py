"""Runtime service lifecycle: bundle bootstrap, readiness, CLI config (P0.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from httpx import ASGITransport, AsyncClient

from osa.generic_agent import (
    EnvironmentSecretResolver,
    SecretResolutionError,
)
from osa.runtimes.adk.service import build_runtime, create_runtime_app


def _write_bundle(root: Path, *, credential: bool = False) -> Path:
    credential_yaml = ""
    if credential:
        credential_yaml = "  credential_ref:\n    source: env\n    key: OPENAI_API_KEY\n"
    (root / "agent.yaml").write_text(
        """
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: service-agent
spec:
  instruction: Help briefly.
  model:
    ref: default
  tools:
    - calculator
""",
        encoding="utf-8",
    )
    models = root / "models"
    models.mkdir(parents=True)
    (models / "default.yaml").write_text(
        f"""
apiVersion: osa/v1alpha1
kind: Model
spec:
  name: default
  provider: fake
  model_id: fake-model
  is_default: true
{credential_yaml}""",
        encoding="utf-8",
    )
    tools = root / "tools"
    tools.mkdir()
    (tools / "calculator.yaml").write_text(
        """
apiVersion: osa/v1alpha1
kind: Tool
spec:
  name: calculator
  description: Arithmetic
""",
        encoding="utf-8",
    )
    return root


def _fake_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    root.mkdir()
    return _write_bundle(root)


class TestBuildRuntime:
    async def test_bundle_bootstrap_builds_ready_runtime(self, tmp_path: Path) -> None:
        runtime, agent = await build_runtime(_fake_bundle(tmp_path), allow_fake_provider=True)
        try:
            assert agent.metadata.name == "service-agent"
            assert agent.tools == ["calculator"]
            assert agent.metadata.version
        finally:
            await runtime.shutdown()

    async def test_fake_provider_requires_explicit_opt_in(self, tmp_path: Path) -> None:
        from osa.generic_agent import ModelConfigurationError

        with pytest.raises(ModelConfigurationError, match="provider 'fake'"):
            await build_runtime(_fake_bundle(tmp_path), allow_fake_provider=False)

    async def test_unresolvable_secret_fails_before_runtime(self, tmp_path: Path) -> None:
        root = tmp_path / "bundle-secret"
        root.mkdir()
        _write_bundle(root, credential=True)
        import os

        os.environ.pop("OPENAI_API_KEY", None)
        with pytest.raises(SecretResolutionError):
            await build_runtime(root, secret_resolver=EnvironmentSecretResolver())

    async def test_resolvable_secret_allows_bootstrap(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "bundle-secret-ok"
        root.mkdir()
        _write_bundle(root, credential=True)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        runtime, agent = await build_runtime(root, allow_fake_provider=True)
        try:
            assert agent.metadata.name == "service-agent"
        finally:
            await runtime.shutdown()


class TestServiceApp:
    async def test_ready_and_invoke_via_bundle_app(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OSA_ALLOW_FAKE_PROVIDER", "1")
        app = create_runtime_app(_fake_bundle(tmp_path))
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            ready = await client.get("/health/ready")
            assert ready.status_code == 200

            invoked = await client.post("/v1/invoke", json={"input": "hello"})
            assert invoked.status_code == 200
            body = invoked.json()
            assert body["session_id"]
            assert "invocation_id" in body

            capabilities = await client.get("/v1/capabilities")
            assert capabilities.status_code == 200
            assert capabilities.json()["agent_name"] == "service-agent"
            assert capabilities.json()["tools"] == ["calculator"]
        # After shutdown the module state is reset.
        from osa.runtimes.adk import api as runtime_api

        assert runtime_api.get_agent() is None

    async def test_invalid_bundle_fails_startup_before_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OSA_ALLOW_FAKE_PROVIDER", raising=False)
        bundle = tmp_path / "broken"
        bundle.mkdir()
        (bundle / "agent.yaml").write_text(
            "apiVersion: osa/v1alpha1\nkind: Agent\nmetadata:\n  name: a\nspec:\n  model:\n    ref: ghost\n",
            encoding="utf-8",
        )
        app = create_runtime_app(bundle)

        from fastapi import FastAPI

        assert isinstance(app, FastAPI)
        with pytest.raises(Exception) as excinfo:  # noqa: PT011 - lifespan failure path
            async with app.router.lifespan_context(app):
                pass
        assert "ghost" in str(excinfo.value) or "unknown model" in str(excinfo.value)
