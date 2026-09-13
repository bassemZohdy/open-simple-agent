# Open Simple Agent — Active Backlog

Updated 2026-09-13. This file contains only unfinished, deferred, or
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
dual-major protocol and ADK Runner coverage runs in CI. Application-controlled
resource/prompt discovery and retrieval, bounded normalized payloads, and
explicit legacy SSE compatibility are implemented. LangGraph MCP integration
remains deferred.

Persistence selection is externalized per subsystem. PostgreSQL is the shared,
durable provider; Control Plane, memory, and durable sessions also have
explicit file-backed SQLite providers for local single-process use. An unset
DSN uses only the subsystem's documented process-local default, and a
configured DSN is authoritative and fail-closed. `OSA_PERSISTENCE_POLICY=shared`
rejects process-local and SQLite state for enabled surfaces and requires the
shared PostgreSQL/Kubernetes-or-OpenShift posture.

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
  owner-loss handling are implemented. Migration-owned event-cursor storage is
  provisioned as schema version 2 and a bounded cursor-relay foundation plus an
  explicit default-disabled SDK stream-handler adapter are available;
  independent PostgreSQL workers now accept takeover fencing, late-event
  rejection, ordered terminal delivery, and cursor replay. Public multi-process
  streaming route acceptance remains incomplete; replica-wide
  capability telemetry now has an optional migration-owned PostgreSQL sink
  with deduplication, ordering, and tenant-retention controls;
  deployment-operation ownership now has a migration-owned PostgreSQL lease,
  fencing, and stale-write guard for exposed mutations; independent-worker
  PostgreSQL acceptance now proves serialization, takeover, late-result
  rejection, tenant isolation, and recovery for the covered operations;
  global gateway quotas remain a deployment concern;
- deployment-specific browser OIDC and optional Python package publication
  remain open; public releases through `v0.1.2` are complete. The
  Kubernetes lifecycle acceptance passes in CI, while this workstation cannot
  run it locally because Docker is unavailable.

## Pending work and gates

The following are the current blockers or decision gates:

- **Infrastructure-gated:** concrete enterprise identity-source acceptance
  requires a selected provider and test tenant.
  The real Kind lifecycle workflow passes in CI. The dedicated OpenShift
  provider and Route generation are implemented; OpenShift still requires a
  selected target cluster and remains a separate acceptance gate.
- **Architecture-gated:** completing the remaining public distributed A2A
  multi-process/active-task streaming and late-event route contract requires approval
  of `docs/adrs/011-distributed-operation-ownership.md`.
- **Product-gated:** browser OIDC issuer/client/redirect semantics and Python
  package registry publication need explicit product decisions. The first
  public release is complete. English and Arabic are the currently supported
  Control Panel locales; adding further locales remains a product decision.
- **Requirement-gated:** the deferred section below remains intentionally
  paused until a concrete product or integration requirement exists.

## Decision worksheet (not yet accepted)

These are the remaining user-, provider-, or architecture-owned decisions.
The recommendations are review defaults only; they do not authorize scope or
enable a gated feature.

| Gate | Decision required | Review default | Unlocks |
|---|---|---|---|
| Enterprise identity | Select one real OIDC/JWKS or RFC 7662 provider and a non-production test tenant, including issuer, audience, expected subject, and tenant claims. | Use the existing provider-neutral harness against the organization’s existing non-production IdP; do not add provider-specific code to the core boundary. | Concrete identity lifecycle acceptance. |
| ADR-011 | Approve or revise the conservative lease/fencing/cancellation/event-cursor contract and its public-route acceptance criteria. | Accept the proposed decision as written; keep public A2A streaming disabled until the ADR is accepted and route-level acceptance passes. | Public cross-replica A2A streaming and late-event work. |
| OpenShift provider | Select a target OpenShift version/cluster and confirm Route, security-context, admission, and operator ownership requirements. | Keep standard Kubernetes as the CI-validated production target; validate the dedicated `oc`-backed provider against the selected environment before claiming OpenShift support. | OpenShift deployment-provider acceptance. |
| Browser OIDC | Choose deployment-owned issuer, client registration, redirect, refresh, logout, and token-storage semantics. | Authorization Code + PKCE with deployment-specific configuration; never place bearer tokens in URLs, source, logs, or persistent configuration. | Control Panel browser login/refresh implementation. |
| Public bundle import/export | Decide whether third parties may import/export public agent-definition bundles, and define compatibility, trust, and secret/reference rules. | Keep public definition import/export out of scope until a versioned compatibility and trust contract is approved; retain server-side deployment export and resource import/export. | Public bundle API and UI scope. |
| Python package publication | Select PyPI/another registry/private-only distribution and its support policy. | Keep the signed GitHub Release, GHCR, and Docker Hub paths as the baseline; publish Python packages only after a registry and support policy are selected. | Optional Python package-registry publication. |
| Further locales | Choose target markets, translation ownership, and locale acceptance criteria. | Keep English and Arabic supported; add locales only with an identified market and maintained translations. | Additional Control Panel locales. |

