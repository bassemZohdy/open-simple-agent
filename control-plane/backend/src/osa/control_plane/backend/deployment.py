"""Deployment provider abstraction and the local development provider.

A ``DeploymentProvider`` manages the process/lifecycle concerns of running an
agent runtime. It is deliberately separate from ``AgentRuntime``: the runtime
owns in-process agent behavior, while providers own starting, stopping, and
observing the thing that hosts it.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4


class DeploymentStatus(StrEnum):
    """Lifecycle status of a deployment."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class DeploymentSpec:
    """How to launch one agent runtime."""

    agent_id: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Deployment:
    """A deployment managed by a provider."""

    deployment_id: str
    agent_id: str
    status: DeploymentStatus = DeploymentStatus.STARTING
    pid: int | None = None
    error: str | None = None


class DeploymentProvider(ABC):
    """Contract for deploying, starting, stopping, and observing agent runtimes."""

    @abstractmethod
    async def deploy(self, spec: DeploymentSpec) -> Deployment:
        """Create and start a new deployment for the spec."""
        ...

    @abstractmethod
    async def restart(self, deployment_id: str) -> Deployment:
        """Stop and start the deployment again (same agent, fresh process)."""
        ...

    @abstractmethod
    async def stop(self, deployment_id: str) -> Deployment:
        """Stop a deployment."""
        ...

    @abstractmethod
    async def status(self, deployment_id: str) -> Deployment:
        """Report the current status of a deployment."""
        ...

    @abstractmethod
    async def list_deployments(self) -> list[Deployment]:
        """List all deployments known to this provider."""
        ...


class LocalDeploymentProvider(DeploymentProvider):
    """Runs each deployment as a local OS process.

    Intended for development. There is no restart policy, no health probing
    beyond process liveness, and no multi-host awareness.
    """

    def __init__(self, stop_grace_seconds: float = 5.0) -> None:
        self._stop_grace_seconds = stop_grace_seconds
        self._specs: dict[str, DeploymentSpec] = {}
        self._deployments: dict[str, Deployment] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    async def deploy(self, spec: DeploymentSpec) -> Deployment:
        deployment = Deployment(deployment_id=str(uuid4()), agent_id=spec.agent_id, status=DeploymentStatus.STARTING)
        process, error = self._spawn(spec)
        if process is None:
            deployment.status = DeploymentStatus.FAILED
            deployment.error = error
            self._deployments[deployment.deployment_id] = deployment
            return deployment
        self._specs[deployment.deployment_id] = spec
        self._processes[deployment.deployment_id] = process
        deployment.pid = process.pid
        deployment.status = DeploymentStatus.RUNNING
        self._deployments[deployment.deployment_id] = deployment
        return deployment

    async def restart(self, deployment_id: str) -> Deployment:
        deployment = self._require_deployment(deployment_id)
        spec = self._require_spec(deployment_id)
        await self.stop(deployment_id)
        process, error = self._spawn(spec)
        if process is None:
            deployment.status = DeploymentStatus.FAILED
            deployment.error = error
            return deployment
        self._processes[deployment_id] = process
        deployment.pid = process.pid
        deployment.status = DeploymentStatus.RUNNING
        return deployment

    def _spawn(self, spec: DeploymentSpec) -> tuple[subprocess.Popen[bytes] | None, str | None]:
        try:
            process = subprocess.Popen(  # noqa: S603
                spec.command,
                env={**os.environ, **spec.env} if spec.env else None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            return None, str(exc)
        return process, None

    async def stop(self, deployment_id: str) -> Deployment:
        deployment = self._require_deployment(deployment_id)
        process = self._processes.pop(deployment_id, None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self._stop_grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self._stop_grace_seconds)
        deployment.status = DeploymentStatus.STOPPED
        deployment.error = None
        return deployment

    async def status(self, deployment_id: str) -> Deployment:
        deployment = self._require_deployment(deployment_id)
        process = self._processes.get(deployment_id)
        if process is not None and deployment.status is DeploymentStatus.RUNNING and process.poll() is not None:
            deployment.status = DeploymentStatus.FAILED
            deployment.error = f"Process exited with code {process.returncode}"
        return deployment

    async def list_deployments(self) -> list[Deployment]:
        return [await self.status(deployment_id) for deployment_id in self._deployments]

    def _require_deployment(self, deployment_id: str) -> Deployment:
        try:
            return self._deployments[deployment_id]
        except KeyError:
            raise KeyError(f"Deployment not found: {deployment_id}") from None

    def _require_spec(self, deployment_id: str) -> DeploymentSpec:
        try:
            return self._specs[deployment_id]
        except KeyError:
            raise KeyError(f"Deployment not found: {deployment_id}") from None
