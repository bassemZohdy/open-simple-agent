# Deployment Guide

How to run Open Simple Agent services in containers or as processes.

## The runtime (data plane)

The runtime serves one externally configured agent from a **deployment
bundle**. The production image installs the LiteLLM, PostgreSQL, and A2A
extras; it runs as non-root (UID 10001, arbitrary-UID friendly) with a
health check.

### Configuration

The runtime reads a bundle directory mounted at `/app/config`:

```
/app/config
├── agent.yaml        # AgentDefinition
├── models/*.yaml     # referenced model definitions
├── tools/*.yaml      # referenced tool definitions (implementations ship in the image)
├── mcps/*.yaml       # referenced MCP servers
└── memory-policies/*.yaml
```

Environment variables:

| Variable | Purpose |
|---|---|
| `OSA_BUNDLE` | Bundle path (the image default is `/app/config`; `--config` overrides) |
| `OSA_MODEL_REF` | Override the agent's model reference at start time |
| `OSA_ALLOW_FAKE_PROVIDER` | Opt-in deterministic fake model (`1`); never enabled by default |
| `OSA_A2A_URL` | Public URL advertised in the Agent Card when `spec.a2a.enabled` |
| `OSA_MEMORY_DATABASE_URL` | PostgreSQL DSN for persistent memory (optional; in-memory without it) |
| `OSA_AUTH_*` | Bearer/OIDC validation for inbound calls (see the security guide) |
| `OSA_LOG_FORMAT=json` | Structured JSON logs |

### Run

```bash
docker run -d -p 8080:8080 \
  -v "$PWD/my-bundle:/app/config:ro" \
  -e OSA_MEMORY_DATABASE_URL="postgresql+asyncpg://..." \
  osa-runtime:latest
```

Startup is fail-fast: an invalid bundle, missing resource reference, or
unreachable memory database aborts the boot before readiness. `SIGTERM`
shuts down gracefully (in-flight runs are cancelled; sessions closed).

Local equivalent: `uv run osa-runtime --config ./my-bundle --port 8080`.

## The Control Plane (management plane)

```bash
docker run -d -p 8000:8000 \
  -e OSA_CONTROL_PLANE_DATABASE_URL="postgresql+asyncpg://..." \
  osa-control-plane:latest
```

- Without `OSA_CONTROL_PLANE_DATABASE_URL` the Control Plane runs in-memory
  (single process; state lost on restart).
- With a DSN, agents/deployments/resources/audit events/external-agent records
  persist in PostgreSQL and are shared across replicas. Resource reads and
  validation reconcile each process-local catalog from durable records. A
  durable Control Plane requires `OSA_DEPLOY_PROVIDER=kubernetes`; the local
  provider is development-only and process-local.

### Deployment configuration

The Control Plane image starts the PostgreSQL-aware application factory. Run
`osa-cp-migrate` separately before rollout when
`OSA_CONTROL_PLANE_DATABASE_URL` is configured; the application never
auto-migrates. The selected deployment provider uses these server-side settings:

| Variable | Purpose | Default |
|---|---|---|
| `OSA_CONTROL_PLANE_DATABASE_URL` | PostgreSQL DSN for agents, resources, deployments, and audit events | unset (in-memory) |
| `OSA_DEPLOY_COMMAND_TEMPLATE` | Server-owned runtime launch template; supports `{bundle_path}` and `{port}` | `osa-runtime --config {bundle_path} --port {port}` |
| `OSA_DEPLOY_PROVIDER` | Deployment provider (`local` or `kubernetes`) | `local` for in-memory development; required as `kubernetes` with a durable Control Plane |
| `OSA_KUBERNETES_IMAGE` | Runtime image used by the Kubernetes provider | required for `kubernetes` |
| `OSA_KUBERNETES_NAMESPACE` | Kubernetes namespace | `default` |
| `OSA_KUBERNETES_REPLICAS` | Desired runtime replicas | `1` |
| `OSA_KUBECTL` | `kubectl` executable | `kubectl` |
| `OSA_KUBERNETES_ROLLOUT_TIMEOUT_SECONDS` | Maximum Kubernetes rollout wait | `60` |
| `OSA_DEPLOY_ROOT` | Root directory for exported deployment bundles | OS temporary directory plus `osa-deployments` |
| `OSA_DEPLOY_INVOKE_URL_TEMPLATE` | Optional public runtime URL; supports `{deployment_id}`, `{agent_id}`, `{version}`, and `{port}` | unset |
| `OSA_DEPLOY_RUNTIME_ALLOWED_ORIGINS` | Origins forwarded to launched runtimes as `OSA_RUNTIME_ALLOWED_ORIGINS` | unset |

The command template is configuration owned by the server/operator, never API
input. The Control Plane image packages `osa-runtime` for the local development
topology. In Kubernetes mode, the provider mounts the exported bundle into the
separately published runtime image, so runtime credentials stay in Kubernetes
Secret references.

### Migrations

Apply Alembic migrations **before the first rollout of a new version** and
before any replica serves traffic with a schema expectation:

```bash
OSA_CONTROL_PLANE_DATABASE_URL=... uv run osa-cp-migrate
```

The application never auto-migrates (multi-replica race). Run migrations as
a separate step, then roll replicas.

Runtime persistence has independent migration commands and startup ordering:

```bash
OSA_MEMORY_DATABASE_URL=... uv run osa-memory-migrate
OSA_SESSION_DATABASE_URL=... uv run osa-session-migrate
```

Run the memory command when `OSA_MEMORY_DATABASE_URL` is configured. Run the
session command before deploying any bundle whose `spec.session.persistence`
is `true`. Runtime startup validates both schemas but never creates or alters
them.

### Multi-replica notes

- Control Plane replicas share state through PostgreSQL; writes are
  transactional with unique constraints and optimistic locking.
- The local provider owns and stops only its own child processes and is rejected
  for durable Control Plane deployments. The Kubernetes provider rehydrates
  status from identity-labelled workloads after Control Plane restarts.
- Runtime replicas need `spec.session.persistence: true` plus a shared,
  migrated `OSA_SESSION_DATABASE_URL` for cross-replica session continuity.
- Optional HTTP rate limits are available in-process; production replicas
  should enforce the same policy at an API gateway or service mesh.

## Deploying an agent through the Control Plane

1. Create the agent (`POST /agents`) with a definition.
2. Activate it (`POST /agents/{id}/activate`) — references are validated.
3. `POST /agents/{id}/deploy` — the Control Plane exports a bundle and
   launches it through the selected provider: the local provider uses
   `OSA_DEPLOY_COMMAND_TEMPLATE`, while Kubernetes creates the labelled
   workload from the exported bundle.
4. Observe with `GET /deployments/{id}`, `GET /deployments/{id}/logs`,
   restart/stop/rollback as needed.

Launch commands are synthesized from the server-owned template — the API
never accepts process commands.

## Remaining deployment work

- The real Kind-cluster lifecycle acceptance job is committed to CI; Control
  Plane restart recovery and distributed deployment-operation ownership remain
  gated by the Kubernetes CI environment.
- Distributed A2A task state and cancellation semantics (P2.4)
