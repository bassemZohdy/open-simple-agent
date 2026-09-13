"""Opt-in real-cluster acceptance for the OpenShift deployment provider.

The test expects an authenticated ``oc`` context, a project where the
Control Plane service account may manage the generated resources, and a
runtime image visible to the cluster. The manual workflow supplies those
operator-owned inputs; ordinary CI never runs this test implicitly.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from osa.control_plane.backend.deployment import DeploymentSpec, DeploymentStatus
from osa.control_plane.backend.openshift_deployment import OpenShiftDeploymentProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("OSA_OPENSHIFT_ACCEPTANCE") != "1",
    reason="set OSA_OPENSHIFT_ACCEPTANCE=1 with an authenticated OpenShift cluster to run",
)


async def _oc(oc: str, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        oc,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))
    return stdout.decode("utf-8", errors="replace")


@pytest.mark.asyncio
async def test_openshift_provider_real_lifecycle_route_and_restart_recovery() -> None:
    image = os.environ["OSA_OPENSHIFT_ACCEPTANCE_IMAGE"]
    oc = os.environ.get("OSA_OC", "oc")
    namespace = f"osa-accept-{uuid4().hex[:8]}"
    bundle = Path(__file__).parents[2] / "examples" / "smoke-bundle"
    await _oc(oc, "create", "namespace", namespace)

    try:
        provider = OpenShiftDeploymentProvider(
            image=image,
            namespace=namespace,
            replicas=1,
            oc=oc,
            rollout_timeout_seconds=180,
        )
        spec = DeploymentSpec(
            agent_id="openshift-acceptance-agent",
            command=["osa-runtime", "--config", str(bundle)],
            env={"OSA_ALLOW_FAKE_PROVIDER": "1"},
            label="v1",
            port=8080,
        )

        deployed = await provider.deploy(spec)
        assert deployed.status is DeploymentStatus.RUNNING, deployed.error

        route_name = provider._resource_name(deployed.deployment_id)  # noqa: SLF001
        route = json.loads(await _oc(oc, "--namespace", namespace, "get", "route", route_name, "-o", "json"))
        assert route["apiVersion"] == "route.openshift.io/v1"
        assert route["spec"]["to"]["name"] == route_name
        assert route["spec"]["port"]["targetPort"] == "http"

        recovered_provider = OpenShiftDeploymentProvider(
            image=image,
            namespace=namespace,
            oc=oc,
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
        await _oc(
            oc,
            "--namespace",
            namespace,
            "patch",
            "deployment",
            deployment_name,
            "--type=merge",
            "-p",
            json.dumps(patch),
        )
        await _oc(
            oc,
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
        await _oc(oc, "delete", "namespace", namespace, "--ignore-not-found=true")
