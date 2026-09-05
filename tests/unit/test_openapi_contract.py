"""OpenAPI schema contract (P3.4).

Validates both FastAPI apps produce spec-valid OpenAPI documents and that
every route documented in ``docs/API.md`` actually exists on the matching
application — the documentation cannot silently drift from the code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from openapi_spec_validator import validate

from osa.control_plane.backend.api import app as control_plane_app
from osa.runtimes.adk.api import runtime_app

if TYPE_CHECKING:
    from fastapi import FastAPI

DOCS = Path(__file__).resolve().parents[2] / "docs" / "API.md"

_ROUTE_ROW = re.compile(r"^\|\s*`([A-Z]+)`\s*\|\s*`(/[^`]*)`")


def _strip_suffix(path: str) -> str:
    """Drop documented query strings (``/logs?tail=`` -> ``/logs``)."""
    return path.split("?", 1)[0]


def _normalize_params(path: str) -> str:
    """Treat any ``{param}`` segment as a wildcard so docs shorthand (``{id}``)
    matches the schema name (``{external_id}``)."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def _documented_routes(section: str) -> set[tuple[str, str]]:
    """Extract (method, path) pairs from markdown route tables."""
    routes: set[tuple[str, str]] = set()
    for line in section.splitlines():
        match = _ROUTE_ROW.match(line)
        if match:
            routes.add((match.group(1).lower(), _normalize_params(_strip_suffix(match.group(2)))))
    return routes


def _schema_routes(app: FastAPI) -> set[tuple[str, str]]:
    schema = app.openapi()
    routes: set[tuple[str, str]] = set()
    for path, methods in schema["paths"].items():
        for method in methods:
            if method in {"get", "post", "put", "patch", "delete"}:
                routes.add((method, _normalize_params(path)))
    return routes


@pytest.mark.parametrize(
    ("app", "name"),
    [
        (control_plane_app, "control-plane"),
        (runtime_app, "runtime"),
    ],
    ids=["control-plane", "runtime"],
)
def test_openapi_document_is_spec_valid(app: FastAPI, name: str) -> None:  # noqa: TC002 - pytest param
    schema = app.openapi()
    validate(schema)
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"]


class TestDocumentedRoutesExist:
    """Every route documented in docs/API.md must exist in the app schema."""

    def test_control_plane_documented_routes(self) -> None:
        document = DOCS.read_text(encoding="utf-8")
        control_plane_section = document.split("## Runtime API")[0]
        documented = _documented_routes(control_plane_section)
        assert documented, "no routes parsed from the Control Plane section"
        missing = documented - _schema_routes(control_plane_app)
        assert not missing, f"documented routes missing from the Control Plane app: {sorted(missing)}"

    def test_runtime_documented_routes(self) -> None:
        document = DOCS.read_text(encoding="utf-8")
        runtime_section = "## Runtime API" + document.split("## Runtime API", 1)[1]
        documented = _documented_routes(runtime_section)
        assert documented, "no routes parsed from the Runtime section"
        missing = documented - _schema_routes(runtime_app)
        assert not missing, f"documented routes missing from the runtime app: {sorted(missing)}"


class TestSchemaRoutesAreDocumented:
    def test_no_undocumented_control_plane_routes(self) -> None:
        document = DOCS.read_text(encoding="utf-8")
        control_plane_section = document.split("## Runtime API")[0]
        documented = _documented_routes(control_plane_section)
        schema_routes = _schema_routes(control_plane_app)
        undocumented = {
            (method, path)
            for method, path in schema_routes
            if path not in {"/health/live", "/health/ready"} and (method, path) not in documented
        }
        assert not undocumented, (
            "schema routes missing from docs/API.md (document them or mark "
            f"them public infrastructure): {sorted(undocumented)}"
        )

    def test_no_undocumented_runtime_routes(self) -> None:
        document = DOCS.read_text(encoding="utf-8")
        runtime_section = "## Runtime API" + document.split("## Runtime API", 1)[1]
        documented = _documented_routes(runtime_section)
        schema_routes = _schema_routes(runtime_app)
        undocumented = {
            (method, path)
            for method, path in schema_routes
            if path not in {"/health/live", "/health/ready"} and (method, path) not in documented
        }
        assert not undocumented, (
            "runtime schema routes missing from docs/API.md (document them or mark "
            f"them public infrastructure): {sorted(undocumented)}"
        )
