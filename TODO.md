# Open Simple Agent — Active Backlog

Updated 2026-09-06. This file contains only unfinished, deferred, or
deliberately gated work. Completed implementation history is recorded in
`CHANGELOG.md` and git history.

A task is complete only when the implementation, automated tests, relevant
documentation, and appropriate failure/security behavior are complete.

## Current status

The P0 runnable-agent gate, managed-platform foundation, Manager Agent
management surface, current Control Panel, runtime/Control Plane images, and
release-supply-chain automation are implemented and covered by CI.

The current production-readiness limits are:

- runtime sessions are in-memory unless a durable provider is selected and
  implemented;
- memory PostgreSQL still uses transitional bootstrap DDL;
- Control Plane resource records are durable, but per-process resource catalogs
  are startup caches;
- external-agent records and local deployment-provider process state are
  process-local;
- deployment export, retry, rollback, and restart reconciliation need stronger
  safety guarantees;
- outbound A2A destinations do not yet have an application-level SSRF policy;
- A2A task state, capability-level telemetry, and HTTP capacity controls are
  not replica-safe or fully defined;
- translated-locale coverage, deployment-specific OIDC browser login, package
  registry publication, and the first public release remain open; Kubernetes
  follow-up is intentionally paused.

## Recommended next task

Start with **BF14: secure and atomically stage deployment bundle exports**.
It is a focused security/data-integrity fix on the active deployment path and
is a prerequisite for trusting the local provider in multi-tenant or shared
deployment roots. Follow it with BF18 (outbound A2A SSRF policy), then the
related deployment reliability work BF15–BF17 and BF19.

---

# P1 — Runtime durability

## Persistent runtime sessions — PENDING

`SessionProvider` currently defaults to the in-memory `SessionManager`. It
enforces ownership, TTL, and bounded history, but state is lost with a runtime
process and cannot provide cross-replica continuity.

- [ ] Select the persistent-provider contract and configuration semantics for
  `spec.session.persistence`, including database lifecycle and migration
  ownership.
- [ ] Implement and wire a durable provider without weakening ownership checks,
  TTL expiry, bounded history, or metadata redaction.
- [ ] Add restart, expiry, ownership, concurrent-update, and cross-process
  tests; document backup, upgrade, and failure-recovery behavior.

## Memory schema ownership — PENDING

`PostgresMemoryProvider` still creates `osa_memory_entries` with bootstrap DDL
when `OSA_MEMORY_DATABASE_URL` is configured. PostgreSQL persistence works, but
schema ownership and upgrade ordering are not yet migration-controlled.

- [ ] Select migration ownership and operational commands for the independent
  memory database.
- [ ] Replace runtime `CREATE TABLE IF NOT EXISTS` bootstrap behavior with an
  explicit, versioned migration path.
- [ ] Add upgrade/rollback coverage and document backup, migration, and startup
  ordering requirements.

---

# P1 — Managed platform

## P1.5 Kubernetes deployment provider — PAUSED

The first `kubectl`-backed provider slice exists: Deployment and Service
generation, bundle ConfigMaps, Secret references, probes, hardened pod
security, scale/restart/rollback/status/log operations, and OSA identity
labels. Do not resume this work until it is explicitly reprioritized.

- [ ] Wire packaged Control Plane provider selection and configuration to the
  Kubernetes provider.
- [ ] Validate deploy/readiness/scale/restart/rollback/recovery against a real
  Kind cluster in CI or a dedicated acceptance workflow.
- [ ] Add status-watch and recovery behavior for Control Plane restart and
  already-running Kubernetes workloads.
- [ ] Document production RBAC, namespaces, image-pull secrets, network
  policies, resource limits, and upgrade requirements.
- [ ] Keep OpenShift-specific behavior separate; do not introduce OpenShift
  assumptions into the generic Kubernetes provider.

## Packaged Control Plane deployment launcher — PENDING

The Control Plane image is management-only, while the default local deployment
command invokes `osa-runtime`. That executable is not packaged in
`Dockerfile.control-plane`, so an isolated Control Plane container cannot
launch a runtime without an operator-provided launcher or colocated runtime.

- [ ] Decide the supported production contract: package the runtime launcher,
  require an operator-provided launcher/provider, or make provider selection
  explicit instead of relying on the local default.
- [ ] Add image-level integration coverage proving that the selected topology
  can launch, probe, stop, and observe a runtime.
