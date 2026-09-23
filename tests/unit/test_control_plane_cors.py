"""Browser CORS for the Control Plane remains explicit and origin-scoped."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from osa.control_plane.backend.api import _allowed_origins_from_env
from osa.control_plane.backend.service import create_control_plane_app


def test_allowed_origins_parse_csv() -> None:
    assert _allowed_origins_from_env({}) == []
    assert _allowed_origins_from_env(
        {"OSA_CONTROL_PLANE_ALLOWED_ORIGINS": " https://one.example.test, https://two.example.test, "}
    ) == ["https://one.example.test", "https://two.example.test"]


@pytest.mark.asyncio
async def test_configured_origin_supports_requests_and_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSA_CONTROL_PLANE_ALLOWED_ORIGINS", "https://panel.example.test")
    app = create_control_plane_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.example.test") as client:
        response = await client.get("/agents", headers={"Origin": "https://panel.example.test"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://panel.example.test"

        preflight = await client.options(
            "/agents",
            headers={
                "Origin": "https://panel.example.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://panel.example.test"

        other_origin = await client.get("/agents", headers={"Origin": "https://other.example.test"})
        assert "access-control-allow-origin" not in other_origin.headers


@pytest.mark.asyncio
async def test_unconfigured_origin_has_no_cors_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSA_CONTROL_PLANE_ALLOWED_ORIGINS", raising=False)
    app = create_control_plane_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.example.test") as client:
        response = await client.get("/agents", headers={"Origin": "https://panel.example.test"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
