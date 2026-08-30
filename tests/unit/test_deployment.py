"""Tests for deployment providers (Milestone 13)."""

import asyncio
import sys

import pytest

from osa.control_plane.backend import (
    Deployment,
    DeploymentSpec,
    DeploymentStatus,
    LocalDeploymentProvider,
)


def _sleep_command(seconds: float = 30) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


class TestLocalDeploymentProvider:
    async def test_deploy_starts_process_and_reports_running(self) -> None:
        provider = LocalDeploymentProvider()

        deployment = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=_sleep_command()))

        assert deployment.status is DeploymentStatus.RUNNING
        assert deployment.pid is not None
        assert deployment.error is None
        await provider.stop(deployment.deployment_id)

    async def test_stop_terminates_process(self) -> None:
        provider = LocalDeploymentProvider()
        deployment = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=_sleep_command()))

        stopped = await provider.stop(deployment.deployment_id)

        assert stopped.status is DeploymentStatus.STOPPED
        assert stopped.error is None

    async def test_status_reports_failed_after_process_exit(self) -> None:
        provider = LocalDeploymentProvider()
        deployment = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=[sys.executable, "-c", "pass"]))

        await asyncio.sleep(0.2)
        status = await provider.status(deployment.deployment_id)

        assert status.status is DeploymentStatus.FAILED
        assert status.error is not None
        assert "exited with code 0" in status.error

    async def test_restart_produces_fresh_running_process(self) -> None:
        provider = LocalDeploymentProvider()
        deployment = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=_sleep_command()))
        original_pid = deployment.pid

        restarted = await provider.restart(deployment.deployment_id)

        assert restarted.status is DeploymentStatus.RUNNING
        assert restarted.deployment_id == deployment.deployment_id
        assert restarted.pid != original_pid
        await provider.stop(restarted.deployment_id)

    async def test_list_deployments_reports_all(self) -> None:
        provider = LocalDeploymentProvider()
        first = await provider.deploy(DeploymentSpec(agent_id="a", command=_sleep_command()))
        second = await provider.deploy(DeploymentSpec(agent_id="b", command=_sleep_command()))
        await provider.stop(first.deployment_id)

        deployments = await provider.list_deployments()

        assert len(deployments) == 2
        by_agent = {d.agent_id: d for d in deployments}
        assert by_agent["a"].status is DeploymentStatus.STOPPED
        assert by_agent["b"].status is DeploymentStatus.RUNNING
        await provider.stop(second.deployment_id)

    async def test_deploy_failure_reports_failed(self) -> None:
        provider = LocalDeploymentProvider()

        deployment = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=["/nonexistent/binary/for/osa"]))

        assert deployment.status is DeploymentStatus.FAILED
        assert deployment.error is not None
        assert deployment.pid is None

    async def test_unknown_deployment_raises(self) -> None:
        provider = LocalDeploymentProvider()

        with pytest.raises(KeyError, match="Deployment not found"):
            await provider.status("nope")

    async def test_stop_is_idempotent(self) -> None:
        provider = LocalDeploymentProvider()
        deployment = await provider.deploy(DeploymentSpec(agent_id="agent-1", command=_sleep_command()))
        await provider.stop(deployment.deployment_id)

        second_stop = await provider.stop(deployment.deployment_id)

        assert second_stop.status is DeploymentStatus.STOPPED
        assert isinstance(second_stop, Deployment)
