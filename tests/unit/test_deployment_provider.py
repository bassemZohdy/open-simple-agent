"""Deployment provider hardening tests (P1.5)."""

from __future__ import annotations

import sys

import pytest

from osa.control_plane.backend.deployment import (
    DeploymentSpec,
    DeploymentStatus,
    LocalDeploymentProvider,
)


def _sleep_command(seconds: int = 30) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _print_and_stay_command(message: str) -> list[str]:
    return [sys.executable, "-c", f"print({message!r}, flush=True); import time; time.sleep(30)"]


class TestBoundedLogs:
    async def test_logs_capture_stdout(self) -> None:
        provider = LocalDeploymentProvider()
        deployment = await provider.deploy(
            DeploymentSpec(agent_id="a", command=_print_and_stay_command("hello-from-runtime"))
        )
        assert deployment.status is DeploymentStatus.RUNNING
        try:
            import asyncio

            lines: list[str] = []
            for _ in range(30):
                lines = await provider.logs(deployment.deployment_id)
                if any("hello-from-runtime" in line for line in lines):
                    break
                await asyncio.sleep(0.1)
            assert any("hello-from-runtime" in line for line in lines)
        finally:
            await provider.stop(deployment.deployment_id)

    async def test_logs_are_bounded(self) -> None:
        provider = LocalDeploymentProvider(log_lines=5)
        flood = [
            sys.executable,
            "-c",
            "print('\\n'.join(str(i) for i in range(500)), flush=True); import time; time.sleep(30)",
        ]
        deployment = await provider.deploy(DeploymentSpec(agent_id="a", command=flood))
        try:
            import asyncio

            for _ in range(20):
                lines = await provider.logs(deployment.deployment_id, tail=1000)
                if len(lines) >= 5:
                    break
                await asyncio.sleep(0.1)
            assert len(lines) == 5
            assert lines[-1] == "499"
        finally:
            await provider.stop(deployment.deployment_id)

    async def test_logs_for_unknown_deployment(self) -> None:
        provider = LocalDeploymentProvider()
        with pytest.raises(KeyError):
            await provider.logs("no-such-id")


class TestStartupFailureCapture:
    async def test_unreachable_command_fails_with_error(self) -> None:
        provider = LocalDeploymentProvider()
        deployment = await provider.deploy(DeploymentSpec(agent_id="a", command=["osa-no-such-executable-xyz"]))
        assert deployment.status is DeploymentStatus.FAILED
        assert deployment.error is not None

    async def test_early_exit_fails_deployment(self) -> None:
        provider = LocalDeploymentProvider(health_interval_seconds=0.05)
        deployment = await provider.deploy(
            DeploymentSpec(
                agent_id="a",
                command=[sys.executable, "-c", "print('dying'); raise SystemExit(3)"],
                # A probe turns early exit into a startup failure instead of a
                # falsely-healthy immediate RUNNING.
                health_check_url="http://127.0.0.1:1/health/ready",
                startup_timeout_seconds=5.0,
            )
        )
        assert deployment.status is DeploymentStatus.FAILED
        assert deployment.error is not None
        assert "3" in deployment.error
        lines = await provider.logs(deployment.deployment_id)
        assert any("dying" in line for line in lines)

    async def test_failed_health_probe_fails_deployment(self) -> None:
        """A process that stays alive but never serves the probe fails startup."""
        provider = LocalDeploymentProvider(health_interval_seconds=0.05)
        deployment = await provider.deploy(
            DeploymentSpec(
                agent_id="a",
                command=_sleep_command(),
                health_check_url="http://127.0.0.1:1/health/ready",
                startup_timeout_seconds=0.5,
            )
        )
        assert deployment.status is DeploymentStatus.FAILED
        assert "healthy" in (deployment.error or "")


class TestIdempotency:
    async def test_redeploy_same_running_command_returns_existing(self) -> None:
        provider = LocalDeploymentProvider()
        first = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=_sleep_command(), label="1.0.0"))
        try:
            second = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=_sleep_command(), label="1.0.0"))
            assert second.deployment_id == first.deployment_id
            assert second.pid == first.pid
        finally:
            await provider.stop(first.deployment_id)

    async def test_different_command_spawns_new_deployment(self) -> None:
        provider = LocalDeploymentProvider()
        first = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=_sleep_command(), label="1.0.0"))
        second = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=_sleep_command(45), label="2.0.0"))
        try:
            assert second.deployment_id != first.deployment_id
        finally:
            await provider.stop(first.deployment_id)
            await provider.stop(second.deployment_id)


class TestLifecycleCleanup:
    async def test_shutdown_stops_running_deployments(self) -> None:
        provider = LocalDeploymentProvider()
        first = await provider.deploy(DeploymentSpec(agent_id="a", command=_sleep_command()))
        second = await provider.deploy(DeploymentSpec(agent_id="b", command=_sleep_command()))
        await provider.shutdown()
        statuses = {d.deployment_id: d.status for d in await provider.list_deployments()}
        assert statuses[first.deployment_id] is DeploymentStatus.STOPPED
        assert statuses[second.deployment_id] is DeploymentStatus.STOPPED

    async def test_status_detects_dead_process(self) -> None:
        provider = LocalDeploymentProvider()
        deployment = await provider.deploy(DeploymentSpec(agent_id="a", command=_sleep_command(1)))
        import asyncio

        await asyncio.sleep(1.5)
        observed = await provider.status(deployment.deployment_id)
        assert observed.status is DeploymentStatus.FAILED
        assert "exited" in (observed.error or "")
