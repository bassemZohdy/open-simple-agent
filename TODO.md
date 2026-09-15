# Open Simple Agent — Active Backlog

Updated 2026-09-15. This file contains only unfinished, deferred, or
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
  remain open; public releases through `v0.1.4` are complete. The
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

Start the Docker end-user onboarding plan at **DEMO-01** below, then complete
its runnable demo, walkthrough, and screenshots in dependency order. The
optional video follows the validated screenshot guide. This is ready work
and does not require resolving the production gates below.

In parallel with that product work, the remaining architecture decision is:

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

- [ ] Approve ADR-011, then enable the durable stream capability in the normal
  runtime and complete production multi-process route acceptance.

## Capability-level audit telemetry — COMPLETE

Management, runtime-boundary, and auth-denial audits exist. Model/native-tool/
MCP capability metrics and a bounded payload-free local JSONL sink are
implemented for a single process. An optional migration-owned PostgreSQL sink
provides replica-wide durable events with stable IDs, tenant/operation
metadata, deterministic ingestion ordering, deduplication, retention pruning,
and explicit tenant deletion.

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

## Docker end-user onboarding and demo assets — PLANNED

Goal: a new user can pull released images, open the Control Panel, configure
and run an agent, and understand the result using a visual guide. Screenshots
are required; a short narrated/captioned video is an optional follow-up.

Current gap: the README still starts with source/developer setup and separate
container examples. The Compose demo, walkthrough, capture script, screenshot
gallery contract, and manual smoke workflow are checked in; real screenshots,
video, and tagged-release validation remain open.

Implement in order: DEMO-01 → DEMO-02 → DEMO-03 → DEMO-04.
DEMO-05 is optional after DEMO-04. DEMO-06 publishes the completed guide/assets;
DEMO-07 maintains and verifies the delivered path. This work can proceed
independently of enterprise identity, ADR-011, and OpenShift acceptance gates.

- [ ] **DEMO-01 — Complete the released Docker application entry point.**
  Extend the existing image release workflow to publish the Control Panel
  image alongside the runtime and Control Plane, using the same version,
  registry, signing, and provenance conventions. Document exact published
  image names, supported CPU architectures, ports, browser URLs, and which
  services are needed for standalone runtime versus the full application.
  Validate frontend API configuration for a pulled image and browser access
  to deployed runtimes; users must not need a source build to configure URLs.
  **Acceptance:** all images required for the documented full-app demo can
  be pulled at one released version, and the browser reaches the real API
  and runtime. Verify actual image manifests before claiming ARM64 support.
  **Progress (2026-09-15):** PR #15 adds the Control Panel release image,
  corrected frontend Docker build context, startup API URL configuration,
  release-note entries, and image/port documentation. Keep this task open
  until a tagged release is built, pulled from both registries, and its
  published manifest and browser path are verified.

- [ ] **DEMO-02 — Add a reproducible local Docker demo.**
  Provide a downloadable versioned Compose/configuration bundle and concise
  environment example, with start, readiness, seed, stop, and reset commands.
  Reuse the current local deployment provider and explicit fake-provider
  opt-in for a no-model-key demonstration; label simulated responses clearly.
  Seed a useful sample agent and its referenced resources through supported
  APIs. Verify the Control Plane container's child-runtime ports, advertised
  invoke URLs, and CORS from the user's browser; do not assume Docker service
  names are browser-resolvable or that a Docker deployment provider exists.
  Bind the unauthenticated local demo to loopback, without a host Docker
  socket mount. Explain process-local state loss and make data deletion an
  explicit reset step. Offer a documented live-model configuration using
  secret references/environment, with no credentials committed.
  **Acceptance:** a clean machine with Docker/Compose and a browser can
  download the bundle, pull images, start it, open the UI, deploy the sample,
  invoke it, and stop/reset it without installing Python or Node.
  Durable/production deployment remains covered by the existing deployment
  guide and its provider/migration requirements.
  **Progress (2026-09-15):** The Compose bundle, deterministic seed script,
  fixed local child-runtime port, browser-reachable invoke URL, explicit fake
  provider forwarding, and static bundle contract tests are implemented and merged from PR #16. Docker execution remains the final acceptance check.

