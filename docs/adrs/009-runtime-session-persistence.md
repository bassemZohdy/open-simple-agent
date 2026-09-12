# ADR-009: Runtime session persistence — explicit PostgreSQL provider

## Status

Accepted

## Decision

`spec.session.persistence: true` selects `PostgresSessionProvider` in the ADK
runtime. The DSN is supplied only through `OSA_SESSION_DATABASE_URL`; an
unconfigured or unmigrated persistent store fails startup rather than falling
back to an in-memory session manager. `osa-session-migrate` owns a small,
versioned schema independent of the Control Plane Alembic history.

The provider preserves the generic contract: server-issued IDs, exact
`(agent_name, user_id, tenant_id)` ownership, TTL deletion, bounded history,
metadata isolation, and optimistic revision checks on save. A stale writer gets
`session_concurrent_update` and cannot overwrite a newer conversation.

The default `false` setting remains process-local for tests and development.
Operators must back up the session database before package upgrades and apply
the migration before starting a persistent runtime replica.
