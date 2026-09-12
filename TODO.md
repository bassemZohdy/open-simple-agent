# Open Simple Agent — Active Backlog

Updated 2026-09-12. This file contains only unfinished, deferred, or
deliberately gated work. Completed implementation history is recorded in
`CHANGELOG.md` and git history.

A task is complete only when implementation, automated tests, relevant
documentation, and appropriate failure/security behavior are complete.

## Current status

The runnable-agent gate, managed-platform foundation, Manager Agent surface,
Control Plane, runtime images, release automation, current English/Arabic
Control Panel, durable runtime sessions, migration-owned memory schema,
operator-selected deployment provider, capability telemetry, identity contract
coverage, opt-in HTTP rate limiting (including a PostgreSQL shared store), and
English/Arabic Control Panel locale coverage are implemented and covered by
tests/CI.

The MCP runtime supports the official SDK 1.x and 2.x compatibility lines;
dual-major protocol and ADK Runner coverage runs in CI. Resource/prompt
exposure and legacy SSE remain intentionally deferred.

Persistence selection is externalized per subsystem. A configured PostgreSQL
DSN is authoritative and must fail closed when unavailable; an unset DSN uses
only the subsystem's documented process-local default. No general-purpose OSA
SQLite provider is currently defined; SQLite remains an explicit local-only
follow-up, never a silent PostgreSQL fallback. Some lower-level A2A and
rate-limit tests use SQLite where their underlying stores permit it, but that
does not establish shared-production support.

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
- deployment-specific browser OIDC, package publication, and the first public
  release remain open; the Kubernetes lifecycle acceptance passes in CI, while
  this workstation cannot run it locally because Docker is unavailable.

## Pending work and gates

The following are the current blockers or decision gates:

- **Infrastructure-gated:** multi-process deployment-operation ownership
  requires a cluster-capable CI environment; concrete enterprise
  identity-source acceptance requires a selected provider and test tenant.
  The real Kind lifecycle workflow passes in CI; OpenShift behavior remains a
  separate provider gate.
- **Architecture-gated:** distributed A2A active-task ownership/cancellation
  and replica-wide capability telemetry require approval of the proposed
  ownership contract in `docs/adrs/011-distributed-operation-ownership.md`,
  plus the telemetry retention/ordering decision.
- **Product-gated:** browser OIDC issuer/client/redirect semantics, package
  registry publication, and the first public release need explicit product
  decisions. English and Arabic are the currently supported Control Panel
  locales; adding further locales remains a product decision.
- **Requirement-gated:** the deferred section below remains intentionally
  paused until a concrete product or integration requirement exists.

## Recommended next task

Implement the persistence provider hierarchy described below, starting with
explicit fail-closed provider selection and a local-only SQLite design. The
MCP SDK 1.x/2.x compatibility slice is covered by the client, ADK Runner, and
dual-major CI tests; distributed operation ownership remains the next
architecture gate after persistence policy work.

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


---

# P2 — Production controls

## Persistence provider hierarchy — PENDING

Persistence is currently externalized per subsystem: PostgreSQL is selected by
an explicit service DSN, while allowed development/test paths remain
process-local when no DSN is configured. A configured DSN must never silently
downgrade to SQLite or memory after a connectivity or migration failure.
No general-purpose SQLite provider is currently wired for the PostgreSQL-only
Control Plane, memory, or durable-session surfaces; if added, it must be an
explicit single-process option with its own migration and backup guidance.

- [ ] Add explicit SQLite providers only for local single-process use, with
  backend-specific migrations, locking/busy-timeout settings, file permissions,
  backup guidance, and clear rejection for shared-replica deployments.
- [ ] Add deployment-policy validation so durable/production surfaces reject
  process-local memory or SQLite providers, and add an explicit policy mode for
  deployments that require shared durable state.
- [ ] Add a provider matrix covering Control Plane, memory, sessions, A2A task
  records, and rate limits across PostgreSQL, SQLite where supported, and
  in-memory development modes, including restart and failure behavior.

## Enterprise identity lifecycle — PARTIALLY COMPLETE

Claim-driven lifecycle semantics, OIDC/JWKS validation, RFC 7662 opaque-token
introspection, and contract-level lifecycle tests are implemented. A
provider-neutral opt-in acceptance harness and manual CI workflow now cover a
real access token, expected subject/tenant, and a protected Control Plane
route. Concrete identity-source acceptance remains open.

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
- [ ] Define cross-replica cancellation ordering and protection against late
  events/retries; the local runtime now emits one canceled terminal state.

## Capability-level audit telemetry — PARTIALLY COMPLETE

Management, runtime-boundary, and auth-denial audits exist. Model/native-tool/
MCP capability metrics and a bounded payload-free local JSONL sink are
implemented for a single process.

- [ ] Select a shared collector or durable replica-wide sink contract with
  ordering, deduplication, and tenant-retention ownership.

# P3 — Product surface and distribution

## Control Panel — PARTIALLY COMPLETE

The panel includes an authenticated shell, English and Arabic locale coverage
with RTL layout, agents/resources, authoring, lifecycle/deployments,
audit/metrics, A2A/runtime consoles, safe snapshots, responsive behavior, and
loading/empty/error recovery. Dates continue to use the browser locale/timezone
through `Intl`; API and machine values remain stable.

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