- [ ] Document split-image and colocated-process topologies, including the
  security boundary around `OSA_DEPLOY_COMMAND_TEMPLATE`.

---

# P2 — Production controls

## P2.2 Enterprise identity lifecycle — PARTIALLY COMPLETE

ADR-007 defines claim-driven lifecycle semantics with the IdP as lifecycle
authority. The shared validation path handles the `active` claim and opaque
token introspection. Integration coverage remains blocked on selecting a
concrete enterprise identity source.

- [ ] Add integration/contract tests for introspection liveness, key rotation,
  disabled identities, and role-change propagation once an identity source is
  selected.

## P2.4 Distributed A2A task state — PENDING

The runtime A2A executor and SDK task store are process-local. A runtime
replica cannot observe task state created by another replica.

- [ ] Select a durable task-store backend plus retention, ownership, and
  recovery semantics compatible with the A2A SDK.
- [ ] Implement and wire the store so task updates remain consistent across
  runtime replicas and restarts.
- [ ] Add multi-replica acceptance coverage for task creation, completion,
  failure, lookup, and recovery without leaking tenant or caller state.
- [ ] Define cancellation semantics for in-flight tasks, including safe
  cancellation of the OSA run, terminal-state ordering, and protection against
  late events or retries resurrecting a canceled task.

## Capability-level audit telemetry — PENDING

The current audit path records management mutations, runtime/A2A boundaries,
and authentication/authorization denials, but not redaction-safe per-capability
events for model, native-tool, or MCP activity.

- [ ] Define event taxonomy, redaction rules, retention, sampling, and
  performance policy.
- [ ] Implement an optional sink and durable persistence path without storing
  prompts, outputs, credentials, or unbounded tool payloads.
- [ ] Add model/tool/MCP success, failure, timeout, and tenant-isolation tests
  plus operational documentation.

## Rate limiting and quotas — PENDING

Neither HTTP application enforces per-principal or per-tenant rate limits,
concurrency quotas, or a `429`/`Retry-After` contract. Until this is implemented,
internet-facing deployments need an API gateway or service mesh for capacity
controls.

- [ ] Define route, principal, and tenant limits, burst behavior, retry
  semantics, and stable `429` response headers.
- [ ] Select replica-safe enforcement and storage for streaming and long-running
  A2A/deployment operations.
- [ ] Add isolation, burst, streaming, A2A, metrics, and documentation tests.

---

# P3 — Product surface and distribution

## P3.1 Control Panel — PARTIALLY COMPLETE

The current English Control Panel includes the authenticated shell, agent and
resource views, authoring, lifecycle/deployment views, audit/metrics, A2A and
managed-runtime invocation consoles, safe snapshot inspection, responsive
behavior, and loading/empty/error recovery states.

- [ ] Add translated-locale coverage while preserving accessible names,
  validation meaning, and browser-locale timestamp behavior.
- [ ] Define deployment-specific OIDC browser login/refresh semantics after an
  issuer, client, and redirect contract is selected.
- [ ] Decide whether public agent-definition bundle import/export APIs are in
  scope; resource import/export and server-side deployment export exist today.

## P3.3 Packaging, CI/CD, and release — PARTIALLY COMPLETE

CI and release workflows validate lockstep versions, build Python distributions,
publish signed/attested GHCR images, generate SBOMs, and support immutable
digest rollback of the mutable `latest` channel. Live-provider acceptance is
available as an opt-in job and remains offline-safe without its secret.

- [ ] Decide whether Python distributions need PyPI or another package
  registry; GitHub Release assets are the current distribution path.
- [ ] Perform the first automated public release only after intentionally
  selecting a release version and moving the relevant changelog entries out of
  `Unreleased`.

---

# Deferred until a concrete requirement

- [ ] Track upstream MCP SDK major changes and revisit the pin when MCP 2.x
  lands.
- [ ] MCP resources/prompts exposure and legacy SSE transport support.
- [ ] Configurable custom model-adapter registration until a second production
  adapter is required.
- [ ] Complete the LangGraph-first technology selection with a bounded OSA
  contract/security/durability POC (the programmatic runtime slice exists;
  MCP/A2A, shared HTTP service, and durable checkpoint validation remain); see
  the [weighted DAR-001 comparison](docs/DAR-001-adk-vs-langchain-langgraph.md).
