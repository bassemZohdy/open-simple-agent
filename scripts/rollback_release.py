"""Plan or execute a rollback of a mutable container image channel.

The release workflow publishes immutable per-version image tags plus one
mutable convenience channel (``latest``). This tool re-points a mutable
channel at an already-published digest without rebuilding: Cosign signatures
and attestations are digest-bound and survive the re-tag. Immutable version
tags are never created, moved, or deleted here.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys

COMPONENTS = ("runtime", "control-plane")
#: The only mutable channels this tool may move; immutable version tags are
#: never eligible even if this allowlist ever grows.
MUTABLE_CHANNELS = ("latest",)
DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
#: Anything shaped like a version tag (v0.2.0, 0.2.0) is immutable by policy.
_IMMUTABLE_TAG = re.compile(r"^(?:v?\d+\.\d+\.\d+|v\d.*)$")


def image_name(registry_base: str, component: str) -> str:
    """The GHCR image for a release component."""
    return f"{registry_base}-{component}"


def validate_plan(registry_base: str, component: str, channel: str, digest: str) -> list[str]:
    """Validate a rollback request and return the shell commands to run.

    Raises ``ValueError`` when any input violates the rollback policy.
    """
    if not registry_base or any(ch.isspace() for ch in registry_base):
        raise ValueError(f"invalid registry base: {registry_base!r}")
    if component not in COMPONENTS:
        raise ValueError(f"component must be one of {COMPONENTS}, got {component!r}")
    if channel not in MUTABLE_CHANNELS:
        raise ValueError(
            f"channel must be one of {MUTABLE_CHANNELS}, got {channel!r}; immutable version tags are never rolled back"
        )
    if _IMMUTABLE_TAG.match(channel):
        raise ValueError(f"refusing to move immutable version tag {channel!r}")
    if DIGEST_PATTERN.match(digest) is None:
        raise ValueError(f"digest must match sha256:<64 hex characters>, got {digest!r}")

    image = image_name(registry_base, component)
    return [
        f"docker buildx imagetools inspect {image}@{digest}",
        f"docker buildx imagetools create --tag {image}:{channel} {image}@{digest}",
    ]


def execute(commands: list[str]) -> None:
    """Run the planned commands, stopping at the first failure."""
    for command in commands:
        subprocess.run(shlex.split(command), check=True)  # noqa: S603 - commands are locally synthesized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Roll a mutable image channel back to a published digest.")
    parser.add_argument("--component", required=True, choices=COMPONENTS, help="Release component to roll back")
    parser.add_argument("--channel", default="latest", help="Mutable channel to move (default: latest)")
    parser.add_argument("--digest", required=True, help="Previously published digest, sha256:<hex>")
    parser.add_argument(
        "--registry-base",
        default=None,
        help="Registry base, for example ghcr.io/owner/repo (default: ghcr.io/<GITHUB_REPOSITORY lowercased>)",
    )
    parser.add_argument("--execute", action="store_true", help="Run the commands (default: print the plan only)")
    args = parser.parse_args(argv)

    raw_repository = os.environ.get("GITHUB_REPOSITORY", "")
    registry_base = args.registry_base or (f"ghcr.io/{raw_repository.lower()}" if raw_repository else "")
    try:
        commands = validate_plan(registry_base, args.component, args.channel, args.digest)
    except ValueError as exc:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error::{exc}", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    for command in commands:
        print(command)
    if args.execute:
        execute(commands)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
