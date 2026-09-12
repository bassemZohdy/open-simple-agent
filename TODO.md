# Open Simple Agent — Active Backlog

Updated 2026-09-12. This file contains only unfinished, deferred, or
deliberately gated work. Completed implementation history is recorded in
`CHANGELOG.md` and git history.

A task is complete only when implementation, automated tests, relevant
documentation, and appropriate failure/security behavior are complete.

## Current status

The runnable-agent gate, managed-platform foundation, Manager Agent surface,
Control Plane, runtime images, release automation, and current English
Control Panel are implemented and covered by CI.

Production-readiness limits are:

- runtime sessions are in-memory unless a durable provider is selected and
  implemented;
- memory PostgreSQL still uses transitional bootstrap DDL;
- resource records are durable and route/deployment reads reconcile local
  catalogs, while live PostgreSQL cross-replica acceptance remains gated;
- PostgreSQL external-agent records are durable, while the default in-memory
  registry and local provider process state remain process-local;
- local deployment-provider restart reconciliation and multi-replica ownership
  remain undefined;
- outbound A2A, Streamable HTTP MCP, and OAuth token destinations have an
  application-level URL/DNS/redirect policy, with network egress still
  required as defense in depth;
- A2A task state, capability telemetry, and HTTP capacity controls are not
  replica-safe or fully defined;
- translated locales, deployment-specific browser OIDC, package publication,
  and the first public release remain open; Kubernetes follow-up is paused.

## Recommended next task

Run the PostgreSQL cross-replica acceptance workflow, then define local-provider
recovery/ownership semantics. The next gated product decisions are durable
sessions, migration-owned memory schema, packaged deployment topology, and
distributed A2A task state.

---

# P1 — Runtime durability

## Persistent runtime sessions — PENDING

`SessionProvider` defaults to the in-memory `SessionManager`. It enforces
ownership, TTL, and bounded history, but state is lost with a runtime process.

- [ ] Select the persistent-provider contract and configuration semantics for
  `spec.session.persistence`, including database lifecycle and migration
  ownership.
- [ ] Implement and wire a durable provider without weakening ownership,
  expiry, bounded history, or metadata redaction.
- [ ] Add restart, expiry, ownership, concurrent-update, and cross-process
  tests; document backup, upgrade, and recovery behavior.

## Memory schema ownership — PENDING

`PostgresMemoryProvider` still creates `osa_memory_entries` with bootstrap DDL.

- [ ] Select migration ownership and operational commands for the independent
  memory database.
- [ ] Replace runtime bootstrap DDL with an explicit versioned migration path.
- [ ] Add upgrade/rollback coverage and document backup and startup ordering.

---

# P1 — Managed platform

## Kubernetes deployment provider — PAUSED

Deployment/Service generation, bundle ConfigMaps, Secret references, probes,
hardened pod security, scale/restart/rollback/status/log operations, and OSA
identity labels exist. Resume only when explicitly reprioritized.

- [ ] Wire packaged Control Plane provider selection/configuration.
- [ ] Validate deploy/readiness/scale/restart/rollback/recovery against Kind or
  another real cluster in CI.
- [ ] Add status-watch and recovery behavior for Control Plane restarts and
  already-running workloads.
- [ ] Document RBAC, namespaces, image-pull secrets, network policy, resource
  limits, and upgrades.
- [ ] Keep OpenShift-specific behavior separate from generic Kubernetes code.

## Packaged Control Plane deployment launcher — PENDING

The management image does not package `osa-runtime`, while the default local
deployment command invokes it.

- [ ] Decide whether to package the launcher, require an external provider, or
  make provider selection explicit.
- [ ] Add image-level integration proving launch, probe, stop, and observation.
- [ ] Document split-image and colocated-process topologies and command-template
  security boundaries.

---

# P2 — Production controls

## Enterprise identity lifecycle — PARTIALLY COMPLETE

Claim-driven lifecycle semantics and opaque-token introspection validation are
implemented; concrete identity-source acceptance remains open.

- [ ] Add introspection liveness, key rotation, disabled-identity, and
  role-change propagation tests after selecting an identity source.

