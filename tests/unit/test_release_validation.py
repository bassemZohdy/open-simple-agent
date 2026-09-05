from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.release_validation import MANIFESTS, validate_release

if TYPE_CHECKING:
    from pathlib import Path


def _write_release_fixture(
    root: Path,
    *,
    version: str = "1.2.3",
    changelog_version: str | None = None,
    changelog: str | None = None,
) -> None:
    for manifest in MANIFESTS:
        path = root / manifest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'[project]\nname = "fixture"\nversion = "{version}"\n', encoding="utf-8")
    if changelog is None:
        released = changelog_version or version
        changelog = f"# Changelog\n\n## [Unreleased]\n\n## [{released}] - 2026-09-02\n\n### Added\n- Release fixture.\n"
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


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


def test_validate_release_requires_dated_release_section(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path, changelog_version="1.2.2")

    with pytest.raises(ValueError, match=r"must be '## \[1\.2\.3\] - YYYY-MM-DD'"):
        validate_release(tmp_path, "v1.2.3")


def test_validate_release_rejects_missing_unreleased_section(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path, changelog="# Changelog\n\n## [1.2.3] - 2026-09-02\n\n### Added\n- x\n")

    with pytest.raises(ValueError, match=r"no '## \[Unreleased\]' section"):
        validate_release(tmp_path, "v1.2.3")


def test_validate_release_rejects_unmigrated_unreleased_entries(tmp_path: Path) -> None:
    _write_release_fixture(
        tmp_path,
        changelog=(
            "# Changelog\n\n## [Unreleased]\n\n### Added\n- Not shipped yet.\n\n"
            "## [1.2.3] - 2026-09-02\n\n### Added\n- Release fixture.\n"
        ),
    )

    with pytest.raises(ValueError, match=r"still has entries"):
        validate_release(tmp_path, "v1.2.3")


def test_validate_release_rejects_version_colliding_with_history_heading(tmp_path: Path) -> None:
    """BI2: a version colliding with a historical heading must not pass.

    The real CHANGELOG carries pre-release dev-milestone headings
    (``[0.0.1]``..``[0.14.0]``) below ``[Unreleased]``. The check now pins the
    release heading to the FIRST section after ``[Unreleased]``, so bumping the
    manifests to a version that only matches one of those older headings fails.
    """
    _write_release_fixture(
        tmp_path,
        version="0.2.0",
        changelog=(
            "# Changelog\n\n## [Unreleased]\n\n"
            "## [1.2.3] - 2026-09-02\n\n### Added\n- Newer section.\n\n"
            "## [0.2.0] - 2025-08-29\n\n### Added\n- Old dev milestone.\n"
        ),
    )

    with pytest.raises(ValueError, match=r"first section after \[Unreleased\]"):
        validate_release(tmp_path, "v0.2.0")
