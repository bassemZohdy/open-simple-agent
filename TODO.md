# Open Simple Agent — Active Backlog

Updated 2026-09-12. This file contains only unfinished, deferred, or
deliberately gated work. Completed implementation history is recorded in
`CHANGELOG.md` and git history.

A task is complete only when implementation, automated tests, relevant
documentation, and appropriate failure/security behavior are complete.

## Current status

The runnable-agent gate, managed-platform foundation, Manager Agent surface,
Control Plane, runtime images, release automation, current English Control
Panel, durable runtime sessions, migration-owned memory schema,
operator-selected deployment provider, capability telemetry, identity contract
coverage, and opt-in HTTP rate limiting (including a PostgreSQL shared store)
are implemented and covered by tests/CI.

Production-readiness limits are:

- runtime sessions remain in-memory unless the bundle opts into the durable
  provider and its migration is applied;
- memory PostgreSQL requires its independent versioned migration;
- resource records are durable and route/deployment reads reconcile local
  catalogs; PostgreSQL cross-replica acceptance is covered in CI;
- PostgreSQL external-agent records are durable, while the default in-memory
  registry and local provider process state remain process-local;
- durable Control Planes require an external workload provider; local-provider
  state remains process-local by design;
- outbound A2A, Streamable HTTP MCP, and OAuth token destinations have an
  application-level URL/DNS/redirect policy, with network egress still
  required as defense in depth;
- A2A task records can be durable/shareable with the opt-in SDK PostgreSQL
  store, but active-task ownership/cancellation/recovery and replica-wide
  telemetry collection are not distributed-safe; long-running operation
  ownership and global gateway quotas remain deployment concerns;
- translated locales, deployment-specific browser OIDC, package publication,
  and the first public release remain open; the Kubernetes lifecycle acceptance
  passes in CI, while this workstation cannot run it locally because Docker is
  unavailable.

## Pending work and gates

The following are the current blockers or decision gates:

- **Infrastructure-gated:** multi-process deployment-operation ownership
  requires a cluster-capable CI environment; concrete enterprise
  identity-source acceptance requires a selected provider and test tenant.
  The real Kind lifecycle workflow passes in CI; OpenShift behavior remains a
  separate provider gate.
- **Architecture-gated:** distributed A2A active-task ownership/cancellation
  and replica-wide capability telemetry require an approved ownership,
  retention, ordering, and deduplication design.
- **Product-gated:** translated locales, browser OIDC issuer/client/redirect
  semantics, package registry publication, and the first public release need
  explicit product decisions.
- **Requirement-gated:** the deferred section below remains intentionally
  paused until a concrete product or integration requirement exists.

## Recommended next task

Define distributed operation ownership. The next decision gates are distributed
A2A active-task state, shared telemetry collection, translated locales,
browser OIDC contracts, package publication, and the first public release.

---

# P1 — Managed platform

## Kubernetes deployment provider — CI VALIDATED

Deployment/Service generation, bundle ConfigMaps, Secret references, probes,
hardened pod security, scale/restart/rollback/status/log operations, provider
selection, and OSA identity labels exist. Startup reconciliation and a
cancellable polling watcher now refresh persisted records from provider-owned
workloads after Control Plane restarts. The real Kind lifecycle acceptance
workflow passes in CI; this workstation cannot execute it locally while Docker
is unavailable.

- [x] Validate deploy/readiness/scale/restart/rollback/recovery against Kind in
  CI.
- [ ] Keep OpenShift-specific behavior separate from generic Kubernetes code.

---

# P2 — Production controls

## Enterprise identity lifecycle — PARTIALLY COMPLETE

Claim-driven lifecycle semantics, OIDC/JWKS validation, RFC 7662 opaque-token
introspection, and contract-level lifecycle tests are implemented. Concrete
identity-source acceptance remains open.

- [ ] Run the lifecycle acceptance suite against a selected enterprise
  identity source and test tenant.

## Distributed A2A active-task state — PENDING

The SDK task record can be persisted in PostgreSQL with
`OSA_A2A_TASK_DATABASE_URL`, but the active executor registry is process-local.
Completed-task lookup is restart-safe only when the durable store is enabled;
in-flight work still needs an ownership and recovery protocol.

- [ ] Implement and wire replica-consistent task state.
- [ ] Add multi-replica creation, completion, failure, lookup, and recovery
  acceptance tests without tenant/caller leakage.
- [ ] Define cancellation ordering and protection against late events/retries.

## Capability-level audit telemetry — PARTIALLY COMPLETE

Management, runtime-boundary, and auth-denial audits exist. Model/native-tool/
MCP capability metrics and a bounded payload-free local JSONL sink are
implemented for a single process.

- [ ] Select a shared collector or durable replica-wide sink contract with
  ordering, deduplication, and tenant-retention ownership.

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
