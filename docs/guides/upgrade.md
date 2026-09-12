# Upgrade Guide

How to upgrade OSA services without losing state or availability. The
contract-relevant invariants: packages release in lockstep, the database
schema is Alembic-owned, and migrations are an explicit operational step.

## Before you upgrade

1. Read the [changelog](../../CHANGELOG.md) section for the target version —
   breaking changes are listed there.
2. Back up every configured PostgreSQL database. Control Plane records,
   resource definitions, deployment records, and audit events use
   `OSA_CONTROL_PLANE_DATABASE_URL`; memory entries and persistent sessions use
   the independent `OSA_MEMORY_DATABASE_URL` and `OSA_SESSION_DATABASE_URL`
   databases when configured.
3. Confirm the new images build in CI (the container job smoke-tests both
   images on every commit, so a green main implies buildable images).

## Versioning model

All four packages (`osa-generic-agent`, `osa-adk-runtime`,
`osa-langgraph-runtime`, `osa-control-plane`) release together in lockstep — one version across every
manifest, enforced by `tests/unit/test_versioning.py`. Upgrade all images to
the same version.

## Schema migrations

```
OSA_CONTROL_PLANE_DATABASE_URL=... uv run osa-cp-migrate
OSA_MEMORY_DATABASE_URL=... uv run osa-memory-migrate
OSA_SESSION_DATABASE_URL=... uv run osa-session-migrate
OSA_A2A_TASK_DATABASE_URL=... uv run osa-a2a-migrate
OSA_CAPABILITY_TELEMETRY_DATABASE_URL=... uv run osa-capability-telemetry-migrate
```

- Migrations are forward-only in normal operation; each has a `downgrade()`.
- Run `osa-a2a-migrate` when durable inbound A2A task state is configured;
  it provisions the SDK task table, OSA ownership table, and schema-version-2
  append-only event table before any runtime replica is rolled out. The
  bounded polling relay reads that table by resumable cursor, with
  independent-worker PostgreSQL acceptance for takeover fencing and late
  events, but is not a public route yet. Runtime startup validates the schema
  and does not
  auto-create it. Existing schema version-1 installations must run this
  command before starting the upgraded runtime.
- Apply migrations **before** rolling out the new replicas — the policy is
  explicit migration, never auto-migrate at startup.
- Migrations are additive-first: new columns/tables land with server
  defaults so the previous version keeps working against the migrated
  schema (enabling rolling rollbacks).
- Runtime memory and sessions have independent versioned migration histories;
  startup validates them and never auto-migrates.
- Shared capability telemetry has an independent schema-version row; run its
  migration before rolling out replicas when
  `OSA_CAPABILITY_TELEMETRY_DATABASE_URL` is configured.

## Runtime replicas

1. Roll the new runtime image with the same bundle mount and environment.
2. Sessions: with a shared persistent `SessionProvider`, in-flight
   conversations survive replica replacement; with the in-memory provider,
   sessions are per-process and drain on shutdown.
3. Verify with `GET /health/ready`, then a probe invocation.

## Deployments (Control Plane-managed)

- `POST /deployments/{id}/restart` relaunches with the same identity.
- `POST /deployments/{id}/rollback?version=X` relaunches from an earlier
  immutable version snapshot of the agent definition — use it to revert a
  bad agent-definition rollout independently of image rollouts.

Deployment retry identity, rollback stop/relaunch/persist ordering, and bundle
publication are hardened. Durable Control Plane deployments must select the
Kubernetes provider; its status/list paths rehydrate labelled workloads after
a Control Plane restart. The local provider remains a single-process
development topology.

## Agent definitions

Definitions are validated at load and at reference-resolution time; an
invalid definition or a dangling resource reference aborts startup before
readiness. When upgrading, re-check any definition that used newly
restricted fields — validation failures are deterministic and reported at
startup.

## Downgrading

- Images: roll back to the previous image tag. To move the mutable `latest`
  channel back to an older digest without rebuilding, dispatch the
  `Rollback image channel` workflow from `main` with the component and
  `sha256:` digest — immutable version tags are never rewritten and the
  digest-bound signatures and attestations stay valid.
- Schema: only if the release notes confirm the newer migration is
  compatible with the older code (additive migrations are; destructive ones
  are not). Otherwise restore the database backup.
