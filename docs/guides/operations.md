# Operations Guide

Day-to-day operational tasks for running Open Simple Agent services. Every
command here reflects implemented behavior; planned capabilities are marked.

## Health and readiness

Both services expose liveness and readiness:

| Service | Liveness | Readiness |
|---|---|---|
| Runtime | `GET /health/live` | `GET /health/ready` — green only after the deployment bundle loaded, references resolved, and secrets verified |
| Control Plane | `GET /health/live` | `GET /health/ready` |

Kubernetes-style probes: use `/health/live` for liveness and
`/health/ready` for readiness gates.

## Observability

- **Metrics**: the runtime exposes Prometheus counters and duration
  summaries at `GET /metrics` (invocations, model/tool calls, token usage).
- **Logs**: structured JSON when `OSA_LOG_FORMAT=json`; every invocation log
  line carries `invocation_id`, `session_id`, agent, user/caller, and
  deployment correlation fields. Captured values are redacted and bounded.
- **Traces**: OpenTelemetry spans cover the invocation path
  (`agent.invoke` → `session.resolve` → `model.run` → tool calls).
- **Audit**: `GET /audit-events?limit=` on the Control Plane returns the
  append-only management/invocation event log (tenant-filtered).

## Deployments

Deploy a managed agent (it must be `active`):

```bash
curl -X POST http://control-plane:8000/agents/$AGENT_ID/deploy -d '{}'
```

The Control Plane exports the agent's definition plus referenced resources
to a bundle and launches the runtime via the command template
(`OSA_DEPLOY_COMMAND_TEMPLATE`). Useful follow-ups:

- `GET /deployments/{deployment_id}` — observed status
- `GET /deployments/{deployment_id}/logs?tail=200` — bounded captured logs
- `POST /deployments/{deployment_id}/restart` — fresh process, same identity
- `POST /deployments/{deployment_id}/rollback?version=1.0.0` — relaunch an
  earlier immutable version snapshot
- `GET /agents/{agent_id}/deployments` — history

A deployment only reports `running` after the launched runtime passes its
health probe; startup failures carry the captured logs in the record detail.

## Database operations

- The schema is Alembic-managed. Apply migrations **before** rolling out a
  Control Plane version: `osa-cp-migrate` (reads
  `OSA_CONTROL_PLANE_DATABASE_URL`).
- The application verifies connectivity at startup but never migrates —
  running migrations from several replicas simultaneously is a race.
- Control Plane state and runtime memory may each use PostgreSQL, but they are
  configured by separate DSNs and may be different databases. Back up every
  configured database; Control Plane schema is migration-owned, while memory
  currently uses transitional startup bootstrap DDL (migration ownership is
  tracked in `TODO.md`).

## Upgrades

1. Bump the version once across the workspace root and the three member
   manifests (lockstep is enforced by `tests/unit/test_versioning.py`).
2. Run `osa-cp-migrate` against the target database.
3. Roll images: the runtime and Control Plane images are built separately
   (`Dockerfile`, `Dockerfile.control-plane`).
4. Rolling restarts of runtime replicas are safe: sessions live in the
   `SessionProvider` (shared persistent provider required for cross-replica
   continuity), and deployments can be rolled back via the rollback API.

## Remaining operational work

- Packaged Kubernetes provider selection and real Kind acceptance (see
  `TODO.md`; the first generic provider slice exists but follow-up is paused)
- Distributed A2A task stores (A2A task state is in-memory per runtime)
