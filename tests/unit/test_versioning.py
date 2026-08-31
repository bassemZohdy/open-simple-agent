"""Version consistency across the workspace (P0.6).

All three member packages are released together in lockstep: the version fields
in every manifest must be identical, the installed distributions must report
that same release version, and the FastAPI application metadata must derive
from the installed package metadata rather than a hard-coded constant.
"""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

_MEMBERS = (
    "generic-agent",
    "runtimes/adk",
    "control-plane/backend",
)

_DISTRIBUTIONS = {
    "generic-agent": "osa-generic-agent",
    "runtimes/adk": "osa-adk-runtime",
    "control-plane/backend": "osa-control-plane",
}


def _manifest_version(project_dir: Path) -> str:
    with (project_dir / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


def test_all_manifests_share_one_release_version() -> None:
    versions = {name: _manifest_version(WORKSPACE_ROOT / name) for name in ("", *_MEMBERS)}
    assert len(set(versions.values())) == 1, versions


def test_installed_distributions_report_the_release_version() -> None:
    release = _manifest_version(WORKSPACE_ROOT)
    for member, distribution in _DISTRIBUTIONS.items():
        assert importlib.metadata.version(distribution) == release, member


def test_api_metadata_reports_installed_package_version() -> None:
    from osa.control_plane.backend.api import app as control_plane_app
    from osa.runtimes.adk.api import runtime_app

    assert control_plane_app.version == importlib.metadata.version("osa-control-plane")
    assert runtime_app.version == importlib.metadata.version("osa-adk-runtime")
