# ADR-010: Bounded HTTP capacity and capability telemetry

## Status

Accepted for the local runtime/control-plane slice and the optional shared
PostgreSQL capability sink

## Decision

The HTTP applications expose an opt-in, fixed-window request budget configured
by `OSA_RATE_LIMIT_REQUESTS`, `OSA_RATE_LIMIT_WINDOW_SECONDS`, and
`OSA_RATE_LIMIT_BURST`. The budget key is method + route + a one-way caller
identity digest, so bearer material is never stored or emitted. Exhausted
requests return `429`, `Retry-After`, and bounded `X-RateLimit-*` headers.

The built-in store is process-local and suitable for development or a single
replica. When `OSA_RATE_LIMIT_DATABASE_URL` is configured, both services use
an async SQLAlchemy PostgreSQL-compatible store with atomic fixed-window
conflict updates and stale-window pruning. PostgreSQL is the shared-production
choice; SQLite is an explicit local option for tests and single-process use.
`osa-rate-limit-migrate` provisions the bounded window table, and service
startup validates/initializes it before readiness. This shares request budgets
across replicas only when the selected database is shared, but does not claim
ownership of long-running A2A/deployment operations or gateway-wide quotas.

Model, native-tool, and MCP spans emit bounded capability counters and may be
sent to an optional sink. The default sink is disabled; operators may select
the bounded JSONL sink with `OSA_CAPABILITY_TELEMETRY_PATH` and
`OSA_CAPABILITY_TELEMETRY_MAX_BYTES`, or provide a sink programmatically.
Events contain only bounded capability kind/name, outcome, stable error code,
duration, event ID, operation/tenant correlation, producer time, and optional
sequence metadata; prompts, arguments, credentials, and outputs are excluded.
Sink failures are isolated from agent behavior. The file sink is process-local
and does not claim replica-wide ordering or deduplication.

For replica-wide capability events, OSA also provides the optional
`PostgresCapabilityTelemetrySink`, selected with
`OSA_CAPABILITY_TELEMETRY_DATABASE_URL`. This is a PostgreSQL-only provider;
the table and its schema-version row are created by the explicit
`osa-capability-telemetry-migrate` command and validated before runtime
readiness. A configured database is authoritative: an unavailable or
unmigrated schema fails startup rather than falling back to the JSONL or
in-memory sink. The database DSN and JSONL path are mutually exclusive.

The shared event contract contains only bounded metadata:

- `event_id` is the idempotency key; duplicate deliveries are ignored;
- `tenant_id` and `operation_id` scope ownership and correlation;
- `sequence` preserves producer order within an operation when available;
- `occurred_at` records producer time, while database `ingested_at` is the
  canonical cross-replica ordering clock, with `event_id` as the tie-breaker;
- capability kind/name, outcome, stable error code, and duration exclude
  prompts, inputs, outputs, credentials, and tool arguments.

The operator-owned retention window defaults to 30 days and is configured by
`OSA_CAPABILITY_TELEMETRY_RETENTION_DAYS`. Writes prune expired rows, and the
sink exposes explicit tenant deletion for data-retention workflows. Runtime
sink failures are isolated from agent behavior after startup, while the
operator remains responsible for backups, retention scheduling, and database
access controls.
