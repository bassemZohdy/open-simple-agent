"""Deployment provider abstraction and the local development provider.

A ``DeploymentProvider`` manages the process/lifecycle concerns of running an
agent runtime. It is deliberately separate from ``AgentRuntime``: the runtime
owns in-process agent behavior, while providers own starting, stopping, and
observing the thing that hosts it.

The local provider captures bounded logs per deployment, records startup
failures (including a health-probe window), and supports idempotent
re-deploys of the same (agent, command) pair. Commands are supplied by the
caller of ``deploy`` — the Control Plane synthesizes them from server-owned
templates, never from API input.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from osa.generic_agent import bounded_text

DEFAULT_LOG_LINES = 200
DEFAULT_HEALTH_TIMEOUT_SECONDS = 20.0
DEFAULT_HEALTH_INTERVAL_SECONDS = 0.25


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
    #: URL polled until it responds OK before the deployment reports RUNNING.
    health_check_url: str | None = None
    #: Longest wait for the health check (or for early-exit detection when
    #: no URL is configured).
    startup_timeout_seconds: float = DEFAULT_HEALTH_TIMEOUT_SECONDS
    #: Label used for idempotency and display (e.g. the agent version).
    label: str = ""


@dataclass
class Deployment:
    """A deployment managed by a provider."""

    deployment_id: str
    agent_id: str
    status: DeploymentStatus = DeploymentStatus.STARTING
    pid: int | None = None
    error: str | None = None
    label: str = ""
    health_check_url: str | None = None


def _drain_stream(stream: Any, sink: deque[str], lock: Any) -> None:
    """Reader thread body: push decoded lines into a bounded deque."""
    try:
        for raw_line in iter(stream.readline, b""):
            line = bounded_text(raw_line.decode("utf-8", errors="replace").rstrip())
            with lock:
                sink.append(line)
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            stream.close()


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

    Intended for development: no restart policy, no multi-host awareness.
    stdout/stderr are captured into a bounded ring buffer per deployment
    (``logs()`` returns the tail). When a spec carries a
    ``health_check_url``, the provider polls it during startup; a process
    that exits early or misses the probe window fails the deployment with
    the captured logs attached to the error.
    """

    def __init__(
        self,
        *,
        stop_grace_seconds: float = 5.0,
        log_lines: int = DEFAULT_LOG_LINES,
        health_interval_seconds: float = DEFAULT_HEALTH_INTERVAL_SECONDS,
    ) -> None:
        self._stop_grace_seconds = stop_grace_seconds
        self._log_lines = log_lines
        self._health_interval_seconds = health_interval_seconds
        self._specs: dict[str, DeploymentSpec] = {}
        self._deployments: dict[str, Deployment] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._logs: dict[str, deque[str]] = {}
        self._locks: dict[str, Any] = {}
        self._threads: dict[str, list[Any]] = {}
        self._stop_markers: set[str] = set()

    async def deploy(self, spec: DeploymentSpec) -> Deployment:
        # Idempotency: re-deploying the same (agent, command) while running
        # returns the existing deployment instead of spawning a twin.
        for deployment in self._deployments.values():
            existing_spec = self._specs.get(deployment.deployment_id)
            if (
                deployment.agent_id == spec.agent_id
                and deployment.status is DeploymentStatus.RUNNING
                and existing_spec is not None
                and existing_spec.command == spec.command
            ):
                return deployment

        deployment = Deployment(
            deployment_id=str(uuid4()),
            agent_id=spec.agent_id,
            status=DeploymentStatus.STARTING,
            label=spec.label,
            health_check_url=spec.health_check_url,
        )
        self._logs[deployment.deployment_id] = deque(maxlen=self._log_lines)
        self._locks[deployment.deployment_id] = threading.Lock()
        process, error = self._spawn(deployment.deployment_id, spec)
        if process is None:
            deployment.status = DeploymentStatus.FAILED
            deployment.error = error
            self._deployments[deployment.deployment_id] = deployment
            self._specs[deployment.deployment_id] = spec
            return deployment
        self._specs[deployment.deployment_id] = spec
        self._processes[deployment.deployment_id] = process
        deployment.pid = process.pid
        self._deployments[deployment.deployment_id] = deployment

        probe_ok = await self._await_startup(deployment, spec, process)
        if probe_ok and process.poll() is None:
            deployment.status = DeploymentStatus.RUNNING
        else:
            deployment.status = DeploymentStatus.FAILED
            if deployment.error is None:
                if process.poll() is not None:
                    deployment.error = f"Process exited with code {process.returncode} during startup"
                else:
                    deployment.error = "Startup window elapsed without a healthy process"
            self._reap(deployment.deployment_id)
        return deployment

    async def restart(self, deployment_id: str) -> Deployment:
        """Stop and start again, keeping the same deployment identity."""
        self._require_deployment(deployment_id)
        spec = self._require_spec(deployment_id)
        await self.stop(deployment_id)
        redeployed = await self.deploy(spec)
        if redeployed.deployment_id == deployment_id:
            return redeployed
        # Re-key provider state onto the original deployment identity.
        stores: list[dict[str, Any]] = [
            self._deployments,
            self._specs,
            self._processes,
            self._logs,
            self._locks,
            self._threads,
        ]
        for store in stores:
            if redeployed.deployment_id in store:
                store[deployment_id] = store.pop(redeployed.deployment_id)
        redeployed.deployment_id = deployment_id
        return redeployed

    async def stop(self, deployment_id: str) -> Deployment:
        deployment = self._require_deployment(deployment_id)
        self._stop_markers.add(deployment_id)
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
            self._reap(deployment_id)
        return deployment

    async def list_deployments(self) -> list[Deployment]:
        return [await self.status(deployment_id) for deployment_id in list(self._deployments)]

    async def logs(self, deployment_id: str, tail: int = DEFAULT_LOG_LINES) -> list[str]:
        """Return up to ``tail`` recent captured log lines (oldest first)."""
        self._require_deployment(deployment_id)
        buffer = self._logs.get(deployment_id)
        if buffer is None:
            return []
        with self._locks[deployment_id]:
            lines = list(buffer)
        return lines[-tail:] if tail > 0 else []

    async def shutdown(self) -> None:
        """Stop every deployment owned by this provider."""
        for deployment_id in list(self._deployments):
            deployment = self._deployments[deployment_id]
            if deployment.status in (DeploymentStatus.RUNNING, DeploymentStatus.STARTING):
                await self.stop(deployment_id)

    # -- internals --

    def _spawn(self, deployment_id: str, spec: DeploymentSpec) -> tuple[subprocess.Popen[bytes] | None, str | None]:
        try:
            process = subprocess.Popen(  # noqa: S603 - server-owned command template
                spec.command,
                env={**os.environ, **spec.env} if spec.env else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            return None, str(exc)
        reader = threading.Thread(
            target=_drain_stream,
            args=(process.stdout, self._logs[deployment_id], self._locks[deployment_id]),
            daemon=True,
        )
        reader.start()
        self._threads[deployment_id] = [reader]
        return process, None

    async def _await_startup(
        self, deployment: Deployment, spec: DeploymentSpec, process: subprocess.Popen[bytes]
    ) -> bool:
        """Wait for health (when configured) or the early-exit grace window."""
        deadline = asyncio.get_running_loop().time() + spec.startup_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if process.poll() is not None:
                return False
            if spec.health_check_url is None:
                # No probe configured: treat surviving the grace window as up.
                return True
            if _url_healthy(spec.health_check_url):
                return True
            await asyncio.sleep(self._health_interval_seconds)
        return spec.health_check_url is None and process.poll() is None

    def _reap(self, deployment_id: str) -> None:
        process = self._processes.pop(deployment_id, None)
        if process is not None and process.poll() is None:
            process.terminate()

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


def _url_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - localhost health probe
            return bool(getattr(response, "status", 200) == 200)
    except (urllib.error.URLError, OSError, ValueError):
        return False
