# Open Simple Agent — Active Backlog

Updated 2026-09-03. This file tracks active, pending, and deliberately deferred
work only. Completed implementation history belongs in `CHANGELOG.md`, the
architecture/API documentation, ADRs, and git history.

A task is complete only after implementation, automated tests, relevant
documentation, and appropriate failure/security behavior are complete.

## Current baseline

- The P0 runnable-agent release gate is complete: external bundles, fail-fast
  resource validation, LiteLLM/ADK execution, native function calling, isolated
  sessions, service lifecycle, runtime image, and deterministic container smoke
  acceptance are implemented.
- The managed platform foundation is implemented: PostgreSQL Control Plane
  persistence, resource/template APIs, MCP runtime integration, persistent
  policy-scoped memory, local deployment lifecycle, A2A/external agents,
  authentication/authorization, tenant ownership, audit, observability, and
  streaming/replica behavior.
- The Manager Agent management-tool surface and its approval/security guards are
  implemented.
- CI enforces Ruff formatting/lint, strict mypy, the full PostgreSQL/A2A test
  suite with an 84% coverage floor, dependency/license scanning, runtime +
  Control Plane image smoke/SBOM checks, and Control Panel typecheck/test/build.
- A first `kubectl`-backed Kubernetes deployment-provider slice exists, but all
  further Kubernetes/Kind work is intentionally paused as described below.
- The React/TypeScript Control Panel foundation exists with an API-authenticated
  shell, Agents list/filtering, template and resource-catalog views, readiness
  view, agent detail/version history/lifecycle actions, deployment
  lifecycle/status/log views, audit/metrics views, validated agent
  create/edit/clone flows, an A2A invocation test console, and frontend CI
  coverage.

---

# P1 — Managed platform follow-up

## P1.5 Kubernetes deployment provider — PENDING / PAUSED

The first provider slice is already implemented and retained in the codebase:
Deployment + Service + bundle ConfigMap generation, Kubernetes Secret-backed
environment references, readiness/liveness probes, hardened pod security,
scale/restart/rollback/status/log operations, and OSA identity labels.

Do **not** resume the remaining Kubernetes work until it is explicitly
reprioritized.

- [ ] Wire packaged Control Plane provider selection/configuration to the
  Kubernetes provider.
- [ ] Validate deploy/readiness/scale/restart/rollback/recovery against a real
  Kind cluster in CI or a dedicated acceptance workflow.
- [ ] Add robust status-watch/recovery behavior for Control Plane restart and
  already-running Kubernetes workloads.
- [ ] Document production RBAC, namespace, image-pull-secret, network-policy,
  resource-limit, and upgrade requirements.
- [ ] Keep OpenShift-specific behavior deferred separately; do not introduce
  OpenShift assumptions into the generic Kubernetes provider.

---

# P2 — Production controls follow-up

## P2.2 Enterprise identity lifecycle

The enterprise identity lifecycle semantics are defined in ADR-007
(`docs/adrs/007-enterprise-identity-lifecycle.md`): authorization remains
**claim-driven** with the IdP as the lifecycle authority — no external policy
engine and no OSA-side identity store. The shared validation path rejects
tokens carrying an `active` claim that is not `true`; opaque tokens gain live
disablement through RFC 7662 introspection.

- [x] Define enterprise identity lifecycle semantics: provisioning/
  deprovisioning expectations, disabled identities, role/group
  synchronization, service-account lifecycle, permission revocation bound,
  and stale-token behavior (ADR-007).
- [x] Decide whether enterprise authorization remains claim-driven or requires
  an external policy/identity integration (claim-driven; the external policy
  engine stays a deferred item with an explicit revisit trigger).
- [x] Reject tokens whose `active` claim is present but not `true` in the
  shared JWT/introspection validation path.
- [ ] Add integration/contract tests once a concrete enterprise identity
  source is selected, covering introspection liveness, key rotation, and
  role-change propagation.

---

# P3 — Product surface and distribution

## P3.1 Control Panel

- [x] Create the TypeScript/React application and authenticated API shell. The
  current shell accepts an optional short-lived Bearer token stored only in
  browser `sessionStorage`; deployment-specific OIDC login/refresh orchestration
  remains an integration concern until issuer/client/redirect semantics are
  defined.
