"""Unit coverage for the dedicated OpenShift deployment provider."""

from __future__ import annotations

import json
from typing import Any

import pytest

from osa.control_plane.backend.deployment import DeploymentSpec, DeploymentStatus
from osa.control_plane.backend.kubernetes_deployment import KubernetesSecretRef
from osa.control_plane.backend.openshift_deployment import OpenShiftDeploymentProvider

from .test_kubernetes_deployment_provider import _bundle


class FakeOpenShiftProvider(OpenShiftDeploymentProvider):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(image="example/osa-runtime:0.1.0", **kwargs)
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.objects: dict[str, dict[str, Any]] = {}

    async def _run(self, *args: str, stdin: str | None = None) -> str:
        self.calls.append((args, stdin))
        if args[:2] == ("apply", "-f"):
            assert stdin is not None
            manifest = json.loads(stdin)
            deployment = next(item for item in manifest["items"] if item["kind"] == "Deployment")
            name = deployment["metadata"]["name"]
            deployment["status"] = {
                "readyReplicas": deployment["spec"]["replicas"],
                "availableReplicas": deployment["spec"]["replicas"],
            }
            self.objects[name] = deployment
            return "applied"
        if args[:2] == ("rollout", "status"):
            return "successfully rolled out"
        if args[:2] == ("get", "deployment"):
            return json.dumps(self.objects[args[2]])
        raise AssertionError(f"unexpected oc call: {args}")


def _spec(bundle: Any) -> DeploymentSpec:
    return DeploymentSpec(
        agent_id="agent-1",
        command=["osa-runtime", "--config", str(bundle), "--port", "12345"],
        label="v1",
    )


@pytest.mark.asyncio
async def test_deploy_adds_open_shift_route_without_changing_common_workload(tmp_path: Any) -> None:
    provider = FakeOpenShiftProvider(
        namespace="osa-test",
        route_host="agent.example.test",
        secret_env={"MODEL_API_KEY": KubernetesSecretRef("model-secret", "api-key")},
    )

    deployment = await provider.deploy(_spec(_bundle(tmp_path)))

    assert deployment.status is DeploymentStatus.RUNNING
    apply_call = next(call for call in provider.calls if call[0][0] == "apply")
    manifest = json.loads(apply_call[1] or "{}")
    resources = {item["kind"]: item for item in manifest["items"]}
    assert {"ConfigMap", "Deployment", "Service", "Route"} <= resources.keys()
    assert resources["Route"]["apiVersion"] == "route.openshift.io/v1"
    assert resources["Route"]["spec"]["host"] == "agent.example.test"
    assert resources["Route"]["spec"]["to"] == {
        "kind": "Service",
        "name": resources["Service"]["metadata"]["name"],
        "weight": 100,
    }
    assert resources["Route"]["spec"]["tls"] == {
        "termination": "edge",
        "insecureEdgeTerminationPolicy": "Redirect",
    }
    container = resources["Deployment"]["spec"]["template"]["spec"]["containers"][0]
    assert container["securityContext"]["runAsNonRoot"] is True
    secret_env = next(item for item in container["env"] if item["name"] == "MODEL_API_KEY")
    assert secret_env["valueFrom"]["secretKeyRef"] == {"name": "model-secret", "key": "api-key"}


def test_route_options_are_validated() -> None:
    with pytest.raises(ValueError, match="route_tls_termination"):
        OpenShiftDeploymentProvider(image="example/runtime", route_tls_termination="invalid")
    with pytest.raises(ValueError, match="route_insecure_edge_policy"):
        OpenShiftDeploymentProvider(image="example/runtime", route_insecure_edge_policy="invalid")
    with pytest.raises(ValueError, match="route_host"):
        OpenShiftDeploymentProvider(image="example/runtime", route_host="not a host")
