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
- With a DSN, agents/deployments/resources/audit events persist in
  PostgreSQL and are shared across replicas.

### Migrations

Apply Alembic migrations **before the first rollout of a new version** and
before any replica serves traffic with a schema expectation:

```bash
OSA_CONTROL_PLANE_DATABASE_URL=... uv run osa-cp-migrate
```

The application never auto-migrates (multi-replica race). Run migrations as
a separate step, then roll replicas.

### Multi-replica notes

- Control Plane replicas share state through PostgreSQL; writes are
  transactional with unique constraints and optimistic locking.
- Runtime replicas need a shared `SessionProvider` (persistent) for
  cross-replica session continuity; with the in-memory provider, sessions
  are per-process (documented limitation).

## Deploying an agent through the Control Plane

1. Create the agent (`POST /agents`) with a definition.
2. Activate it (`POST /agents/{id}/activate`) — references are validated.
3. `POST /agents/{id}/deploy` — the Control Plane exports a bundle and
   launches the runtime locally via `OSA_DEPLOY_COMMAND_TEMPLATE`.
4. Observe with `GET /deployments/{id}`, `GET /deployments/{id}/logs`,
   restart/stop/rollback as needed.

Launch commands are synthesized from the server-owned template — the API
never accepts process commands.

## Remaining deployment work

- Packaged Kubernetes provider selection and real Kind acceptance (see
  `TODO.md`; the first generic provider slice exists but follow-up is paused)
- Distributed A2A task stores (A2A task state is in-memory per runtime)
