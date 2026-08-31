# ADR-003: Memory persistence — PostgreSQL via SQLAlchemy async

## Status

Accepted

## Date

2026-08-31

## Owners

Open Simple Agent maintainers

## Context

Memory is currently in-process (`InMemoryProvider`): it does not survive
restarts and cannot be shared across replicas. P1.4 requires a first
persistent provider. The project has ratified a database stack (PostgreSQL
16, SQLAlchemy 2.0 async, Alembic) for the Control Plane (P1.1); memory
persistence lands first in the build order, so it introduces that stack.
Access requirements for memory at this stage are scope-keyed lookup plus
case-insensitive substring search (the semantics the in-memory provider
already exposes) — semantic/vector retrieval is explicitly deferred in the
backlog.

## Decision drivers

- One storage stack for the project (no second database to operate).
- Search semantics must mirror the in-memory provider (case-insensitive
  substring) so behavior is consistent across providers.
- Provider/DSN must be externalized configuration (never inside agent
  definitions); credentials resolve via the `SecretResolver` contract or a
  DSN from the environment.
- Test environments stay offline and skip cleanly when no database is
  configured.

## Considered options

1. **PostgreSQL + SQLAlchemy async + ILIKE search** — one stack, sufficient
   for scoped lookup and substring retrieval; pgvector can be added later
   without changing providers' contracts.
2. **Redis** — strong for TTL/ephemeral, weaker for the scan-style substring
   search semantics; a second stack to operate.
3. **pgvector now** — premature: semantic retrieval is deferred; no embedding
   pipeline exists.

## Decision

- `PostgresMemoryProvider` (`osa.runtimes.adk.postgres_memory`) stores memory
  entries in a dedicated `osa_memory_entries` table: append-per-key semantics,
  `(scope, scope_id)` isolation, `ILIKE` substring search, timestamps.
- The stack is **SQLAlchemy 2.0 async over asyncpg**, pinned as the optional
  extra `osa-adk-runtime[postgres]`; the DSN comes from
  `OSA_MEMORY_DATABASE_URL`. When the variable is unset the runtime uses the
  in-memory provider; when set, the provider is created and its schema is
  ensured at startup (connectivity failures abort startup before readiness).
- Per-scope limits (`max_entries`) and retention (`retention_days`) are
  enforced in SQL through the provider contract's `enforce()`; the runtime
  applies them after every write and before reads, from the resolved
  `MemoryPolicy` (policy fields are authoritative when a policy is attached;
  `spec.memory` fields apply otherwise).
- **Extraction remains explicit**: `auto_extract` in `MemoryPolicy` is
  reserved — raw interactions are never persisted automatically. A
  policy-driven extraction pipeline is future work and will arrive as an
  explicit, opt-in behavior.
- The table is created with `CREATE TABLE IF NOT EXISTS` until the Alembic
  migration tooling introduced with P1.1 takes over schema management.

## Consequences

### Positive

- Memory survives restarts and is shared across replicas; isolation tests
  cover user/agent/tenant/application scopes.
- One operational database for memory and (upcoming) Control Plane state.

### Negative or trade-offs

- `ILIKE` substring search is linear at scale; an index on
  `(scope, scope_id, key, created_at)` covers the common paths, and pgvector
  remains available if semantic search becomes a requirement.
- Two sources of schema truth exist until Alembic lands (P1.1).

## Validation

- Unit tests cover policy resolution, scope-ID derivation, and in-memory
  limit/retention enforcement.
- Integration tests run against a real PostgreSQL 16 (`OSA_TEST_DATABASE_URL`
  in CI as a service container): persistence across provider "restarts",
  scope isolation, limit eviction, and retention purge.

## Follow-up

- [ ] Replace bootstrap `CREATE TABLE IF NOT EXISTS` with Alembic migrations
      in P1.1.
- [ ] Revisit pgvector when semantic retrieval becomes a concrete
      requirement.
