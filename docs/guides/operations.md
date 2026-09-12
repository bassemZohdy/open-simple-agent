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
  summaries at `GET /metrics` (invocations, model/tool/MCP calls, capability
  outcomes, token usage, and rate-limit responses).
- **Capability event file**: set `OSA_CAPABILITY_TELEMETRY_PATH` for a bounded,
  sanitized JSONL trail. `OSA_CAPABILITY_TELEMETRY_MAX_BYTES` controls
  compaction; the sink never stores prompts, outputs, credentials, or tool
  arguments and is process-local.
- **Logs**: structured JSON when `OSA_LOG_FORMAT=json`; every invocation log
  line carries `invocation_id`, `session_id`, agent, user/caller, and
  deployment correlation fields. Captured values are redacted and bounded.
- **Traces**: OpenTelemetry spans cover the invocation path
  (`agent.invoke` → `session.resolve` → `model.run` → tool calls).
- **Audit**: `GET /audit-events?limit=` on the Control Plane returns the
  append-only management/invocation event log (tenant-filtered).
- **Capacity**: set `OSA_RATE_LIMIT_REQUESTS` and optionally
  `OSA_RATE_LIMIT_WINDOW_SECONDS`/`OSA_RATE_LIMIT_BURST` to enable bounded
  route/caller budgets. Exhausted requests return `429` with `Retry-After`.
  The built-in store is process-local; use a gateway or service mesh for
  replica-safe production enforcement.

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
- Control Plane state, runtime memory, and runtime sessions may each use
  PostgreSQL, configured by separate DSNs and potentially different databases.
  Back up every configured database. Apply `osa-cp-migrate`,
  `osa-memory-migrate`, and `osa-session-migrate` as separate pre-start steps;
  all three runtimes validate schema versions and do not auto-migrate.

## Upgrades

1. Bump the version once across the workspace root and the four member
   manifests (lockstep is enforced by `tests/unit/test_versioning.py`).
2. Run `osa-cp-migrate` and, when configured, the memory/session migration
   commands against their target databases.
3. Roll images: the runtime and Control Plane images are built separately;
   the Control Plane image includes `osa-runtime` for local development, while
   Kubernetes uses the separately published runtime image.
   (`Dockerfile`, `Dockerfile.control-plane`).
4. Rolling restarts of runtime replicas are safe when sessions use a shared
   migrated `PostgresSessionProvider`; the default in-memory provider remains
   single-process. Durable Control Plane deployments must select Kubernetes;
   its status/list paths rehydrate labelled workloads after a restart.

## Remaining operational work

- Real Kind-cluster recovery acceptance and replica-safe deployment operation
  ownership
- Distributed A2A active-task state and cancellation semantics (P2.4)
- Replica-wide capability telemetry and gateway-level quota policy
