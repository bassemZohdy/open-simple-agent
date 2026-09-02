from __future__ import annotations

import json
from pathlib import Path

import pytest

from osa.control_plane.backend.deployment import DeploymentSpec, DeploymentStatus
from osa.control_plane.backend.kubernetes_deployment import (
    KubernetesDeploymentProvider,
    KubernetesSecretRef,
)


class FakeKubernetesProvider(KubernetesDeploymentProvider):
    def __init__(self, **kwargs):
        super().__init__(image="example/osa-runtime:0.1.0", **kwargs)
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.objects: dict[str, dict] = {}

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
        if args[:2] == ("rollout", "restart"):
            return "restarted"
        if args[:2] == ("rollout", "undo"):
            return "rolled back"
        if args[0] == "scale":
            name = args[1].split("/", 1)[1]
            replicas = int(args[2].split("=", 1)[1])
            self.objects[name]["spec"]["replicas"] = replicas
            self.objects[name]["status"]["readyReplicas"] = replicas
            return "scaled"
        if args[:2] == ("get", "deployment"):
            return json.dumps(self.objects[args[2]])
        if args[:2] == ("get", "deployments"):
            return json.dumps({"items": list(self.objects.values())})
        if args[0] == "logs":
            return "line one\nline two\n"
        raise AssertionError(f"unexpected kubectl call: {args}")


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "agent.yaml").write_text("apiVersion: osa/v1alpha1\nkind: Agent\n", encoding="utf-8")
    models = root / "models"
    models.mkdir()
    (models / "model.yaml").write_text("kind: Model\n", encoding="utf-8")
    return root


def _spec(bundle: Path, *, label: str = "v1") -> DeploymentSpec:
    return DeploymentSpec(
        agent_id="agent-1",
        command=["osa-runtime", "--config", str(bundle), "--port", "12345"],
        env={"OSA_ALLOW_FAKE_PROVIDER": "0"},
        label=label,
    )


@pytest.mark.asyncio
async def test_deploy_builds_configmap_service_probes_and_security_context(tmp_path: Path) -> None:
    provider = FakeKubernetesProvider(
        namespace="osa-test",
        secret_env={"MODEL_API_KEY": KubernetesSecretRef("model-secret", "api-key")},
    )

    deployment = await provider.deploy(_spec(_bundle(tmp_path)))

    assert deployment.status is DeploymentStatus.RUNNING
    apply_call = next(call for call in provider.calls if call[0][0] == "apply")
    manifest = json.loads(apply_call[1] or "{}")
    resources = {item["kind"]: item for item in manifest["items"]}
    assert {"ConfigMap", "Deployment", "Service"} <= resources.keys()
    assert "agent.yaml" in {item["path"] for item in resources["Deployment"]["spec"]["template"]["spec"]["volumes"][0]["configMap"]["items"]}
    container = resources["Deployment"]["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert container["securityContext"]["runAsNonRoot"] is True
    secret_env = next(item for item in container["env"] if item["name"] == "MODEL_API_KEY")
    assert secret_env["valueFrom"]["secretKeyRef"] == {"name": "model-secret", "key": "api-key"}
    assert "api-key" not in json.dumps(resources["ConfigMap"]["data"].values())


@pytest.mark.asyncio
async def test_deploy_is_idempotent_for_same_agent_and_version(tmp_path: Path) -> None:
    provider = FakeKubernetesProvider()
    spec = _spec(_bundle(tmp_path))

    first = await provider.deploy(spec)
    second = await provider.deploy(spec)

    assert second.deployment_id == first.deployment_id
    assert sum(1 for call in provider.calls if call[0][0] == "apply") == 1


@pytest.mark.asyncio
async def test_scale_restart_rollback_status_logs_and_list(tmp_path: Path) -> None:
    provider = FakeKubernetesProvider()
    deployment = await provider.deploy(_spec(_bundle(tmp_path)))

    stopped = await provider.scale(deployment.deployment_id, 0)
    assert stopped.status is DeploymentStatus.STOPPED

    running = await provider.scale(deployment.deployment_id, 2)
    assert running.status is DeploymentStatus.RUNNING

    restarted = await provider.restart(deployment.deployment_id)
    assert restarted.status is DeploymentStatus.RUNNING

    rolled_back = await provider.rollback(deployment.deployment_id)
    assert rolled_back.status is DeploymentStatus.RUNNING

    status = await provider.status(deployment.deployment_id)
    assert status.status is DeploymentStatus.RUNNING
    assert await provider.logs(deployment.deployment_id, tail=2) == ["line one", "line two"]
    listed = await provider.list_deployments()
    assert [item.deployment_id for item in listed] == [deployment.deployment_id]


@pytest.mark.asyncio
async def test_stop_scales_to_zero(tmp_path: Path) -> None:
    provider = FakeKubernetesProvider()
    deployment = await provider.deploy(_spec(_bundle(tmp_path)))

    stopped = await provider.stop(deployment.deployment_id)

    assert stopped.status is DeploymentStatus.STOPPED
    assert any(call[0][0] == "scale" and call[0][2] == "--replicas=0" for call in provider.calls)


def test_bundle_requires_agent_yaml(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    provider = FakeKubernetesProvider()

    with pytest.raises(ValueError, match="agent.yaml"):
        provider._bundle_files(root)