- [ ] Add agents, versions, templates, resources, deployments, health, and audit
  views.
  - [x] Read-only Agents list/search/status filtering.
  - [x] Control Plane readiness view.
  - [x] Agent detail/version history and lifecycle actions.
  - [x] Templates and resource catalogs, including tenant-scoped Model, Tool,
    Skill, MCP, and MemoryPolicy browsing/search and safe redacted-definition
    inspection.
  - [x] Deployment lifecycle/status/logs, including per-agent history,
    intent-only deploy, stop/restart/rollback actions, observed-status
    refresh, and bounded captured-log inspection with tail selection.
  - [x] Audit events and operational metrics, including client-side action
    filtering, bounded event limits, parsed Prometheus sample tables, and the
    raw exposition view.
- [x] Add validated agent create/edit/clone flows. Create supports empty
  drafts, built-in templates, or pasted JSON definitions validated on the
  client (name/JSON/metadata.name) and server (422 surfaced inline); editing
  flows through immutable version creation on the detail page; clone
  deep-links into a pre-filled create panel (definitions are write-only, so
  the copy re-selects a template or definition).
- [x] Add an A2A test console: external agents listed with card/version and
  health status, message + timeout invocation through
  `POST /external-agents/{id}/invoke`, and inline response/error rendering.
- [ ] Add managed-agent invocation console capabilities (sessions, streaming,
  tool traces) once the runtime-access design is decided: expose runtime URLs
  in the deployment API, add Control Plane proxy routes, or another mechanism.
- [ ] Complete accessibility, localization, and responsive behavior coverage.
  Responsive layout plus loading/empty/error states are implemented in the
  foundation slice, and keyboard accessibility now includes a skip-to-content
  link plus route-change focus management (tested). Broader
  accessibility/localization acceptance remains open.

## P3.3 Packaging, CI/CD, and release

Completed release-supply-chain automation:

- [x] Validate that release tags match the lockstep version in all four
  manifests and require a dated matching changelog section.
- [x] Build the three Python wheel/sdist distributions as one lockstep release
  and validate them before publication.
- [x] Publish versioned runtime and Control Plane images to GHCR, with `latest`
  as the mutable convenience channel.
- [x] Add OCI SBOM/provenance metadata and GitHub build-provenance attestations
  for release artifacts/images.
- [x] Keyless-sign published container image digests with Cosign using GitHub
  OIDC.
- [x] Create GitHub Releases with Python distributions and SHA-256 checksums;
  support exact `vX.Y.Z` tag releases and validated manual release dispatch
  from `main`.
- [x] Modernize CI actions and make missing SBOM outputs fail the build; upload
  both runtime and Control Plane image SBOMs after both are generated.

Remaining release work:

- [ ] Add a live-provider acceptance job using an opt-in secret and the P0.2
  real-model acceptance path.
- [ ] Decide whether Python distributions also need publication to PyPI or
  another package registry; GitHub Release assets are the implemented
  distribution path today.
- [ ] Automate rollback of mutable release/deployment channel pointers to a
  previously published immutable version/digest. Never rebuild or overwrite an
  existing version tag.
- [ ] Perform the first automated public release only after intentionally
  selecting/bumping the release version and moving the desired changelog
  entries out of `Unreleased`.

---

# Deferred until a concrete requirement

- [ ] Additional runtime frameworks such as LangChain/LangGraph.
- [ ] Multiple unrelated agents in one runtime process.
- [ ] Dynamic runtime plugin installation.
- [ ] Advanced semantic agent discovery and hosted marketplace.
- [ ] Advanced multi-tenancy and multi-region deployment.
- [ ] Agent delegation/consent framework beyond baseline A2A security.
- [ ] General human-approval framework beyond management operations.
- [ ] Advanced memory extraction/consolidation and vector retrieval.
- [ ] Enterprise external policy engine (until P2.2 selects a concrete need).

---

# Priority order while Kubernetes is paused

1. Decide the managed-agent runtime-access design (deployment-API runtime URL
   exposure vs Control Plane proxy routes) and build the invocation console's
   sessions/streaming/tool-trace capabilities on it.
2. Implement the opt-in live-provider acceptance path when a suitable CI secret
   is available.
3. Complete the remaining P3.3 registry/rollback/first-release decisions when a
   release is intentionally scheduled.
4. P2.2 lifecycle semantics are defined (ADR-007); add integration/contract
   tests when a concrete enterprise identity source is selected.
5. Keep all Kubernetes/Kind/OpenShift follow-up pending until explicitly
   resumed.
