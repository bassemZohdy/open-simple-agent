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
replica. Production deployments must enforce the same policy at a shared API
gateway/service mesh until a replica-safe application store is selected.

Model, native-tool, and MCP spans emit bounded capability counters and may be
sent to an optional sink. Events contain only kind, capability name, outcome,
stable error code, and duration; prompts, arguments, credentials, and outputs
are excluded. Sink failures are isolated from agent behavior.
