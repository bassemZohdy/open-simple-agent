# ADR-008: Runtime access and deployment invoke URLs

## Status

Accepted

## Date

2026-09-04

## Context

Deployed agent runtimes expose HTTP invocation surfaces (`/v1/invoke`,
`/v1/invoke/stream`, sessions). The Control Plane deliberately does not route
invocation traffic, and the deployment APIs never accepted endpoints from
callers. The Control Panel's invocation console therefore could only test
external A2A agents; managed-agent invocation was deferred pending a design
decision on how callers learn where a deployed runtime lives.

The options considered:

1. **Control Plane proxy routes** — the CP forwards invocation traffic to the
   runtime. Rejected for now: it makes the CP a traffic path (contradicting
   its record/control scope), couples CP availability and latency to
   invocations, and duplicates streaming/auth surface.
2. **Deployment API exposes an operator-configured public runtime URL** — the
   CP records and publishes where the runtime lives, but traffic still flows
   directly. Accepted: keeps the CP out of the data path, requires no new
   trust boundary, and mirrors how the launch command is already
   synthesized server-side.
3. **Implicit ingress conventions** — callers guess runtime URLs from agent
   names. Rejected: implicit infrastructure coupling, no single source of
   truth.

## Decision

The deployment record gains an optional, **operator-configured public invoke
URL** (`invoke_url`). DeploymentService synthesizes it at deploy time from
the `OSA_DEPLOY_INVOKE_URL_TEMPLATE` environment variable with
`{deployment_id}`, `{agent_id}`, `{version}`, and `{port}` placeholders —
never from API input, exactly like the launch command template. When the
variable is unset (the default), the deployment publishes no endpoint and the
API returns `invoke_url: null`.

The URL is informational routing metadata: the Control Plane still carries no
invocation traffic. Browser-based managed-agent invocation from the Control
Panel additionally requires the runtime to allow cross-origin calls
(deployment-specific CORS) or a future proxy; until then the Panel shows the
endpoint so operators and integrations can call the runtime directly with
their existing auth. Kubernetes and other providers inherit this
automatically because synthesis lives in `DeploymentService`, not in any
provider.

## Consequences

- `osa_deployments` gains a nullable `invoke_url` column (migration 0007);
  in-memory behavior is unchanged apart from the new field.
- Operators who set the template take responsibility for making the URL
  reachable and for the runtime's auth/CORS posture; OSA does not probe or
  publish health of the public endpoint.
- The Control Panel now uses the recorded URL plus runtime CORS for its direct
  managed-agent test-message flow. A future proxy route, if ever required,
  remains an additive deployment decision and does not change the data model.
