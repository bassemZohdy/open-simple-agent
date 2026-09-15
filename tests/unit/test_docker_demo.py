"""Static contract checks for the end-user Docker demo bundle."""

from pathlib import Path

import yaml

DEMO = Path(__file__).resolve().parents[2] / "examples" / "docker-demo"


def test_docker_demo_bundle_has_required_services_and_seed_contract() -> None:
    compose = yaml.safe_load((DEMO / "docker-compose.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"control-plane", "control-panel", "seed"}
    control_plane = compose["services"]["control-plane"]
    assert control_plane["environment"]["OSA_DEPLOY_PORT"] == "${OSA_RUNTIME_PORT:-8081}"
    assert control_plane["environment"]["OSA_DEPLOY_INVOKE_URL_TEMPLATE"] == (\n        "http://localhost:${OSA_RUNTIME_PORT:-8081}"\n    )
    assert control_plane["environment"]["OSA_ALLOW_FAKE_PROVIDER"] == "1"
    assert "127.0.0.1:${OSA_RUNTIME_PORT:-8081}:${OSA_RUNTIME_PORT:-8081}" in control_plane["ports"]

    panel = compose["services"]["control-panel"]
    assert panel["environment"]["OSA_API_BASE_URL"] == "http://localhost:${OSA_CONTROL_PLANE_PORT:-8000}"
    assert "127.0.0.1:${OSA_CONTROL_PANEL_PORT:-8080}:8080" in panel["ports"]

    seed = (DEMO / "seed.sh").read_text(encoding="utf-8")
    assert "docker-demo-agent" in seed
    assert "/resources/Model" in seed
    assert "/agents/" in seed
    assert "/deploy" in seed


def test_docker_demo_docs_explain_deterministic_output_and_reset() -> None:
    docs = (DEMO / "README.md").read_text(encoding="utf-8")
    assert "DEMO RESPONSE" in docs
    assert "docker compose down --remove-orphans" in docs
    assert "No Python, Node.js, or source checkout" in docs
