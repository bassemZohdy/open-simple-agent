# ADR-004: Control Plane persistence — PostgreSQL repositories and Alembic

## Status

Accepted

## Date

2026-08-31

## Owners

Open Simple Agent maintainers

## Context

The Control Plane stores agent records, versions, templates, and resource
catalogs in module-level in-memory structures: state is lost on restart and
two replicas would diverge. P1.1 requires durable state shared across
replicas. The database stack was ratified with ADR-003's work: PostgreSQL 16,
SQLAlchemy 2.0 async, Alembic.

## Decision drivers

- Two replicas must share consistent state; restarts must not lose records or
  version history.
- The runtime (data plane) must keep working against a standalone database
  without the Control Plane.
- Existing in-memory behavior remains the default for tests and development.
- Schema changes must be reviewable and repeatable (migrations, not ad-hoc
  DDL).

## Decision

- **Repositories.** The Control Plane gains abstract repository interfaces —
  `AgentRepository` (records + versions), `ResourceDefinitionRepository`
  (models, tools, skills, MCPs, memory policies), and `DeploymentRecordRepository`
  (persisted deployment intent; wired to providers in P1.5) — in
  `osa.control_plane.backend.repositories`. The existing in-memory
  implementations remain the default; PostgreSQL implementations live beside
  them and are selected by configuration.
- **Stack.** SQLAlchemy 2.0 (async, Core queries) over asyncpg, as the
  optional `osa-control-plane[postgres]` extra. Alembic owns the schema; the
  app never creates tables.
- **Schema (migration 0001).** `osa_agents` (unique name, status,
  current_version, JSONB skills/labels), `osa_agent_versions` (unique
  `(agent_id, version)`, JSONB definition snapshot, cascade delete with the
  agent), `osa_resource_definitions` (tenant owner, unique
  `(tenant_id, kind, name)`, JSONB spec; migration 0005). Existing rows use
  the empty-string shared scope, which the application exposes as no tenant.
  Timestamps are UTC. The runtime-owned `osa_memory_entries` table stays
  bootstrap-created by the runtime (`IF NOT EXISTS`, ADR-003) because the
  runtime deploys independently; the two schemas coexist idempotently.
- **Startup migration policy.** Migrations are an explicit operational step
  (`osa-cp-migrate` console script wrapping `alembic upgrade head`),
  not auto-run at app startup — auto-migrating from several replicas
  simultaneously is a race. Application startup verifies connectivity only.
  CI applies migrations before integration tests.
- **Consistency.** Writes run in transactions; agent names and
  `(agent_id, version)` are enforced by unique constraints (mapping
  `IntegrityError` to the existing typed errors). Record updates use
  compare-and-set on `current_version` for optimistic concurrency; lifecycle
  transitions select the row `FOR UPDATE` inside the transaction and reject
  invalid moves.
- **Templates** remain code-defined built-ins (rebuilt at startup): they are
  release artifacts of the Control Plane, not user state. If user-defined
  templates arrive (P1.2), they gain a repository then.
- **Configuration.** `OSA_CONTROL_PLANE_DATABASE_URL` selects the PostgreSQL
  repositories when the app is created via
  `create_control_plane_app()`; unset means in-memory (current behavior,
  unchanged for tests and development).

## Consequences

### Positive

- Records and version history survive restarts; replicas share one state.
- Resource definitions persist ahead of the P1.2 resource APIs and are
  isolated by tenant namespace when authentication is enabled.
- The typed error contract (`DuplicateAgentError`,
  `DuplicateVersionError`, `InvalidTransitionError`) is preserved across
  backends, so the API error mapping is backend-agnostic.

### Negative or trade-offs

- Two code paths (in-memory, PostgreSQL) must stay behaviorally aligned;
  the shared abstract interface plus a common contract test suite covers
  this.
- Operations must run migrations before/with rollouts (explicit policy).

## Validation

- PostgreSQL integration tests (skipped without `OSA_TEST_DATABASE_URL`,
  CI service container): restart survival, two repository instances seeing
  consistent state, unique-name/version conflicts, optimistic-concurrency
  conflicts, cascade version history, and equal resource names in separate
  tenant scopes.
- The existing API contract suite runs unchanged against the in-memory
  default.

## Follow-up

- [x] P1.2: expose resource definitions over the API with write-through
      persistence.
- [x] P1.5: implement `DeploymentRecordRepository` persistence.
- [ ] P2.2: audit metadata repository.
