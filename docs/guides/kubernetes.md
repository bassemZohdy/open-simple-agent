# Kubernetes Deployment Provider

Open Simple Agent includes a first Kubernetes deployment-provider slice implemented by `KubernetesDeploymentProvider`.

The provider uses the operator's existing `kubectl` context and RBAC boundary. It does not invoke a shell and sends generated manifests to `kubectl apply -f -` over stdin.

## Current capabilities

- Kubernetes `Deployment` and `Service` creation.
- Agent deployment bundle materialized as a read-only `ConfigMap` volume.
- Runtime environment variables plus Kubernetes `Secret` key references.
- Readiness and liveness HTTP probes.
- Rolling-update strategy and Kubernetes revision history.
- Status observation from Deployment replica/condition state.
- Restart, stop, scale, rollback, and bounded logs.
- Hardened container security context (`runAsNonRoot`, no privilege escalation, read-only root filesystem, all capabilities dropped).
- OSA identity labels on managed resources for discovery after Control Plane restart.
- A real-cluster lifecycle acceptance test at
  `tests/acceptance/test_kubernetes_provider.py`, run by the `kubernetes-acceptance`
  CI job against a Docker-backed Kind cluster.

## Usage

The packaged Control Plane selects the provider with
`OSA_DEPLOY_PROVIDER=kubernetes` and requires `OSA_KUBERNETES_IMAGE` when a
PostgreSQL Control Plane is configured. The provider remains operator-owned:
OSA generates only the workload objects and never accepts arbitrary manifests
or commands from the API.

```python
from osa.control_plane.backend import KubernetesDeploymentProvider, KubernetesSecretRef

provider = KubernetesDeploymentProvider(
    image="ghcr.io/example/open-simple-agent:0.1.0",
    namespace="osa",
    secret_env={
        "MODEL_API_KEY": KubernetesSecretRef("agent-provider", "api-key"),
    },
)
```

Pass the provider through `configure_control_plane_app(..., deployment_provider=provider)`.

The Control Plane exports the selected agent and referenced resources to a deployment bundle. The Kubernetes provider reads that server-owned bundle, converts its files into a ConfigMap, mounts it at `/etc/osa/bundle`, and launches the runtime image with the supported `osa-runtime` CLI contract.

## Security boundary

The provider never accepts arbitrary Kubernetes manifests or commands from the HTTP API. Resource names, labels, probes, command arguments, and workload shape are synthesized by OSA. Secret values are not placed in ConfigMaps; `KubernetesSecretRef` emits `secretKeyRef` references only.

`kubectl` credentials and authorization remain an infrastructure concern.
Create a namespace-scoped Role and RoleBinding for the Control Plane service
account. The provider needs read/create/update/patch/delete access to the
OSA-managed ConfigMaps, Deployments, and Services; read access to Pods and
Pods/log; and read/update/patch access to the Deployment `scale` subresource.
Do not grant cluster-wide permissions when a namespace-scoped Role is enough.

Provision the target namespace and its image-pull credentials before rollout.
The generated workload currently uses the namespace's service-account/admission
configuration for image pulls; runtime environment secrets use explicit
`KubernetesSecretRef` references and are not copied into ConfigMaps. Apply a
NetworkPolicy outside OSA that permits Control Plane-to-runtime Service traffic
and only the runtime's required database, model-provider, and MCP egress.
The provider does not synthesize CPU/memory requests or limits yet, so enforce
those through a namespace `LimitRange`/`ResourceQuota` policy until the
workload resource contract is selected.

For upgrades, apply `osa-cp-migrate` and any runtime memory/session migrations
before serving the new image, use an immutable runtime image tag or digest,
wait for the generated Deployment readiness rollout, and retain the previous
Deployment revision for `rollback`. The Control Plane image and runtime image
are released separately; durable runtime sessions require a shared migrated
session database.

## Further validation

The generic Kubernetes lifecycle is validated by the CI acceptance job:

1. Deploy/readiness/scale/restart/rollback/stop pass against a real Kind cluster
   in CI; local execution requires Docker to be running.
2. Continue validating Control Plane restart recovery with persisted deployment
   records and Kubernetes labels, including status-watch behavior for
   already-running workloads.

OpenShift-specific behavior remains deferred; the provider targets standard Kubernetes APIs first.

The provider boundary is intentional: `OSA_DEPLOY_PROVIDER=kubernetes` selects
only `KubernetesDeploymentProvider`, while `OSA_DEPLOY_PROVIDER=openshift` is
rejected until a dedicated OpenShift provider exists. OpenShift API, security
context, route, and admission behavior must not be added as conditional paths
to this generic Kubernetes provider.
