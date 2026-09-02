from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release_validation import MANIFESTS, validate_release


def _write_release_fixture(root: Path, *, version: str = "1.2.3", changelog_version: str | None = None) -> None:
    for manifest in MANIFESTS:
        path = root / manifest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'[project]\nname = "fixture"\nversion = "{version}"\n', encoding="utf-8")
    changelog = changelog_version or version
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{changelog}] - 2026-09-02\n\n### Added\n- Release fixture.\n",
        encoding="utf-8",
    )


def test_validate_release_accepts_lockstep_tag_and_changelog(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)

    assert validate_release(tmp_path, "v1.2.3") == "1.2.3"


def test_validate_release_rejects_tag_version_mismatch(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)

    with pytest.raises(ValueError, match="does not match workspace version"):
        validate_release(tmp_path, "v1.2.4")


def test_validate_release_rejects_manifest_drift(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    drifting_manifest = tmp_path / MANIFESTS[-1]
    drifting_manifest.write_text('[project]\nname = "fixture"\nversion = "9.9.9"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="workspace versions are not lockstep"):
        validate_release(tmp_path, "v1.2.3")


def test_validate_release_requires_dated_changelog_heading(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path, changelog_version="1.2.2")

    with pytest.raises(ValueError, match="no dated release heading"):
        validate_release(tmp_path, "v1.2.3")
