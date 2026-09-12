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

Persistence selection is externalized per subsystem. PostgreSQL is the shared,
durable provider; Control Plane, memory, and durable sessions also have
explicit file-backed SQLite providers for local single-process use. An unset
DSN uses only the subsystem's documented process-local default, and a
configured DSN is authoritative and fail-closed. `OSA_PERSISTENCE_POLICY=shared`
rejects process-local and SQLite state for enabled surfaces and requires the
shared PostgreSQL/Kubernetes posture.

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
  store. Tenant/caller-scoped ownership leases, fencing, durable cancellation,
  expired-lease takeover, explicit schema migration, fenced SDK task saves,
  remote-handler cancellation waiting, expired-owner cancellation, read-only
  terminal replay, terminal-event drain-before-release, and fail-closed
  owner-loss handling are implemented. The SDK active-task registry and
  multi-process streaming/late-event acceptance remain incomplete; replica-wide
  capability telemetry now has an optional migration-owned PostgreSQL sink
  with deduplication, ordering, and tenant-retention controls;
  long-running deployment-operation ownership and global gateway quotas remain
  deployment concerns;
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
- **Architecture-gated:** completing the remaining distributed A2A
  multi-process/active-task streaming and late-event contract requires approval
  of `docs/adrs/011-distributed-operation-ownership.md`.
- **Product-gated:** browser OIDC issuer/client/redirect semantics, package
  registry publication, and the first public release need explicit product
  decisions. English and Arabic are the currently supported Control Panel
  locales; adding further locales remains a product decision.
- **Requirement-gated:** the deferred section below remains intentionally
  paused until a concrete product or integration requirement exists.

## Recommended next task

Complete true multi-process A2A active-task acceptance. The persistence
provider hierarchy, shared-policy gate, scoped A2A leases, fenced SDK task
saves, independent-handler lookup/cancellation acceptance, expired-owner
cancellation, terminal read-only replay, and the conservative fail-closed
owner-loss policy are implemented and covered by focused tests. The remaining
architecture gate is PostgreSQL-backed multi-process handler/active-task
streaming and late-event acceptance.

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

## Distributed deployment-operation ownership — PENDING

Deployment intent, provider reconciliation, and Kubernetes lifecycle acceptance
exist, but mutating deployment operations are not yet serialized across
multiple Control Plane replicas. The implementation must reuse the
lease/fencing boundary proposed in ADR-011, keep provider observation separate
from owned intent, and fail closed on stale workers rather than replaying
unknown side effects.

- [x] Decide that all mutating operations for one deployment serialize under
  one key while read-only observations remain concurrent. Deploy creation uses
  the agent key until a deployment ID exists; stop, restart, and rollback use
  the deployment key.
- [x] Implement migration-owned deployment-operation ownership with tenant and
  resource scope, leases, fencing epochs, bounded takeover, and a stable
  operation ID in migration 0010. PostgreSQL is shared; SQLite/in-memory is
  process-local by design.
- [x] Pass operation metadata to deployment providers and prevent stale
  deploy/stop/restart/rollback workers from persisting durable state. The
  Kubernetes provider records operation metadata in workload annotations;
  provider-only `scale()` is not exposed through the Control Plane service yet.
- [ ] Add PostgreSQL acceptance with two independent workers covering command
  serialization, cancellation/expiry, late results, tenant isolation, and
  restart/reconciliation recovery.

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
`OSA_A2A_TASK_DATABASE_URL`. OSA now adds an explicit versioned ownership table
with tenant/caller scope, leases, fencing, durable cancellation, and expired
lease takeover; run `osa-a2a-migrate` before startup. Durable task mutations
now carry the acquired ownership snapshot through the SDK call context and
hold the ownership-row lock while saving, so stale workers fail closed. The
active executor registry remains process-local. Independent handlers can look
up shared active tasks, wait for a remote owner to publish cancellation, and
safely take over an expired owner lease; terminal snapshots are replayed through
read-only SDK events so a second handler does not write duplicate task history.
Process-boundary PostgreSQL acceptance covers task creation, lookup, remote
cancellation, and crash/lease-expiry recovery. Multi-process streaming and
late-event acceptance remain open; owner loss is fail-closed by default.

- [x] Fence SDK task mutations and add explicit owner-loss handling. The
  database adapter rejects missing, expired, or superseded fences without
  publishing a synthetic failure from the stale worker.
- [x] Add PostgreSQL task-store/ownership acceptance for creation, completion,
  failure, lookup, recovery, and tenant/caller isolation across two independent
  ownership workers.
- [x] Add independent-handler acceptance for shared active-task lookup,
  remote cancellation waiting, and expired-owner cancellation takeover; the
  SDK active-task registry remains local to each process.
- [x] Complete the cross-replica cancellation request path: remote handlers
  wait for the durable terminal task, expired owners can be fenced out and
  cancellation can be finalized safely, and terminal snapshots replay without
  a second durable write.
- [x] Add process-boundary PostgreSQL acceptance for active-task creation,
  shared lookup, remote cancellation, and crash/lease-expiry recovery; the SDK
  active-task registry remains local to each process.
- [ ] Add multi-process/multi-worker PostgreSQL acceptance for streaming and
  late events; the SDK active-task registry remains local to each process.
- [x] Decide and implement the non-idempotent owner-loss/retry policy: an
  expired owner is fenced out, the durable task is finalized as failed with a
  stable owner-loss message, and the replacement never replays unknown
  model/tool side effects. Explicit idempotent replay remains a future opt-in
  contract, not a default.

## Capability-level audit telemetry — COMPLETE

Management, runtime-boundary, and auth-denial audits exist. Model/native-tool/
MCP capability metrics and a bounded payload-free local JSONL sink are
implemented for a single process. An optional migration-owned PostgreSQL sink
provides replica-wide durable events with stable IDs, tenant/operation
metadata, deterministic ingestion ordering, deduplication, retention pruning,
and explicit tenant deletion.

- [x] Select and implement the shared PostgreSQL capability telemetry contract
  with ordering, deduplication, and tenant-retention ownership.

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
