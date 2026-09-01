"""Deployment orchestration for the Control Plane (P1.5).

``DeploymentService`` deploys a versioned agent **locally**: it exports the
agent's definition plus its referenced catalog resources to a bundle
directory, then launches a runtime process through a server-owned command
template. Commands are synthesized here — never accepted from API input.

Every transition persists intent/observed state through the
``DeploymentRecordRepository``; the process itself runs under the (hardened)
``LocalDeploymentProvider``, which captures bounded logs and probes health.
No ADK internals are imported: the runtime is an external process.
"""

from __future__ import annotations

import shlex
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from osa.control_plane.backend.agent_catalog import AgentRecord, AgentRecordStatus
from osa.control_plane.backend.deployment import (
    DeploymentProvider,
    DeploymentSpec,
)
from osa.control_plane.backend.repositories import (
    AgentRepository,
    DeploymentRecord,
    DeploymentRecordRepository,
)
from osa.control_plane.backend.resource_catalogs import (  # noqa: TC001 - ctor param
    ResourceCatalogs,
)

if TYPE_CHECKING:
    from osa.generic_agent import AgentDefinition

API_VERSION = "osa/v1alpha1"
DEFAULT_COMMAND_TEMPLATE = "osa-runtime --config {bundle_path} --port {port}"
DEPLOY_COMMAND_TEMPLATE_ENV_VAR = "OSA_DEPLOY_COMMAND_TEMPLATE"
DEPLOY_ROOT_ENV_VAR = "OSA_DEPLOY_ROOT"


@dataclass
class DeployedAgent:
    """Result of a deploy operation."""

    record: DeploymentRecord
    bundle_path: str
    health_check_url: str | None
    pid: int | None


class DeploymentError(Exception):
    """A deployment operation failed."""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def command_template() -> str:
    """The server-owned launch command template (never API-supplied)."""
    import os

    return os.environ.get(DEPLOY_COMMAND_TEMPLATE_ENV_VAR, DEFAULT_COMMAND_TEMPLATE)


def deploy_root() -> Path:
    import os
    import tempfile

    root = os.environ.get(DEPLOY_ROOT_ENV_VAR)
    if root:
        return Path(root)
    return Path(tempfile.gettempdir()) / "osa-deployments"


