"""Deployment orchestration for the Control Plane (P1.5).

``DeploymentService`` deploys a versioned agent through the selected provider:
it exports the agent's definition plus its referenced catalog resources to a
bundle directory, then asks the provider to launch or schedule the runtime.
Local commands are synthesized here — never accepted from API input.

Every transition persists intent/observed state through the
``DeploymentRecordRepository``; the selected provider owns the workload and
its lifecycle. The local provider captures bounded logs and probes health.
No ADK internals are imported: the runtime is an external process.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shlex
import shutil
import socket
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import yaml

from osa.control_plane.backend.agent_catalog import AgentRecord, AgentRecordStatus
from osa.control_plane.backend.deployment import (
    DeploymentProvider,
    DeploymentSpec,
    LocalDeploymentProvider,
)
from osa.control_plane.backend.deployment_errors import (
    DeploymentError,
    DeploymentOperationBusyError,
    DeploymentOperationLostError,
)
from osa.control_plane.backend.deployment_ownership import (
    DeploymentOperationLease,
    DeploymentOperationOwnershipStore,
    InMemoryDeploymentOperationOwnershipStore,
)
from osa.control_plane.backend.repositories import (
    AgentRepository,
    DeploymentRecord,
    DeploymentRecordRepository,
)
from osa.control_plane.backend.resource_catalogs import (  # noqa: TC001 - ctor param
    ResourceCatalogs,
)
from osa.generic_agent import bounded_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from osa.control_plane.backend.repositories import ResourceDefinitionRepository
    from osa.generic_agent import AgentDefinition

API_VERSION = "osa/v1alpha1"
DEFAULT_COMMAND_TEMPLATE = "osa-runtime --config {bundle_path} --port {port}"
DEPLOY_COMMAND_TEMPLATE_ENV_VAR = "OSA_DEPLOY_COMMAND_TEMPLATE"
DEPLOY_ROOT_ENV_VAR = "OSA_DEPLOY_ROOT"
INVOKE_URL_TEMPLATE_ENV_VAR = "OSA_DEPLOY_INVOKE_URL_TEMPLATE"
DEPLOY_PORT_ENV_VAR = "OSA_DEPLOY_PORT"
RUNTIME_CORS_ENV_VAR = "OSA_DEPLOY_RUNTIME_ALLOWED_ORIGINS"
RUNTIME_CORS_PASSTHROUGH_ENV_VAR = "OSA_RUNTIME_ALLOWED_ORIGINS"
DEPLOY_PROVIDER_ENV_VAR = "OSA_DEPLOY_PROVIDER"
KUBERNETES_IMAGE_ENV_VAR = "OSA_KUBERNETES_IMAGE"
OPENSHIFT_PROVIDER_NAME = "openshift"
OPENSHIFT_IMAGE_ENV_VAR = "OSA_OPENSHIFT_IMAGE"


def create_deployment_provider(*, require_shared: bool = False) -> DeploymentProvider:
    """Create the operator-selected deployment provider.

    ``local`` remains the safe development default. Kubernetes is explicit and
    requires an image so a production Control Plane cannot accidentally start
    process-local workloads when the operator expected cluster scheduling.
    """
    provider_name = os.environ.get(DEPLOY_PROVIDER_ENV_VAR, "local").strip().lower()
    if require_shared and provider_name not in {"kubernetes", OPENSHIFT_PROVIDER_NAME}:
        raise DeploymentError(
            "A durable Control Plane requires OSA_DEPLOY_PROVIDER=kubernetes or openshift; "
            "the local provider is process-local and development-only"
        )
    if provider_name == "local":
        return LocalDeploymentProvider()
    if provider_name == "kubernetes":
        image = os.environ.get(KUBERNETES_IMAGE_ENV_VAR, "").strip()
        if not image:
            raise DeploymentError(f"{KUBERNETES_IMAGE_ENV_VAR} is required when {DEPLOY_PROVIDER_ENV_VAR}=kubernetes")
        from osa.control_plane.backend.kubernetes_deployment import KubernetesDeploymentProvider

        return KubernetesDeploymentProvider(
            image=image,
            namespace=os.environ.get("OSA_KUBERNETES_NAMESPACE", "default"),
            replicas=_positive_env_int("OSA_KUBERNETES_REPLICAS", 1),
            kubectl=os.environ.get("OSA_KUBECTL", "kubectl"),
            rollout_timeout_seconds=_positive_env_int("OSA_KUBERNETES_ROLLOUT_TIMEOUT_SECONDS", 60),
        )
    if provider_name == OPENSHIFT_PROVIDER_NAME:
        image = os.environ.get(OPENSHIFT_IMAGE_ENV_VAR, "").strip()
        if not image:
            raise DeploymentError(f"{OPENSHIFT_IMAGE_ENV_VAR} is required when {DEPLOY_PROVIDER_ENV_VAR}=openshift")
        from osa.control_plane.backend.openshift_deployment import OpenShiftDeploymentProvider

        return OpenShiftDeploymentProvider(
            image=image,
            namespace=os.environ.get("OSA_OPENSHIFT_NAMESPACE", "default"),
            replicas=_positive_env_int("OSA_OPENSHIFT_REPLICAS", 1),
            oc=os.environ.get("OSA_OC", "oc"),
            rollout_timeout_seconds=_positive_env_int("OSA_OPENSHIFT_ROLLOUT_TIMEOUT_SECONDS", 60),
            route_host=os.environ.get("OSA_OPENSHIFT_ROUTE_HOST") or None,
            route_tls_termination=os.environ.get("OSA_OPENSHIFT_ROUTE_TLS_TERMINATION", "edge") or None,
            route_insecure_edge_policy=os.environ.get("OSA_OPENSHIFT_ROUTE_INSECURE_EDGE_POLICY", "Redirect"),
        )
    raise DeploymentError(f"Unsupported {DEPLOY_PROVIDER_ENV_VAR} value: {provider_name}")


def _positive_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise DeploymentError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise DeploymentError(f"{name} must be a positive integer")
    return value


@dataclass
class DeployedAgent:
    """Result of a deploy operation."""

    record: DeploymentRecord
    bundle_path: str
    health_check_url: str | None
    pid: int | None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def deployment_port() -> int:
    """Select a local deployment port, optionally fixed for a container demo.

    A fixed port is useful when the local provider runs inside one container
    and the child runtime must be exposed through a known host port. It is an
    operator setting, so API callers cannot choose or influence it. Unset
    preserves the existing ephemeral-port behavior.
    """
    configured = os.environ.get(DEPLOY_PORT_ENV_VAR, "").strip()
    if not configured:
        return _free_port()
    try:
        port = int(configured)
    except ValueError as exc:
        raise DeploymentError(f"{DEPLOY_PORT_ENV_VAR} must be an integer between 1024 and 65535") from exc
    if not 1024 <= port <= 65535:
        raise DeploymentError(f"{DEPLOY_PORT_ENV_VAR} must be an integer between 1024 and 65535")
    return port


def command_template() -> str:
    """The server-owned launch command template (never API-supplied)."""
    import os

    return os.environ.get(DEPLOY_COMMAND_TEMPLATE_ENV_VAR, DEFAULT_COMMAND_TEMPLATE)


def public_invoke_url(deployment_id: str, agent_id: str, version: str, port: int) -> str | None:
    """Synthesize the operator-configured public invoke URL (ADR-008).

    Templates come only from the ``OSA_DEPLOY_INVOKE_URL_TEMPLATE``
    environment variable — never from API input. Placeholders:
    ``{deployment_id}``, ``{agent_id}``, ``{version}``, ``{port}``. Unset
    means the deployment publishes no public endpoint.
    """
    import os

    template = os.environ.get(INVOKE_URL_TEMPLATE_ENV_VAR, "")
    if not template:
        return None
    return template.format(deployment_id=deployment_id, agent_id=agent_id, version=version, port=port)


def _runtime_env() -> dict[str, str]:
    """Environment for the launched runtime process.

    Forwards the operator-configured browser origins (ADR-008) so the
    deployed runtime allows the Control Panel to call it cross-origin.
    """
    import os

    env = {"OSA_ALLOW_FAKE_PROVIDER": "0"}
    origins = os.environ.get(RUNTIME_CORS_ENV_VAR, "")
    if origins:
        env[RUNTIME_CORS_PASSTHROUGH_ENV_VAR] = origins
    return env


def deploy_root() -> Path:
    import os
    import tempfile

    root = os.environ.get(DEPLOY_ROOT_ENV_VAR)
    if root:
        return Path(root)
    return Path(tempfile.gettempdir()) / "osa-deployments"


class DeploymentService:
    """Deploys versioned agents through a configured runtime provider."""

    def __init__(
        self,
        *,
        provider: DeploymentProvider,
        record_repository: DeploymentRecordRepository,
        agent_repository: AgentRepository,
        resource_catalogs: ResourceCatalogs,
        resource_repository: ResourceDefinitionRepository | None = None,
        operation_ownership: DeploymentOperationOwnershipStore | None = None,
    ) -> None:
        self._provider = provider
        self._records = record_repository
        self._agents = agent_repository
        self._catalogs = resource_catalogs
        self._resource_repository = resource_repository
        self._operation_ownership = (
            operation_ownership if operation_ownership is not None else InMemoryDeploymentOperationOwnershipStore()
        )
        self._agent_locks: dict[str, Any] = {}

    def _agent_lock(self, agent_id: str) -> Any:
        lock = self._agent_locks.get(agent_id)
        if lock is None:
            lock = asyncio.Lock()
            self._agent_locks[agent_id] = lock
        return lock

    async def deploy(self, agent_id: str) -> DeploymentRecord:
        """Export the agent's current definition and launch a runtime."""
        async with self._agent_lock(agent_id):
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

            async with self._owned_operation(f"agent:{agent_id}", record.tenant_id, "deploy") as lease:
                desired_identity = f"{agent_id}:{record.current_version}"
                for existing in await self._records.list_for_agent(agent_id):
                    if existing.version != record.current_version or existing.status != "running":
                        continue
                    try:
                        observed = await self._provider.status(existing.deployment_id)
                    except KeyError:
                        continue
                    if observed.status.value == "running":
                        return existing

                await self._reconcile_resources(record.tenant_id)
                bundle_path = self._export_bundle(record)
                port = deployment_port()
                health_url = f"http://127.0.0.1:{port}/health/ready"
                command = shlex.split(command_template().format(bundle_path=bundle_path, port=port))
                spec = DeploymentSpec(
                    agent_id=agent_id,
                    command=command,
                    env=_runtime_env(),
                    health_check_url=health_url,
                    label=record.current_version,
                    identity=desired_identity,
                    port=port,
                    operation_id=lease.operation_id if lease is not None else None,
                    fencing_epoch=lease.fencing_epoch if lease is not None else None,
                )
                await self._assert_owned(lease)
                deployment = await self._provider.deploy(spec)
                await self._assert_owned(lease)
                actual_port = deployment.port or port
                record_row = DeploymentRecord(
                    deployment_id=deployment.deployment_id,
                    agent_id=agent_id,
                    tenant_id=record.tenant_id,
                    agent_name=record.name,
                    version=record.current_version,
                    status=deployment.status.value,
                    detail=deployment.error or "",
                    invoke_url=public_invoke_url(
                        deployment.deployment_id,
                        agent_id,
                        record.current_version,
                        actual_port,
                    ),
                )
                await self._persist(record_row, lease)
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

    async def reconcile_provider_state(self) -> int:
        """Refresh persisted records from provider-owned workloads.

        Providers such as Kubernetes can discover workloads from stable OSA
        identity labels after a Control Plane restart. Only workloads with an
        existing durable record are reconciled; an orphaned workload is never
        promoted into a deployable record automatically.
        """
        observed_deployments = await self._provider.list_deployments()
        reconciled = 0
        for observed in observed_deployments:
            stored = await self._records.get(observed.deployment_id)
            if stored is None:
                continue
            stored.status = observed.status.value
            stored.detail = observed.error or ""
            await self._records.upsert(stored)
            reconciled += 1
        return reconciled

    async def get_record(self, deployment_id: str) -> DeploymentRecord | None:
        """Read persisted deployment intent before performing an operation."""
        return await self._records.get(deployment_id)

    async def stop(self, deployment_id: str) -> DeploymentRecord:
        stored = await self._records.get(deployment_id)
        if stored is None:
            raise KeyError(f"Deployment not found: {deployment_id}")
        async with self._owned_operation(f"deployment:{deployment_id}", stored.tenant_id, "stop") as lease:
            await self._assert_owned(lease)
            await self._provider.stop(deployment_id)
            await self._assert_owned(lease)
            return await self._refresh_status_owned(deployment_id, lease)

    async def restart(self, deployment_id: str) -> DeploymentRecord:
        stored = await self._records.get(deployment_id)
        if stored is None:
            raise KeyError(f"Deployment not found: {deployment_id}")
        async with self._owned_operation(f"deployment:{deployment_id}", stored.tenant_id, "restart") as lease:
            await self._assert_owned(lease)
            await self._provider.restart(deployment_id)
            await self._assert_owned(lease)
            return await self._refresh_status_owned(deployment_id, lease)

    async def rollback(self, deployment_id: str, to_version: str | None = None) -> DeploymentRecord:
        """Redeploy an earlier version of the deployed agent.

        The target version is taken from the agent's immutable version
        history; the deployment is stopped and relaunched from that
        definition snapshot.
        """
        stored = await self._records.get(deployment_id)
        if stored is None:
            raise KeyError(f"Deployment not found: {deployment_id}")
        async with self._agent_lock(stored.agent_id):
            stored = await self._records.get(deployment_id)
            if stored is None:
                raise KeyError(f"Deployment not found: {deployment_id}")
            async with self._owned_operation(f"deployment:{deployment_id}", stored.tenant_id, "rollback") as lease:
                await self._assert_owned(lease)
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
                try:
                    stopped = await self._provider.stop(deployment_id)
                    await self._assert_owned(lease)
                except KeyError:
                    raise
                except DeploymentOperationLostError:
                    raise
                except Exception as exc:
                    raise DeploymentError("Unable to stop the existing deployment for rollback") from exc
                stored.status = stopped.status.value
                stored.detail = stopped.error or ""
                await self._persist(stored, lease)

                await self._reconcile_resources(agent.tenant_id)
                bundle_path = self._export_bundle(agent, override_definition=snapshot.definition, version=target)
                port = deployment_port()
                spec = DeploymentSpec(
                    agent_id=stored.agent_id,
                    command=shlex.split(command_template().format(bundle_path=bundle_path, port=port)),
                    env=_runtime_env(),
                    health_check_url=f"http://127.0.0.1:{port}/health/ready",
                    label=target,
                    identity=f"{stored.agent_id}:{target}",
                    port=port,
                    operation_id=lease.operation_id if lease is not None else None,
                    fencing_epoch=lease.fencing_epoch if lease is not None else None,
                )
                try:
                    await self._assert_owned(lease)
                    deployment = await self._provider.deploy(spec)
                    await self._assert_owned(lease)
                except DeploymentOperationLostError:
                    raise
                except Exception as exc:
                    raise DeploymentError(f"Rollback to version '{target}' failed to start") from exc
                actual_port = deployment.port or port
                replacement = DeploymentRecord(
                    deployment_id=deployment.deployment_id,
                    agent_id=stored.agent_id,
                    tenant_id=agent.tenant_id,
                    agent_name=agent.name,
                    version=target,
                    status=deployment.status.value,
                    detail=deployment.error or "",
                    invoke_url=public_invoke_url(deployment.deployment_id, stored.agent_id, target, actual_port),
                )
                await self._persist(replacement, lease)
                return replacement

    async def logs(self, deployment_id: str, tail: int = 200) -> list[str]:
        if await self._records.get(deployment_id) is None:
            raise KeyError(f"Deployment not found: {deployment_id}")
        provider_logs = getattr(self._provider, "logs", None)
        if provider_logs is None:
            return []
        captured: list[str] = await provider_logs(deployment_id, tail)
        return [bounded_text(line) for line in captured]

    async def list_for_agent(self, agent_id: str) -> list[DeploymentRecord]:
        return await self._records.list_for_agent(agent_id)

    @asynccontextmanager
    async def _owned_operation(
        self,
        resource_id: str,
        tenant_id: str | None,
        operation_kind: str,
    ) -> AsyncIterator[DeploymentOperationLease]:
        """Run a mutating operation under a renewable fenced lease."""
        operation_id = str(uuid4())
        lease = await self._operation_ownership.acquire(tenant_id, resource_id, operation_id)
        if lease is None:
            raise DeploymentOperationBusyError(
                f"A deployment {operation_kind} operation is already active for '{resource_id}'"
            )

        lost = asyncio.Event()

        async def heartbeat_loop() -> None:
            try:
                while True:
                    await asyncio.sleep(max(1.0, self._operation_ownership.lease_seconds / 3))
                    if not await self._operation_ownership.heartbeat(lease):
                        lost.set()
                        return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the operation must fail closed
                lost.set()

        heartbeat_task = asyncio.create_task(heartbeat_loop())
        operation_error: BaseException | None = None
        try:
            await self._assert_owned(lease, lost)
            yield lease
        except BaseException as exc:
            operation_error = exc
            raise
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            outcome = "completed" if operation_error is None else ("lost" if lost.is_set() else "failed")
            try:
                released = await self._operation_ownership.release(lease, outcome)
            except Exception as exc:  # noqa: BLE001 - successful side effects are unknown
                raise DeploymentOperationLostError(
                    f"Unable to release deployment operation '{lease.operation_id}'; operation outcome is unknown"
                ) from exc
            if not released:
                raise DeploymentOperationLostError(
                    f"Deployment operation '{lease.operation_id}' lost ownership; late results were not accepted"
                ) from operation_error

    async def _assert_owned(
        self,
        lease: DeploymentOperationLease,
        lost: asyncio.Event | None = None,
    ) -> None:
        if (lost is not None and lost.is_set()) or not await self._operation_ownership.is_current(lease):
            raise DeploymentOperationLostError(
                f"Deployment operation '{lease.operation_id}' no longer owns '{lease.resource_id}'"
            )

    async def _persist(self, record: DeploymentRecord, lease: DeploymentOperationLease) -> None:
        """Persist only while the operation's fencing token is current."""
        await self._assert_owned(lease)
        if not await self._records.upsert_if_owned(record, lease):
            raise DeploymentOperationLostError(
                f"Deployment operation '{lease.operation_id}' was fenced before persisting state"
            )

    async def _refresh_status_owned(
        self,
        deployment_id: str,
        lease: DeploymentOperationLease,
    ) -> DeploymentRecord:
        observed = await self._provider.status(deployment_id)
        await self._assert_owned(lease)
        stored = await self._records.get(deployment_id)
        if stored is None:
            raise KeyError(f"Deployment not found: {deployment_id}")
        stored.status = observed.status.value
        stored.detail = observed.error or ""
        await self._persist(stored, lease)
        return stored

    async def _reconcile_resources(self, tenant_id: str | None) -> None:
        if self._resource_repository is None:
            return
        from osa.control_plane.backend.resources_api import reconcile_resource_catalogs

        await reconcile_resource_catalogs(self._catalogs, self._resource_repository, tenant_id)

    # -- bundle export --

    def _export_bundle(
        self,
        record: AgentRecord,
        *,
        override_definition: AgentDefinition | None = None,
        version: str | None = None,
    ) -> str:
        """Write the agent plus referenced resources as a bundle directory."""
        from osa.generic_agent import McpDefinition, MemoryPolicy, ModelDefinition, SkillDefinition, ToolDefinition

        definition = override_definition if override_definition is not None else record.definition
        assert definition is not None
        catalogs = self._catalogs.for_tenant(record.tenant_id)
        deployment_root = deploy_root().resolve()
        deployment_root.mkdir(parents=True, exist_ok=True)
        bundle_id = uuid4().hex
        staging = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.", dir=deployment_root))
        final = deployment_root / bundle_id

        def contained(path: Path) -> Path:
            resolved = path.resolve()
            try:
                resolved.relative_to(deployment_root)
            except ValueError as exc:
                raise DeploymentError("deployment bundle path escaped OSA_DEPLOY_ROOT") from exc
            return resolved

        root = contained(staging)
        try:
            (root / "agent.yaml").write_text(
                yaml.safe_dump(definition.model_dump(mode="json", by_alias=True), sort_keys=False),
                encoding="utf-8",
            )
            (root / "bundle.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": API_VERSION,
                        "kind": "AgentBundle",
                        "metadata": {"name": record.name, "version": version or record.current_version or "draft"},
                    },
                    sort_keys=False,
                ),
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
                target = contained(root / directory)
                target.mkdir(exist_ok=True)
                for name in sorted(names):
                    if not self._catalogs_has(catalogs, kind, name):
                        raise DeploymentError(
                            f"Agent '{record.name}' references {kind.lower()} '{name}' "
                            "which is not present in the resource catalogs"
                        )
                    definition_obj = self._catalogs_get(catalogs, kind, name)
                    filename = f"{hashlib.sha256(name.encode('utf-8')).hexdigest()}.yaml"
                    contained(target / filename).write_text(
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
            contained(final)
            os.replace(root, final)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return str(final)

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
