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

## Usage

The provider is currently constructed explicitly by the Control Plane embedding code while the deployment-provider configuration switch and Kind acceptance job are completed.

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

`kubectl` credentials and authorization remain an infrastructure concern. Give the Control Plane service account only the namespace-scoped permissions needed for OSA-managed ConfigMaps, Deployments, Services, Pods/log, and rollout/scale operations.

## Remaining validation

Before marking P1.5 complete:

1. Add the external provider-selection configuration for the packaged Control Plane.
2. Validate deploy/readiness/scale/restart/rollback/stop against a real Kind cluster in CI.
3. Confirm restart recovery with persisted deployment records and Kubernetes labels.
4. Document production RBAC and image-pull configuration.

OpenShift-specific behavior remains deferred; the provider targets standard Kubernetes APIs first.