## Distributed A2A task state — PENDING

The runtime A2A executor and SDK task store are process-local.

- [ ] Select durable task storage, retention, ownership, and recovery rules.
- [ ] Implement and wire replica-consistent task state.
- [ ] Add multi-replica creation, completion, failure, lookup, and recovery
  acceptance tests without tenant/caller leakage.
- [ ] Define cancellation ordering and protection against late events/retries.

## Capability-level audit telemetry — PENDING

Management, runtime-boundary, and auth-denial audits exist; per-capability
model/native-tool/MCP telemetry does not.

- [ ] Define taxonomy, redaction, retention, sampling, and performance policy.
- [ ] Implement optional sink and durable persistence without prompts, outputs,
  credentials, or unbounded tool payloads.
- [ ] Add model/tool/MCP success, failure, timeout, and isolation tests/docs.

## Rate limiting and quotas — PENDING

Neither HTTP application enforces replica-safe rate limits, concurrency quotas,
or a `429`/`Retry-After` contract.

- [ ] Define route, principal, tenant, burst, retry, and response-header rules.
- [ ] Select replica-safe enforcement/storage for streaming and long-running
  A2A/deployment operations.
- [ ] Add isolation, burst, streaming, A2A, metrics, and documentation tests.

---

# P3 — Product surface and distribution

## Control Panel — PARTIALLY COMPLETE

The English panel includes authenticated shell, agents/resources, authoring,
lifecycle/deployments, audit/metrics, A2A/runtime consoles, safe snapshots,
responsive behavior, and loading/empty/error recovery.

- [ ] Add translated-locale coverage while preserving accessibility and
  browser-locale timestamps.
- [ ] Define deployment-specific OIDC browser login/refresh after issuer,
  client, and redirect contracts are selected.
- [ ] Decide whether public agent-definition bundle import/export belongs in
  scope; resource import/export and server-side deployment export exist.

## Packaging, CI/CD, and release — PARTIALLY COMPLETE

Lockstep validation, Python distributions, signed/attested GHCR images, SBOMs,
and digest rollback automation exist.

- [ ] Decide whether Python packages need PyPI or another registry.
- [ ] Perform the first automated public release after intentionally selecting
  a version and moving its changelog entries out of `Unreleased`.

---

# Deferred until a concrete requirement

- [ ] Track upstream MCP SDK major changes and revisit the pin when MCP 2.x
  lands.
- [ ] MCP resources/prompts exposure and legacy SSE transport support.
- [ ] Configurable custom model-adapter registration after a second production
  adapter is required.
- [ ] Complete the bounded LangGraph contract/security/durability POC; see
  `docs/DAR-001-adk-vs-langchain-langgraph.md`.
- [ ] Multiple unrelated agents in one runtime process.
- [ ] Dynamic runtime plugin installation.
- [ ] Advanced semantic discovery and hosted marketplace.
- [ ] Advanced multi-tenancy and multi-region deployment.
- [ ] Agent delegation/consent beyond baseline A2A security.
- [ ] General human approval beyond management operations.
- [ ] Advanced memory extraction/consolidation and vector retrieval.
- [ ] Enterprise external policy engine until P2 identity work selects a need.

---

# Active review findings

Resolved findings are recorded in `CHANGELOG.md` and git history. Only
unresolved findings remain here.

## BF17 — Local-provider reconciliation

The local provider is process-local, and PostgreSQL records cannot rehydrate
child processes after restart. Graceful shutdown is wired, but recovery and
multi-replica ownership remain undefined.

- [ ] Define shutdown, orphan cleanup, restart/reconciliation, and ownership
  semantics, or make an external provider mandatory for production.
- [ ] Cover graceful shutdown, restart recovery, and persisted running records.

## BF19 — Resource-catalog cross-replica acceptance

Resource reads, activation, and deployment reconcile each tenant's local
catalog from durable storage. An acceptance test covers create/update/delete,
activation, and deployment across independently created app instances.

- [ ] Run that acceptance test against PostgreSQL in CI and retain the evidence.