- [ ] Multiple unrelated agents in one runtime process.
- [ ] Dynamic runtime plugin installation.
- [ ] Advanced semantic agent discovery and hosted marketplace.
- [ ] Advanced multi-tenancy and multi-region deployment.
- [ ] Agent delegation/consent beyond baseline A2A security.
- [ ] General human approval beyond management operations.
- [ ] Advanced memory extraction/consolidation and vector retrieval, including
  pgvector.
- [ ] Enterprise external policy engine until P2.2 selects a concrete need.

---

# Active review findings

These findings are unresolved and have concrete failure scenarios. They are
ordered by the recommended implementation sequence. Resolved findings
`F1–F15`, `I1–I7`, `BF1–BF12`, and `BI1–BI5` are recorded in `CHANGELOG.md` and
are intentionally not duplicated here.

## BF14 — Safe deployment bundle export

`DeploymentService._export_bundle` builds paths from agent, version, and
resource names below the shared `OSA_DEPLOY_ROOT`. A path separator can escape
the root, while equal names in different tenants can collide during startup.

- [ ] Use deployment-scoped opaque directories and safe filenames.
- [ ] Enforce resolved-path containment, stage exports atomically, and clean up
  partial exports.
- [ ] Add traversal, cross-tenant, collision, and concurrency tests.

## BF18 — Outbound A2A SSRF policy

External-agent registration, refresh, invocation, and credential token requests
accept arbitrary destinations without application-level SSRF, redirect, or
DNS-rebinding controls.

- [ ] Define allowed schemes/hosts, private-address policy, DNS-rebinding and
  redirect handling, and equivalent rules for credential token URLs.
- [ ] Add negative tests and document the operator escape hatch for private A2A
  endpoints.

## BF15 — Deployment retry idempotency

`DeploymentService.deploy` allocates a fresh port for each request, so a retry
after a lost response changes the command and can start a second runtime.

- [ ] Define an idempotent desired-deployment identity and serialize concurrent
  requests.
- [ ] Test retries and duplicate requests for each provider.

## BF16 — Rollback consistency

Rollback does not consistently stop the existing provider deployment, forward
the runtime environment, or persist a provider's replacement deployment ID and
invoke URL.

- [ ] Make rollback an atomic stop/relaunch/persist operation with failure
  recovery.
- [ ] Add regression tests for process count, URL/port, environment, and status.

## BF17 — Local-provider reconciliation

The local provider is process-local, its `shutdown()` is not wired into the
Control Plane lifespan, and a PostgreSQL-backed restart cannot rehydrate its
child processes.

- [ ] Define shutdown, orphan cleanup, restart/reconciliation, and multi-replica
  semantics, or make an external provider mandatory for production.
- [ ] Cover graceful shutdown, restart recovery, and persisted running records.

## BF19 — Resource-catalog cache coherence

PostgreSQL resource definitions are materialized into each process's
`ResourceCatalogs` only at startup. Cross-replica create, replace, or delete can
therefore leave reads, duplicate checks, activation validation, and bundle
export stale or missing.

- [ ] Add read-through or invalidation/notification semantics while preserving
  tenant isolation and write consistency.
- [ ] Cover cross-replica create/update/delete, activation, and deployment.

## BF13 — Durable external-agent records

External A2A records are always process-local: a restart loses registrations and
replicas can have different endpoints and health state.

- [ ] Add a tenant-scoped durable repository and migration, persisting only
  credential references.
- [ ] Cover restart, replica, health refresh, deletion, and tenant isolation.

## BI8 — Documentation link validation

The current contract tests validate routes and YAML examples but do not catch a
renamed guide, ADR, or changelog link.

- [ ] Add Markdown link and anchor validation to CI.

## BI9 — Honest not-found route

The frontend wildcard route still uses `PlaceholderPage` copy that says
`Planned in P3.1`, which mislabels a typo or stale deep link as a planned
product route.

- [ ] Replace it with an accessible not-found/recovery page and add a route
  smoke test.

## Folded findings

- **BI6** is tracked by the packaged Control Plane deployment launcher task.
- **BI7** is tracked by the rate limiting and quotas task.

## Review history

The 2026-09-04 and 2026-09-05 review resolutions, evidence, and validation
results remain in `CHANGELOG.md` and git history. New reviews should add only
unresolved work here and record completed items in the changelog.
