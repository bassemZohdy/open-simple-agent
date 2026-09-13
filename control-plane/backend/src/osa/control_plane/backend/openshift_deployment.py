"""OpenShift deployment provider for Open Simple Agent.

OpenShift is Kubernetes-compatible for the workload primitives used by OSA,
but its operator-facing boundary is deliberately separate. This provider
uses ``oc`` and adds an OpenShift ``Route`` to the generated ConfigMap,
Deployment, and Service resources. It does not add OpenShift conditionals to
the generic Kubernetes provider or accept arbitrary manifests from API users.

The provider is opt-in. It can be exercised against a real cluster once an
operator selects an OpenShift version, namespace, route policy, and RBAC/SCC
configuration; those compatibility checks remain an acceptance gate.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from osa.control_plane.backend.kubernetes_deployment import KubernetesDeploymentProvider, KubernetesSecretRef

if TYPE_CHECKING:
    from osa.control_plane.backend.deployment import DeploymentSpec

_OPENSHIFT_ROUTE_API_VERSION = "route.openshift.io/v1"
_OPENSHIFT_ROUTE_KIND = "Route"
_ALLOWED_ROUTE_TERMINATIONS = {None, "edge", "reencrypt", "passthrough"}
_ALLOWED_INSECURE_EDGE_POLICIES = {"Allow", "None", "Redirect"}
_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


class OpenShiftDeploymentProvider(KubernetesDeploymentProvider):
    """Deploy OSA runtimes as OpenShift workloads with a managed Route."""

    def __init__(
        self,
        *,
        image: str,
        namespace: str = "default",
        replicas: int = 1,
        oc: str = "oc",
        secret_env: dict[str, KubernetesSecretRef] | None = None,
        rollout_timeout_seconds: int = 60,
        route_host: str | None = None,
        route_tls_termination: str | None = "edge",
        route_insecure_edge_policy: str = "Redirect",
    ) -> None:
        if route_tls_termination not in _ALLOWED_ROUTE_TERMINATIONS:
            raise ValueError("route_tls_termination must be edge, reencrypt, passthrough, or None")
        if route_insecure_edge_policy not in _ALLOWED_INSECURE_EDGE_POLICIES:
            raise ValueError("route_insecure_edge_policy must be Allow, None, or Redirect")
        if route_host is not None and not _is_valid_route_host(route_host):
            raise ValueError("route_host must be a valid DNS host name")
        super().__init__(
            image=image,
            namespace=namespace,
            replicas=replicas,
            kubectl=oc,
            secret_env=secret_env,
            rollout_timeout_seconds=rollout_timeout_seconds,
        )
        self._route_host = route_host
        self._route_tls_termination = route_tls_termination
        self._route_insecure_edge_policy = route_insecure_edge_policy

    def _manifest(self, name: str, deployment_id: str, spec: DeploymentSpec) -> dict[str, Any]:
        """Extend the common workload manifest with one OpenShift Route."""
        manifest = super()._manifest(name, deployment_id, spec)
        service = next(item for item in manifest["items"] if item["kind"] == "Service")
        route_metadata = {
            "name": name,
            "namespace": service["metadata"]["namespace"],
            "labels": dict(service["metadata"].get("labels", {})),
            "annotations": dict(service["metadata"].get("annotations", {})),
        }
        route_spec: dict[str, Any] = {
            "to": {"kind": "Service", "name": name, "weight": 100},
            "port": {"targetPort": "http"},
            "wildcardPolicy": "None",
        }
        if self._route_host is not None:
            route_spec["host"] = self._route_host
        if self._route_tls_termination is not None:
            route_spec["tls"] = {
                "termination": self._route_tls_termination,
                "insecureEdgeTerminationPolicy": self._route_insecure_edge_policy,
            }
        manifest["items"].append(
            {
                "apiVersion": _OPENSHIFT_ROUTE_API_VERSION,
                "kind": _OPENSHIFT_ROUTE_KIND,
                "metadata": route_metadata,
                "spec": route_spec,
            }
        )
        return manifest


def _is_valid_route_host(value: str) -> bool:
    """Validate an OpenShift Route host as a DNS subdomain."""
    if not value or len(value) > 253:
        return False
    labels = value.split(".")
    return all(0 < len(label) <= 63 and _HOST_LABEL_RE.fullmatch(label) is not None for label in labels)


__all__ = ["OpenShiftDeploymentProvider"]