- [ ] **DEMO-03 — Write the end-user walkthrough and capture script.**
  Add a Docker-first getting-started guide covering prerequisites and verified
  resource/architecture requirements; pull/start and readiness; open the
  panel; choose/create an agent; inspect its model/tools; activate/deploy;
  send a prompt; inspect the response/session and available tool traces;
  inspect status/logs; stop/restart; and explain what survives a restart.
  Include expected results and recovery for occupied ports, failed readiness,
  missing model credentials, API/runtime URL or CORS errors, and 401/403.
  Explain switching from the deterministic demo to a live provider without
  implying the demo has generated real model output. Use the same sample
  names/prompts and numbered steps in the guide, screenshots, and video.
  **Acceptance:** a reader can follow the guide from a clean start to a
  successful invocation and shutdown; every shown capability is implemented
  and supported by the chosen demo topology.
  **Progress (2026-09-15):** The Docker-first walkthrough and capture script
  are implemented and merged from PR #17 in `docs/guides/docker-demo.md` and
  `docs/guides/docker-demo-capture.md`, with links from the demo and deployment
  guides. They use the same sample agent, prompt, ports, and deterministic
  output label; live Docker execution and media capture remain pending.

- [ ] **DEMO-04 — Capture real application screenshots and add a gallery.**
  Capture the running released Docker demo: agent overview, creation/template
  flow, model/tool resources, active deployment, invocation/result, and
  status/logs. Include an Arabic RTL example and a narrow-screen example
  using supported layouts. Store optimized images under
  `docs/assets/screenshots/` with descriptive names, captions, alt text, and
  the corresponding walkthrough step. Use synthetic data and exclude tokens,
  credentials, personal data, and unrelated browser content.
  **Acceptance:** images are legible in GitHub, match the documented release,
  and show real successful interactions; no mockups or simulated responses
  are presented as live AI results.
  **Progress (2026-09-15):** The screenshot gallery contract and required
  filename/evidence checklist are scaffolded in
  `docs/assets/screenshots/README.md`. Real screenshots still require a
  Docker-enabled browser session and a published image set.

- [ ] **DEMO-05 — Record a short demo video (optional).**
  Reuse the validated walkthrough for a 2–4 minute recording: what OSA does,
  pull/start containers, open the panel, configure/deploy an agent, invoke it,
  inspect output/status, and stop it. Include readable captions, a transcript,
  chapter timestamps, and an explicit deterministic/live-provider label.
  Keep recording instructions and script in the repository; attach a
  compressed MP4 to a GitHub Release and link it through a screenshot
  thumbnail, avoiding large raw recordings in git.
  **Acceptance:** a new user can reproduce the recorded steps using the same
  released bundle; playback and captions/transcript are checked. Video
  completion does not block the screenshot guide.

- [ ] **DEMO-06 — Publish an obvious end-user starting point.**
  Put the Docker quick start and a compact screenshot preview near the top
  of README.md, linking to the full walkthrough/gallery and video when
  available. Link the guide and assets from the relevant Docker Hub image
  descriptions and GitHub Release notes; keep developer setup separately
  discoverable. Publish the Compose/configuration bundle with the matching
  release and show its version beside the media.
  **Acceptance:** GitHub and Docker Hub users can reach the full-app startup
  instructions and visuals directly, and all commands, image tags, asset
  links, and download links resolve.

- [ ] **DEMO-07 — Verify and maintain the documented demo.**
  Add a focused browser/container smoke check for the exact demo path
  (readiness → create/activate/deploy → invoke → stop), using deterministic
  fixtures and the released or candidate images. Reuse existing CI tooling;
  run media recapture manually or for relevant UI/demo/release changes,
  without adding live-model calls or video rendering to every PR.
  Record release/commit, image digests, locale, viewport, seed command, and
  capture command beside the assets. Add a release checklist to refresh
  outdated screenshots and verify the optional video against changed flows.
  **Acceptance:** the walkthrough passes from a clean environment, a failed
  invocation cannot be mistaken for success, and maintainers can reproduce
  the screenshots without secret credentials.
  **Progress (2026-09-15):** The merged
  `.github/workflows/docker-demo.yml` manual workflow builds candidate images and
  exercises readiness, browser configuration, seed/create/activate/deploy,
  direct invocation, and stop, with logs and cleanup on failure. Run it on a
  Docker-enabled GitHub runner before declaring the demo acceptance complete.

## Packaging, CI/CD, and release — PARTIALLY COMPLETE

Lockstep validation, Python distributions, signed/attested GHCR and Docker Hub
images, SBOMs, digest rollback automation, and public release `v0.1.4` exist.

- [ ] Decide whether Python packages need PyPI or another registry.

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