## Recommended next task

Review and approve the architecture-gated public A2A streaming and late-event
route contract. The durable cursor relay, independent-worker acceptance, and
explicit SDK stream-handler integration are complete; approval is the remaining
step before enabling the Agent Card capability in the normal runtime and
running production multi-process route acceptance. Deployment-operation
ownership is implemented and its
independent-worker PostgreSQL acceptance passes in CI for the exposed
deploy/stop/restart/rollback paths, including takeover, stale-result fencing,
tenant isolation, and recovery.

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

- [ ] Validate the dedicated OpenShift deployment provider against a selected
  cluster, including API compatibility, Routes, admission/security behavior,
  and lifecycle reconciliation.


---

# P2 — Production controls

## Distributed deployment-operation ownership — COMPLETE

Deployment intent, provider reconciliation, and Kubernetes lifecycle
acceptance exist. PostgreSQL now serializes the exposed mutating deployment
operations with the lease/fencing boundary proposed in ADR-011, keeps provider
observation separate from owned intent, and fails closed on stale workers
rather than replaying unknown side effects. Independent-worker PostgreSQL
acceptance passes in CI for provider-side-effect serialization, expiry/takeover,
late-result rejection, tenant isolation, and restart/reconciliation recovery.

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
- [x] Add PostgreSQL acceptance with two independent workers covering command
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

## Distributed A2A active-task state — PARTIALLY COMPLETE

The SDK task record can be persisted in PostgreSQL with
`OSA_A2A_TASK_DATABASE_URL`. OSA now adds an explicit versioned ownership table
with tenant/caller scope, leases, fencing, durable cancellation, and expired
lease takeover, plus schema-version-2 migration-owned append-only event-cursor
and bounded polling-relay foundations; run `osa-a2a-migrate` before startup.
Durable task mutations
now carry the acquired ownership snapshot through the SDK call context and
hold the ownership-row lock while saving, so stale workers fail closed. The
active executor registry remains process-local. Independent handlers can look
up shared active tasks, wait for a remote owner to publish cancellation, and
safely take over an expired owner lease; terminal snapshots are replayed through
read-only SDK events so a second handler does not write duplicate task history.
Process-boundary PostgreSQL acceptance covers task creation, lookup, remote
  cancellation, and crash/lease-expiry recovery. Independent PostgreSQL-worker
  acceptance also covers durable relay takeover fencing, late-event rejection,
  ordered terminal delivery, and cursor replay. The explicit stream-handler
  route adapter is covered, while normal-runtime public multi-process streaming
  enablement remains open; owner loss is fail-closed by default.

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
- [x] Add a migration-owned event cursor and bounded polling relay foundation
  with tenant-scoped reconnect cursors and terminal-event handling.
- [x] Add multi-process/multi-worker PostgreSQL acceptance for durable relay
  streaming, takeover fencing, late-event rejection, and cursor replay; the
  SDK active-task registry remains local to each process.
- [x] Implement the explicit, default-disabled `OsaA2aRequestHandler` relay
  adapter and acceptance coverage for the SDK stream-handler surface; runtime
  capability advertisement remains disabled until the ADR is accepted.
- [x] Integrate the durable relay with public `SubscribeToTask` /
  `message/stream` handler routes through the explicit, default-disabled
  `OsaA2aRequestHandler` acceptance hook and add route-level coverage.
- [ ] Approve ADR-011, then enable the durable stream capability in the normal
  runtime and complete production multi-process route acceptance.
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

Lockstep validation, Python distributions, signed/attested GHCR and Docker Hub
images, SBOMs, digest rollback automation, and public release `v0.1.2` exist.

- [ ] Decide whether Python packages need PyPI or another registry.
- [x] Perform the first automated public release (`v0.1.1`) after intentionally
  selecting the version and moving its changelog entries out of `Unreleased`.
- [x] Publish the lockstep `v0.1.2` release with GitHub artifacts, checksums,
  GHCR images, and Docker Hub images.

---

# Deferred until a concrete requirement

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