class DeploymentService:
    """Deploys versioned agents as local runtime processes."""

    def __init__(
        self,
        *,
        provider: DeploymentProvider,
        record_repository: DeploymentRecordRepository,
        agent_repository: AgentRepository,
        resource_catalogs: ResourceCatalogs,
    ) -> None:
        self._provider = provider
        self._records = record_repository
        self._agents = agent_repository
        self._catalogs = resource_catalogs

    async def deploy(self, agent_id: str) -> DeploymentRecord:
        """Export the agent's current definition and launch a runtime."""
        record = await self._agents.get(agent_id)
        if record is None:
            raise KeyError(f"Agent not found: {agent_id}")
        if record.definition is None:
            raise DeploymentError(f"Agent '{record.name}' has no definition to deploy")
        if record.status is not AgentRecordStatus.ACTIVE:
            raise DeploymentError(
                f"Agent '{record.name}' must be active before deployment (status: {record.status.value})"
            )
        if getattr(record, "agent_type", "managed") == "external":
            raise DeploymentError(
                f"Agent '{record.name}' is an external A2A agent; external agents are never deployed by OSA"
            )

        bundle_path = self._export_bundle(record)
        port = _free_port()
        health_url = f"http://127.0.0.1:{port}/health/ready"
        command = shlex.split(command_template().format(bundle_path=bundle_path, port=port))
        spec = DeploymentSpec(
            agent_id=agent_id,
            command=command,
            env={"OSA_ALLOW_FAKE_PROVIDER": "0"},
            health_check_url=health_url,
            label=record.current_version,
        )
        deployment = await self._provider.deploy(spec)
        record_row = DeploymentRecord(
            deployment_id=deployment.deployment_id,
            agent_id=agent_id,
            tenant_id=record.tenant_id,
            agent_name=record.name,
            version=record.current_version,
            status=deployment.status.value,
            detail=deployment.error or "",
        )
        await self._records.upsert(record_row)
        return record_row

    async def status(self, deployment_id: str) -> DeploymentRecord:
        observed = await self._provider.status(deployment_id)
        stored = await self._records.get(deployment_id)
        if stored is None:
            raise KeyError(f"Deployment not found: {deployment_id}")
        stored.status = observed.status.value
        stored.detail = observed.error or ""
        await self._records.upsert(stored)
        return stored

    async def get_record(self, deployment_id: str) -> DeploymentRecord | None:
        """Read persisted deployment intent before performing an operation."""
        return await self._records.get(deployment_id)

    async def stop(self, deployment_id: str) -> DeploymentRecord:
        await self._provider.stop(deployment_id)
        return await self.status(deployment_id)

    async def restart(self, deployment_id: str) -> DeploymentRecord:
        await self._provider.restart(deployment_id)
        return await self.status(deployment_id)

    async def rollback(self, deployment_id: str, to_version: str | None = None) -> DeploymentRecord:
        """Redeploy an earlier version of the deployed agent.

        The target version is taken from the agent's immutable version
        history; the deployment is stopped and relaunched from that
        definition snapshot.
        """
        stored = await self._records.get(deployment_id)
        if stored is None:
            raise KeyError(f"Deployment not found: {deployment_id}")
        agent = await self._agents.get(stored.agent_id)
        if agent is None:
            raise DeploymentError(f"Agent '{stored.agent_id}' no longer exists")
        target = to_version
        if target is None:
            previous = [v for v in agent.versions if v.version != agent.current_version]
            if not previous:
                raise DeploymentError(f"Agent '{agent.name}' has no earlier version to roll back to")
            target = previous[-1].version
        snapshot = next((v for v in agent.versions if v.version == target), None)
        if snapshot is None or snapshot.definition is None:
            raise DeploymentError(f"Version '{target}' has no definition snapshot")

        bundle_path = self._export_bundle(agent, override_definition=snapshot.definition)
        port = _free_port()
        spec = DeploymentSpec(
            agent_id=stored.agent_id,
            command=shlex.split(command_template().format(bundle_path=bundle_path, port=port)),
            health_check_url=f"http://127.0.0.1:{port}/health/ready",
            label=target,
        )
        deployment = await self._provider.deploy(spec)
        stored.version = target
        stored.status = deployment.status.value
        stored.detail = deployment.error or ""
        await self._records.upsert(stored)
        return stored

    async def logs(self, deployment_id: str, tail: int = 200) -> list[str]:
        if await self._records.get(deployment_id) is None:
            raise KeyError(f"Deployment not found: {deployment_id}")
        provider_logs = getattr(self._provider, "logs", None)
        if provider_logs is None:
            return []
        captured: list[str] = await provider_logs(deployment_id, tail)
        return captured

    async def list_for_agent(self, agent_id: str) -> list[DeploymentRecord]:
        return await self._records.list_for_agent(agent_id)

    # -- bundle export --

    def _export_bundle(self, record: AgentRecord, *, override_definition: AgentDefinition | None = None) -> str:
        """Write the agent plus referenced resources as a bundle directory."""
        from osa.generic_agent import McpDefinition, MemoryPolicy, ModelDefinition, SkillDefinition, ToolDefinition

        definition = override_definition if override_definition is not None else record.definition
        assert definition is not None
        catalogs = self._catalogs.for_tenant(record.tenant_id)
        root = deploy_root() / f"{record.name}-{record.current_version or 'draft'}"
        root.mkdir(parents=True, exist_ok=True)
        (root / "agent.yaml").write_text(
            yaml.safe_dump(definition.model_dump(mode="json", by_alias=True), sort_keys=False),
            encoding="utf-8",
        )

        spec = definition.spec
        exporters: list[tuple[str, Any, str]] = [
            ("Model", ModelDefinition, "models"),
            ("Tool", ToolDefinition, "tools"),
            ("Skill", SkillDefinition, "skills"),
            ("Mcp", McpDefinition, "mcps"),
            ("MemoryPolicy", MemoryPolicy, "memory-policies"),
        ]
        wanted: dict[str, set[str]] = {
            "Model": {spec.model.ref} if spec.model is not None else set(),
            "Tool": {ref.ref for ref in spec.tools},
            "Skill": {ref.ref for ref in spec.skills},
            "Mcp": {ref.ref for ref in spec.mcps},
            "MemoryPolicy": {spec.memory.policy} if spec.memory.enabled and spec.memory.policy else set(),
        }
        for kind, _model_cls, directory in exporters:
            names = wanted[kind]
            if not names:
                continue
            target = root / directory
            target.mkdir(exist_ok=True)
            for name in sorted(names):
                if not self._catalogs_has(catalogs, kind, name):
                    raise DeploymentError(
                        f"Agent '{record.name}' references {kind.lower()} '{name}' "
                        "which is not present in the resource catalogs"
                    )
                definition_obj = self._catalogs_get(catalogs, kind, name)
                (target / f"{name}.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "apiVersion": API_VERSION,
                            "kind": kind,
                            "spec": definition_obj.model_dump(mode="json", by_alias=True),
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
        return str(root)

    def _catalogs_has(self, catalogs: ResourceCatalogs, kind: str, name: str) -> bool:
        checks = {
            "Model": catalogs.has_model,
            "Tool": catalogs.has_tool,
            "Skill": catalogs.has_skill,
            "Mcp": catalogs.has_mcp,
            "MemoryPolicy": catalogs.has_memory_policy,
        }
        return checks[kind](name)

    def _catalogs_get(self, catalogs: ResourceCatalogs, kind: str, name: str) -> Any:
        getters = {
            "Model": catalogs.get_model,
            "Tool": catalogs.get_tool,
            "Skill": catalogs.get_skill,
            "Mcp": catalogs.get_mcp,
            "MemoryPolicy": catalogs.get_memory_policy,
        }
        return getters[kind](name)
