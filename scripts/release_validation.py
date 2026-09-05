from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

MANIFESTS = (
    Path("pyproject.toml"),
    Path("generic-agent/pyproject.toml"),
    Path("runtimes/adk/pyproject.toml"),
    Path("control-plane/backend/pyproject.toml"),
)


def _manifest_version(path: Path) -> str:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"{path}: missing [project] table")
    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"{path}: missing project.version")
    return version


def _validate_changelog(changelog: str, version: str) -> None:
    """Require a properly cut release section (BI2).

    The heading must be the first section after ``## [Unreleased]``, which
    stops pre-release dev-milestone headings (which live lower in the file)
    from satisfying the version check by collision. ``[Unreleased]`` must be
    empty so entries cannot silently ride along with the release.
    """
    unreleased = re.search(r"^## \[Unreleased\][ \t]*$", changelog, re.MULTILINE)
    if unreleased is None:
        raise ValueError("CHANGELOG.md has no '## [Unreleased]' section")

    tail = changelog[unreleased.end() :]
    section = re.search(r"^## (.+)$", tail, re.MULTILINE)
    if section is None:
        raise ValueError(f"CHANGELOG.md has no dated release heading for {version}")

    unreleased_body = tail[: section.start()]
    if any(line.strip() for line in unreleased_body.splitlines()):
        raise ValueError(
            "CHANGELOG.md '## [Unreleased]' still has entries; move them into the release section before tagging"
        )

    heading = section.group(1).strip()
    if not re.fullmatch(rf"\[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}", heading):
        raise ValueError(
            f"CHANGELOG.md's first section after [Unreleased] must be '## [{version}] - YYYY-MM-DD'; "
            f"found '## {heading}'"
        )


def validate_release(root: Path, tag: str) -> str:
    versions = {path: _manifest_version(root / path) for path in MANIFESTS}
    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        details = ", ".join(f"{path}={version}" for path, version in versions.items())
        raise ValueError(f"workspace versions are not lockstep: {details}")

    version = unique_versions.pop()
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ValueError(f"release tag {tag!r} does not match workspace version {expected_tag!r}")

    _validate_changelog((root / "CHANGELOG.md").read_text(encoding="utf-8"), version)

    return version


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an Open Simple Agent release tag.")
    parser.add_argument("tag", help="Release tag, for example v0.2.0")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root")
    args = parser.parse_args()

    try:
        version = validate_release(args.root.resolve(), args.tag)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        parser.error(str(exc))

    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
