"""Kubernetes deployment provider for Open Simple Agent.

The provider intentionally shells out to ``kubectl`` instead of embedding a
Kubernetes client SDK. This keeps the Control Plane dependency surface small,
works against any conformant cluster (including Kind), and uses the operator's
existing kubeconfig/RBAC boundary.

All commands are fixed argument vectors; no shell is involved and manifests
are passed over stdin. Agent bundle files are materialized as a ConfigMap and
mounted read-only. Runtime credentials can be supplied as Kubernetes Secret
references so secret values never transit the Control Plane process.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from osa.control_plane.backend.deployment import (
    DEFAULT_LOG_LINES,
    Deployment,
    DeploymentProvider,
    DeploymentSpec,
    DeploymentStatus,
)
from osa.generic_agent import bounded_text

_LABEL_PREFIX = "osa.open-simple-agent"
_RUNTIME_PORT = 8080
_NAME_RE = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class KubernetesSecretRef:
    """Map one runtime environment variable to a Kubernetes Secret key."""

    secret_name: str
    key: str


class KubectlError(RuntimeError):
    """Raised when a kubectl operation fails."""


class KubernetesDeploymentProvider(DeploymentProvider):
    """Deploy OSA runtimes as Kubernetes Deployments and Services."""

    def __init__(
        self,
        *,
        image: str,
        namespace: str = "default",
        replicas: int = 1,
        kubectl: str = "kubectl",
        secret_env: dict[str, KubernetesSecretRef] | None = None,
        rollout_timeout_seconds: int = 60,
    ) -> None:
        if not image.strip():
            raise ValueError("image must not be empty")
        if replicas < 1:
            raise ValueError("replicas must be >= 1")
        self._image = image
        self._namespace = namespace
        self._replicas = replicas
        self._kubectl = kubectl
        self._secret_env = dict(secret_env or {})
        self._rollout_timeout_seconds = rollout_timeout_seconds
        self._deployments: dict[str, Deployment] = {}

    async def deploy(self, spec: DeploymentSpec) -> Deployment:
        for existing in self._deployments.values():
            if (
                existing.agent_id == spec.agent_id
                and existing.status is DeploymentStatus.RUNNING
                and existing.label == spec.label
            ):
                return existing

        deployment_id = str(uuid4())
        deployment = Deployment(
            deployment_id=deployment_id,
            agent_id=spec.agent_id,
            status=DeploymentStatus.STARTING,
            label=spec.label,
        )
        self._deployments[deployment_id] = deployment
        name = self._resource_name(deployment_id)
        try:
            manifest = self._manifest(name, deployment_id, spec)
            await self._run("apply", "-f", "-", stdin=json.dumps(manifest))
            await self._await_rollout(name)
            deployment.status = DeploymentStatus.RUNNING
        except (KubectlError, OSError, ValueError) as exc:
            deployment.status = DeploymentStatus.FAILED
            deployment.error = bounded_text(str(exc))
        return deployment

    async def restart(self, deployment_id: str) -> Deployment:
        deployment = await self.status(deployment_id)
        name = self._resource_name(deployment_id)
        await self._run("rollout", "restart", f"deployment/{name}")
        deployment.status = DeploymentStatus.STARTING
        await self._await_rollout(name)
        deployment.status = DeploymentStatus.RUNNING
        deployment.error = None
        return deployment

    async def stop(self, deployment_id: str) -> Deployment:
        deployment = await self.status(deployment_id)
        await self._run("scale", f"deployment/{self._resource_name(deployment_id)}", "--replicas=0")
        deployment.status = DeploymentStatus.STOPPED
        deployment.error = None
        return deployment

    async def scale(self, deployment_id: str, replicas: int) -> Deployment:
        """Scale a deployment and wait for the new replica set to become ready."""
        if replicas < 0:
            raise ValueError("replicas must be >= 0")
        deployment = await self.status(deployment_id)
        name = self._resource_name(deployment_id)
        await self._run("scale", f"deployment/{name}", f"--replicas={replicas}")
        if replicas == 0:
            deployment.status = DeploymentStatus.STOPPED
        else:
            deployment.status = DeploymentStatus.STARTING
            await self._await_rollout(name)
            deployment.status = DeploymentStatus.RUNNING
        deployment.error = None
        return deployment

    async def rollback(self, deployment_id: str) -> Deployment:
        """Use Kubernetes Deployment revision history to roll back one revision."""
        deployment = await self.status(deployment_id)
        name = self._resource_name(deployment_id)
        await self._run("rollout", "undo", f"deployment/{name}")
        deployment.status = DeploymentStatus.STARTING
        await self._await_rollout(name)
        deployment.status = DeploymentStatus.RUNNING
        deployment.error = None
        return deployment

    async def status(self, deployment_id: str) -> Deployment:
        name = self._resource_name(deployment_id)
        try:
            raw = await self._run("get", "deployment", name, "-o", "json")
        except KubectlError as exc:
            if "NotFound" in str(exc) or "not found" in str(exc).lower():
                raise KeyError(f"Deployment not found: {deployment_id}") from None
            raise
        payload = json.loads(raw)
        deployment = self._deployments.get(deployment_id) or self._deployment_from_payload(payload)
        self._deployments[deployment_id] = deployment
        replicas = int(payload.get("spec", {}).get("replicas", 1) or 0)
        status = payload.get("status", {})
        ready = int(status.get("readyReplicas", 0) or 0)
        unavailable = int(status.get("unavailableReplicas", 0) or 0)
        failed = any(
            condition.get("type") == "Progressing" and condition.get("status") == "False"
            for condition in status.get("conditions", [])
        )
        if failed:
            deployment.status = DeploymentStatus.FAILED
            deployment.error = self._condition_message(status) or "Kubernetes rollout failed"
        elif replicas == 0:
            deployment.status = DeploymentStatus.STOPPED
            deployment.error = None
        elif ready >= replicas and unavailable == 0:
            deployment.status = DeploymentStatus.RUNNING
            deployment.error = None
        else:
            deployment.status = DeploymentStatus.STARTING
            deployment.error = None
        return deployment

    async def list_deployments(self) -> list[Deployment]:
        raw = await self._run("get", "deployments", "-l", f"{_LABEL_PREFIX}/managed=true", "-o", "json")
        payload = json.loads(raw)
        result: list[Deployment] = []
        for item in payload.get("items", []):
            deployment = self._deployment_from_payload(item)
            self._deployments[deployment.deployment_id] = deployment
            result.append(await self.status(deployment.deployment_id))
        return result

    async def logs(self, deployment_id: str, tail: int = DEFAULT_LOG_LINES) -> list[str]:
        await self.status(deployment_id)
        if tail <= 0:
            return []
        raw = await self._run(
            "logs",
            f"deployment/{self._resource_name(deployment_id)}",
            f"--tail={tail}",
            "--all-containers=true",
        )
        return [bounded_text(line) for line in raw.splitlines()]

    async def shutdown(self) -> None:
        """Do not delete cluster workloads when the Control Plane shuts down."""

    def _manifest(self, name: str, deployment_id: str, spec: DeploymentSpec) -> dict[str, Any]:
        bundle_path = self._bundle_path(spec.command)
        files = self._bundle_files(bundle_path)
        config_name = f"{name}-bundle"
        labels = {
            "app.kubernetes.io/name": name,
            "app.kubernetes.io/managed-by": "open-simple-agent",
            f"{_LABEL_PREFIX}/managed": "true",
            f"{_LABEL_PREFIX}/deployment-id": deployment_id,
            f"{_LABEL_PREFIX}/agent-id": spec.agent_id,
        }
        if spec.label:
            labels[f"{_LABEL_PREFIX}/version"] = self._label_value(spec.label)

        data: dict[str, str] = {}
        items: list[dict[str, str]] = []
        for index, (relative_path, content) in enumerate(sorted(files.items())):
            key = f"file-{index}"
            data[key] = content
            items.append({"key": key, "path": relative_path})

        env: list[dict[str, Any]] = [{"name": key, "value": value} for key, value in sorted(spec.env.items())]
        env.extend(
            {
                "name": env_name,
                "valueFrom": {"secretKeyRef": {"name": ref.secret_name, "key": ref.key}},
            }
            for env_name, ref in sorted(self._secret_env.items())
        )
        container = {
            "name": "runtime",
            "image": self._image,
            "imagePullPolicy": "IfNotPresent",
            "args": ["--config", "/etc/osa/bundle", "--port", str(_RUNTIME_PORT)],
            "ports": [{"name": "http", "containerPort": _RUNTIME_PORT}],
            "env": env,
            "readinessProbe": {
                "httpGet": {"path": "/health/ready", "port": "http"},
                "periodSeconds": 2,
                "timeoutSeconds": 2,
                "failureThreshold": 15,
            },
            "livenessProbe": {
                "httpGet": {"path": "/health/live", "port": "http"},
                "periodSeconds": 10,
                "timeoutSeconds": 2,
                "failureThreshold": 3,
            },
            "volumeMounts": [{"name": "bundle", "mountPath": "/etc/osa/bundle", "readOnly": True}],
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "runAsNonRoot": True,
                "capabilities": {"drop": ["ALL"]},
            },
        }
        return {
            "apiVersion": "v1",
            "kind": "List",
            "items": [
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": config_name, "namespace": self._namespace, "labels": labels},
                    "data": data,
                },
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": name, "namespace": self._namespace, "labels": labels},
                    "spec": {
                        "replicas": self._replicas,
                        "revisionHistoryLimit": 5,
                        "strategy": {
                            "type": "RollingUpdate",
                            "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                        },
                        "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
                        "template": {
                            "metadata": {"labels": labels},
                            "spec": {
                                "automountServiceAccountToken": False,
                                "containers": [container],
                                "volumes": [
                                    {
                                        "name": "bundle",
                                        "configMap": {"name": config_name, "items": items},
                                    }
                                ],
                            },
                        },
                    },
                },
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": name, "namespace": self._namespace, "labels": labels},
                    "spec": {
                        "selector": {"app.kubernetes.io/name": name},
                        "ports": [{"name": "http", "port": _RUNTIME_PORT, "targetPort": "http"}],
                    },
                },
            ],
        }

    async def _await_rollout(self, name: str) -> None:
        await self._run(
            "rollout",
            "status",
            f"deployment/{name}",
            f"--timeout={self._rollout_timeout_seconds}s",
        )

    async def _run(self, *args: str, stdin: str | None = None) -> str:
        command = [self._kubectl, "--namespace", self._namespace, *args]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise KubectlError(f"Unable to execute kubectl: {exc}") from exc
        stdout, stderr = await process.communicate(stdin.encode() if stdin is not None else None)
        if process.returncode != 0:
            detail = bounded_text(stderr.decode("utf-8", errors="replace").strip())
            raise KubectlError(detail or f"kubectl exited with code {process.returncode}")
        return stdout.decode("utf-8", errors="replace")

    @staticmethod
    def _bundle_path(command: list[str]) -> Path:
        try:
            index = command.index("--config")
            path = Path(command[index + 1])
        except (ValueError, IndexError):
            raise ValueError("Deployment command must contain '--config <bundle_path>'") from None
        if not path.is_dir():
            raise ValueError(f"Bundle path does not exist or is not a directory: {path}")
        return path

    @staticmethod
    def _bundle_files(root: Path) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            files[relative] = path.read_text(encoding="utf-8")
        if "agent.yaml" not in files:
            raise ValueError("Deployment bundle must contain agent.yaml")
        return files

    @staticmethod
    def _resource_name(deployment_id: str) -> str:
        compact = _NAME_RE.sub("-", deployment_id.lower()).strip("-")
        return f"osa-{compact[:40]}"

    @staticmethod
    def _label_value(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_.")
        return (normalized or "unknown")[:63]

    @staticmethod
    def _condition_message(status: dict[str, Any]) -> str:
        for condition in status.get("conditions", []):
            if condition.get("type") == "Progressing" and condition.get("status") == "False":
                return bounded_text(str(condition.get("message") or condition.get("reason") or ""))
        return ""

    @staticmethod
    def _deployment_from_payload(payload: dict[str, Any]) -> Deployment:
        labels = payload.get("metadata", {}).get("labels", {})
        deployment_id = str(labels.get(f"{_LABEL_PREFIX}/deployment-id", ""))
        agent_id = str(labels.get(f"{_LABEL_PREFIX}/agent-id", ""))
        if not deployment_id or not agent_id:
            raise KubectlError("Kubernetes Deployment is missing OSA identity labels")
        return Deployment(
            deployment_id=deployment_id,
            agent_id=agent_id,
            status=DeploymentStatus.STARTING,
            label=str(labels.get(f"{_LABEL_PREFIX}/version", "")),
        )
