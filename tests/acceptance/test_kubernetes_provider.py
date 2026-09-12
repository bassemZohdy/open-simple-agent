"""Real-cluster acceptance tests for the Kubernetes deployment provider.

The test is opt-in because it requires a Docker-backed Kind cluster and a
runtime image loaded into that cluster. CI enables it explicitly.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from osa.control_plane.backend.deployment import DeploymentSpec, DeploymentStatus
from osa.control_plane.backend.kubernetes_deployment import KubernetesDeploymentProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("OSA_KIND_ACCEPTANCE") != "1",
    reason="set OSA_KIND_ACCEPTANCE=1 with a Docker-backed Kind cluster to run",
)


async def _kubectl(kubectl: str, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        kubectl,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))
    return stdout.decode("utf-8", errors="replace")


@pytest.mark.asyncio
async def test_kubernetes_provider_real_lifecycle_and_restart_recovery() -> None:
    image = os.environ["OSA_KUBERNETES_ACCEPTANCE_IMAGE"]
    kubectl = os.environ.get("OSA_KUBECTL", "kubectl")
    namespace = f"osa-accept-{uuid4().hex[:8]}"
    bundle = Path(__file__).parents[2] / "examples" / "smoke-bundle"
    await _kubectl(kubectl, "create", "namespace", namespace)

    try:
        provider = KubernetesDeploymentProvider(
            image=image,
            namespace=namespace,
            replicas=1,
            kubectl=kubectl,
            rollout_timeout_seconds=180,
        )
        spec = DeploymentSpec(
            agent_id="kind-acceptance-agent",
            command=["osa-runtime", "--config", str(bundle)],
            env={"OSA_ALLOW_FAKE_PROVIDER": "1"},
            label="v1",
            port=8080,
        )

        deployed = await provider.deploy(spec)
        assert deployed.status is DeploymentStatus.RUNNING, deployed.error

        # A new provider instance simulates a Control Plane restart. The
        # deployment is rehydrated from Kubernetes identity labels, not its
        # process-local cache.
        recovered_provider = KubernetesDeploymentProvider(
            image=image,
            namespace=namespace,
            kubectl=kubectl,
            rollout_timeout_seconds=180,
        )
        recovered = await recovered_provider.status(deployed.deployment_id)
        assert recovered.deployment_id == deployed.deployment_id
        assert recovered.agent_id == spec.agent_id
        assert recovered.status is DeploymentStatus.RUNNING
        listed = await recovered_provider.list_deployments()
        assert [item.deployment_id for item in listed] == [deployed.deployment_id]

        scaled = await recovered_provider.scale(deployed.deployment_id, 2)
        assert scaled.status is DeploymentStatus.RUNNING

        restarted = await recovered_provider.restart(deployed.deployment_id)
        assert restarted.status is DeploymentStatus.RUNNING

        deployment_name = recovered_provider._resource_name(deployed.deployment_id)  # noqa: SLF001
        patch = {"spec": {"template": {"metadata": {"annotations": {"osa-acceptance-revision": "v2"}}}}}
        await _kubectl(
            kubectl,
            "--namespace",
            namespace,
            "patch",
            "deployment",
            deployment_name,
            "--type=merge",
            "-p",
            json.dumps(patch),
        )
        await _kubectl(
            kubectl,
            "--namespace",
            namespace,
            "rollout",
            "status",
            f"deployment/{deployment_name}",
            "--timeout=180s",
        )
        rolled_back = await recovered_provider.rollback(deployed.deployment_id)
        assert rolled_back.status is DeploymentStatus.RUNNING

        stopped = await recovered_provider.stop(deployed.deployment_id)
        assert stopped.status is DeploymentStatus.STOPPED
    finally:
        await _kubectl(kubectl, "delete", "namespace", namespace, "--ignore-not-found=true")
