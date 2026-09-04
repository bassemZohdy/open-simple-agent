"""Tests for the mutable-channel image rollback planner."""

from __future__ import annotations

import shlex
import subprocess

import pytest
from scripts.rollback_release import execute, image_name, main, validate_plan

DIGEST = "sha256:" + "a" * 64
VALID = ("ghcr.io/owner/repo", "runtime", "latest", DIGEST)


def test_image_name_appends_component() -> None:
    assert image_name("ghcr.io/owner/repo", "control-plane") == "ghcr.io/owner/repo-control-plane"


def test_plan_inspects_then_retags_the_digest() -> None:
    commands = validate_plan(*VALID)
    assert commands == [
        f"docker buildx imagetools inspect ghcr.io/owner/repo-runtime@{DIGEST}",
        f"docker buildx imagetools create --tag ghcr.io/owner/repo-runtime:latest ghcr.io/owner/repo-runtime@{DIGEST}",
    ]


def test_plan_accepts_control_plane_component() -> None:
    commands = validate_plan("ghcr.io/owner/repo", "control-plane", "latest", DIGEST)
    assert any("control-plane:latest" in command for command in commands)


@pytest.mark.parametrize(
    ("component", "channel", "digest"),
    [
        ("frontend", "latest", DIGEST),
        ("runtime", "v0.2.0", DIGEST),
        ("runtime", "0.2.0", DIGEST),
        ("runtime", "stable-v1", DIGEST),
        ("runtime", "latest", "abc123"),
        ("runtime", "latest", "sha256:xyz"),
        ("", "latest", DIGEST),
    ],
)
def test_plan_rejects_policy_violations(component: str, channel: str, digest: str) -> None:
    with pytest.raises(ValueError, match=".+"):
        validate_plan("ghcr.io/owner/repo", component, channel, digest)


def test_execute_runs_each_planned_command(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = validate_plan(*VALID)
    executed: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> None:
        executed.append(args)

    monkeypatch.setattr(subprocess, "run", fake_run)
    execute(commands)
    assert [shlex.join(args) for args in executed] == commands


def test_main_prints_plan_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--component", "runtime", "--digest", DIGEST, "--registry-base", "ghcr.io/owner/repo"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "imagetools create --tag ghcr.io/owner/repo-runtime:latest" in captured.out


def test_main_reports_policy_errors(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    exit_code = main(
        [
            "--component",
            "runtime",
            "--digest",
            DIGEST,
            "--channel",
            "v0.2.0",
            "--registry-base",
            "ghcr.io/owner/repo",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "immutable version tag" in captured.err
