# Upgrade Guide

How to upgrade OSA services without losing state or availability. The
contract-relevant invariants: packages release in lockstep, the database
schema is Alembic-owned, and migrations are an explicit operational step.

## Before you upgrade

1. Read the [changelog](../CHANGELOG.md) section for the target version —
   breaking changes are listed there.
2. Back up the PostgreSQL database (all OSA state: Control Plane records,
   resource definitions, deployment records, audit events, memory entries).
3. Confirm the new images build in CI (the container job smoke-tests both
   images on every commit, so a green main implies buildable images).

## Versioning model

All three packages (`osa-generic-agent`, `osa-adk-runtime`,
`osa-control-plane`) release together in lockstep — one version across every
manifest, enforced by `tests/unit/test_versioning.py`. Upgrade all images to
the same version.

## Schema migrations

```
OSA_CONTROL_PLANE_DATABASE_URL=... uv run osa-cp-migrate
```

- Migrations are forward-only in normal operation; each has a `downgrade()`.
- Apply migrations **before** rolling out the new replicas — the policy is
  explicit migration, never auto-migrate at startup.
- Migrations are additive-first: new columns/tables land with server
  defaults so the previous version keeps working against the migrated
  schema (enabling rolling rollbacks).

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
