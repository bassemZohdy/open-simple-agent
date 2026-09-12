# ADR-010: Bounded HTTP capacity and capability telemetry

## Status

Accepted for the local runtime/control-plane slice

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
Events contain only kind, capability name, outcome, stable error code, and
duration; prompts, arguments, credentials, and outputs are excluded. Sink
failures are isolated from agent behavior. The file sink is process-local and
does not claim replica-wide ordering or deduplication.
