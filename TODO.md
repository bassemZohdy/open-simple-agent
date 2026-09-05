# Open Simple Agent — Active Backlog

Updated 2026-09-04. This file tracks active, pending, and deliberately deferred
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
  create/edit/clone flows, an A2A invocation test console, managed-agent
  runtime invocation with direct CORS-enabled browser-to-runtime calls, and
  frontend CI coverage.

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
- [x] Decide the managed-agent runtime-access design (ADR-008): deployment
  records expose an optional operator-configured public runtime URL
  (`OSA_DEPLOY_INVOKE_URL_TEMPLATE`, synthesized server-side, migration 0007);
  the Control Plane still carries no invocation traffic.
- [x] Add managed-agent invocation from the Control Panel (sessions,
  streaming, tool traces) using the recorded runtime endpoint — the runtime
  now supports opt-in browser CORS via `OSA_RUNTIME_ALLOWED_ORIGINS`, the
  deployment service forwards configured origins to launched runtimes, and
  the Control Panel exposes a direct test-message form on deployments that
  publish an invoke URL (ADR-008).
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
- [x] Automate rollback of mutable release/deployment channel pointers to a
  previously published immutable digest: the `Rollback image channel` workflow
  re-tags `latest` via `docker buildx imagetools create` after policy
  validation (`scripts/rollback_release.py`, unit-tested). Immutable version
  tags are never rebuilt or overwritten; signatures and attestations survive
  because they are digest-bound.
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

1. ~~Enable managed-agent invocation from the Panel on top of ADR-008's
   recorded runtime endpoints (runtime CORS or a proxy route), bringing
   sessions, streaming, and tool traces to the console.~~ **Done.**
2. Implement the opt-in live-provider acceptance path when a suitable CI secret
   is available.
3. Complete the remaining P3.3 registry/rollback/first-release decisions when a
   release is intentionally scheduled.
4. P2.2 lifecycle semantics are defined (ADR-007); add integration/contract
   tests when a concrete enterprise identity source is selected.
5. Keep all Kubernetes/Kind/OpenShift follow-up pending until explicitly
   resumed.

---

# Review Findings

Findings are filed with a concrete failure scenario. Resolve them in order and
record resolutions in the Review Log.

## Control Panel UI (control-plane/frontend) — 2026-09-04

### Fixes

- [x] F1 Dark mode leaves light-theme grays on dark surfaces: `.state-card`,
  `.filter-bar label`, `td small`, `.muted-text`, card/body `p`, `.eyebrow`,
  and `th` keep `#475467`/`#667085` (~2.2–3.4:1, below WCAG AA 4.5:1);
  `.count-badge` and the anonymous `.connection-pill` render near-white text
  on a `#eef2f6` pill; `.confirmation` renders dark red on `#291b1c`.
  Failure: with OS dark mode, loading/empty states, count badges, form
  labels, and the archive confirmation are unreadable.
- [x] F2 No scroll reset on route change: `AppShell` focuses `#main-content`
  with `preventScroll: true` and `BrowserRouter` does no scroll management.
  Failure: opening an agent from the bottom of a long list renders the
  detail page scrolled to the old offset with the top content off-screen.
- [x] F3 AgentsPage and the DeploymentsPage agent picker hardcode
  `limit: 100` and never send `offset`, though `/agents` supports
  limit/offset. Failure: with >100 agents the rest are unreachable in the UI
  and the total badge disagrees with the rows shown.
- [x] F4 Create-agent "Built-in template" source with no template selected
  silently omits `template` and creates an empty draft. Failure: the user
  picks the template source, misses the dropdown, and gets a blank draft
  instead of a template-based agent.
- [x] F5 `closeCreate()` never clears `?create=1&cloneOf=…` from the URL.
  Failure: closing the clone panel and clicking "Create agent" in the same
  visit re-enters clone mode with stale pre-filled metadata.
- [x] F6 Disabled buttons keep `cursor: pointer` and full styling (no
  `:disabled` rules). Failure: during busy states (e.g. "Creating…") other
  buttons look clickable and clicks silently no-op.
- [x] F7 Console timeout input is not clamped: clearing it sends
  `timeout_seconds=0` (`Number("")`) and out-of-range values pass through
  despite `min`/`max`. Failure: invoke with a cleared timeout fails with a
  confusing server-side error.
- [x] F8 ResourcesPage and TemplatesPage error states have no retry path
  (every other page has one). Failure: a transient API error requires a full
  page reload or kind switch to recover.
- [x] F9 Stale copy on `/console`: the footer still claims managed-agent
  invocation "require[s] runtime access design (pending)" although ADR-008
  managed invocation shipped on the Deployments page. Failure: operators
  conclude the feature does not exist.
- [x] F10 No React error boundary. Failure: any render exception (e.g. an
  unexpected API payload shape such as `labels: null`) unmounts the whole
  app to a blank page with no recovery.
- [x] F11 No 401/403 handling: an expired token surfaces only as per-page
  errors and Retry loops with the dead token. Failure: mid-session token
  expiry leaves every view failing with no prompt to reconnect.
- [x] F12 `AuthContext` guards the initial `sessionStorage` read but not the
  `setItem`/`removeItem` writes. Failure: where storage is unavailable
  (private mode, storage disabled), submitting a token throws and the form
  dies silently.
- [x] F13 No fetch carries a timeout or `AbortController`;
  `invokeRuntimeEndpoint` waits forever. Failure: a hung runtime leaves
  "Invoking…" spinning indefinitely; only a page reload escapes.
- [x] F14 Rollback has no confirmation (archive has one) and the rollback
  version input's form swallows Enter (`onSubmit` is only `preventDefault`).
  Failure: one stray click immediately relaunches an older version, and
  pressing Enter in the version field silently does nothing.
- [x] F15 `.detail-grid { minmax(300px, 1fr) }` exceeds the ~282px content
  width of a 320px viewport. Failure: horizontal page scroll on small
  phones; use `minmax(min(300px, 100%), 1fr)`.

### Improvements

- [ ] I1 Poll deployment status (or provide one "refresh all") so `starting`
  deployments converge without manual "Refresh status" clicks.
- [ ] I2 Render localized/relative timestamps in version history and the
  audit table instead of raw ISO strings.
- [ ] I3 Move the audit action filter server-side (the API only supports
  `limit` today) or relabel the count badge so "N matching" does not imply a
  global match count over the unloaded window.
- [ ] I4 Health page: render the full readiness payload and add a refresh
  control.
- [ ] I5 Production serving story for the Panel: SPA fallback so deep links
  like `/agents/<id>` do not 404 on plain static hosts, plus the UI
  container image already marked as future work in PROJECT_DEFINITION.
- [ ] I6 Scope the Deployments busy state per row/action instead of locking
  every button on the page during any single request.
- [ ] I7 Surface immutable version snapshot content when the API exposes it
  (definitions are write-only today, so clone/edit cannot show the source
  definition).

## Backend, Runtime & Release Tooling (control-plane/backend, generic-agent,
runtimes/adk, scripts, docs) — 2026-09-04

Every item below was independently confirmed by reading the current source
(not just reported by a review pass); two are empirically reproduced.

### Fixes

- [x] BF1 Cross-user memory leakage via a shared, never-reset `LlmAgent`
  instruction: `GenericAdkAgent._invoke`/`stream_invoke`
  (`runtimes/adk/src/osa/runtimes/adk/runtime.py:487-488`, `:559-560`) run on
  one process-wide `llm_agent` instance and only *set*
  `self.llm_agent.instruction` when the current request's memory lookup
  returns content — there is no `else` branch restoring the base
  instruction when it doesn't. Failure: User A's request pulls their private
  memory into the shared instruction; User B's very next request (new topic,
  no memory hits, so the `if memory_context:` branch is skipped) is sent to
  the model with A's memory content still attached, and the model can
  reflect it back to B. Directly contradicts the "isolated sessions" P0
  guarantee. Concurrent in-flight requests make the window worse (A's own
  call can race and pick up B's just-written instruction).
- [x] BF2 External A2A agent records have no tenant isolation:
  `ExternalAgentRecord`
  (`control-plane/backend/src/osa/control_plane/backend/external_agents.py`)
  has no `tenant_id` field, `ExternalAgentCatalog` is one process-global
  dict, and none of `list_external_agents`/`get_external_agent`/
  `refresh_external_agent`/`delete_external_agent`/`invoke_external_agent`
  filter or check ownership by tenant — unlike every other resource type in
  this package (`_owned_record`/`_request_tenant` for agents,
  `scoped_catalogs` for resources). Failure: Tenant A registers an external
  agent with an `OutboundCredential`; Tenant B (valid token, different
  `tenant_id`, same `external-agent:*` permission) lists it, invokes it —
  using A's stored credential against the remote service — or deletes it.
- [x] BF3 PostgreSQL-backed deployment records are never wired up despite a
  configured DSN: in `create_control_plane_app`
  (`control-plane/backend/src/osa/control_plane/backend/service.py:88-133`),
  `deployment_records: Any = None` is declared before the `if dsn:` branch
  and never assigned inside it — only `agents`/`resources`/
  `audit_repository` get Postgres implementations. The `if deployment_records
  is not None:` gate (line 124) that would build a `DeploymentService` around
  it is therefore permanently dead, and `PostgresDeploymentRecordRepository`
  (`repositories.py:698`) is never imported from `service.py`. Failure: an
  operator sets `OSA_CONTROL_PLANE_DATABASE_URL` and runs two Control Plane
  replicas per ADR-004's stated goal; a deploy routed to replica A is
  invisible to `GET /deployments/{id}` on replica B, and all deployment
  history is lost on restart.
- [x] BF4 `PATCH /agents/{id}` with a new `definition` leaves the Postgres
  `skills` column stale: `update_agent`
  (`control-plane/backend/src/osa/control_plane/backend/api.py:595-611`)
  never puts `skills` into the `updates` dict passed to
  `agent_repository.update()`, then calls `_sync_derived_fields(updated)`
  (`api.py:233-236`) which mutates the returned Python object's `.skills`
  in place. `PostgresAgentRepository.update()`
  (`repositories.py:363-391`) returns a freshly re-queried record, so that
  mutation is never written back — `_UPDATABLE_COLUMNS` even lists `skills`
  as a valid column, it's just never populated by the caller. Failure (
  Postgres backend only): the immediate PATCH response shows the correct new
  skills (illusion of success), but the stored row — and every later
  `GET /agents?skill=…` filter — keeps the pre-update skill list
  indefinitely.
- [x] BF5 `GET /agents/{agent_id}/deployments` is authorized against
  `agent:read` instead of `deployment:read`:
  `permission_for_request`
  (`generic-agent/src/osa/generic_agent/auth.py:230-257`) routes on
  `path.startswith("/deployments/") or path.endswith("/deploy")` before
  falling through to the generic `/agents/` clause; `/agents/{id}/deployments`
  matches neither, so it resolves to `AGENT_READ` like any other
  `/agents/...` route. Every sibling deployment-observing endpoint (
  `GET /deployments/{id}`, `.../logs`) correctly requires `DEPLOYMENT_READ`.
  Failure: an enterprise role mapped to `agent:read` but not
  `deployment:read` can still read deployment ids, status, and `invoke_url`
  through this one route, bypassing the permission boundary enforced
  everywhere else.
- [x] BF6 MCP tool-call retries can duplicate non-idempotent tool
  executions: `McpToolConnection.call_tool`
  (`runtimes/adk/src/osa/runtimes/adk/mcp_client.py:301-334`) retries on a
  bare `except Exception`, with no distinction between a transient
  transport failure and a response that was simply lost after the server
  already completed the call, and no idempotency key. Failure: a tool that
  sends an email or creates a record times out waiting for its response,
  OSA retries and the server runs it a second time.
- [x] BF7 The documented local quick-start (`README.md`, `CONTRIBUTING.md`,
  `AGENTS.md`: `uv sync --all-packages` then `uv run pytest`) omits the
  `--extra a2a` CI always adds, and `tests/integration/test_a2a.py` has no
  `pytest.mark.skipif`/`importorskip` guard the way
  `tests/integration/test_postgres_memory.py` does for the `postgres` extra.
  Empirically reproduced: a venv synced exactly per the README's documented
  commands fails 11 tests in `test_a2a.py` with
  `A2aNotInstalledError` instead of skipping. Failure: a new contributor
  follows the README verbatim and sees unexplained test failures that look
  like a broken checkout.

### Improvements

- [x] BI1 `docs/guides/security.md` — the doc that states "current security
  behavior... is tested" — never mentions `OSA_RUNTIME_ALLOWED_ORIGINS` or
  CORS, even though ADR-008 explicitly puts "the runtime's auth/CORS
  posture" on the operator and the CHANGELOG notes the Control Panel's
  runtime-invoke request never forwards the Panel's own bearer token.
  Failure: an operator enables `OSA_RUNTIME_ALLOWED_ORIGINS` for the
  Deployments-page "Send test message" feature, checks the security guide
  for guidance, finds nothing, and doesn't realize the runtime's own
  `OSA_AUTH_MODE` now gates a newly browser-reachable endpoint.
- [x] BI2 `scripts/release_validation.py`'s changelog check
  (`release_heading = re.compile(rf"^## \[{{version}}\] - \d{{4}}-\d{{2}}-\d{{2}}$")`)
  matches *any* heading anywhere in the file, regardless of position or
  date, and `CHANGELOG.md` already carries pre-release dev-milestone
  headings from `[0.0.1]` through `[0.14.0]` above the never-yet-tagged
  `[Unreleased]` section (`git tag -l` is currently empty). Empirically
  confirmed in `.sandbox/`: bumping all four manifests to a version that
  collides with one of those existing headings (e.g. `0.2.0`) makes
  `validate_release()` pass even though `## [Unreleased]` still has
  unmigrated content and the matched heading's date/content predate the
  actual release. Failure: whoever cuts the first real release picks a
  version number that happens to match one of these old headings and ships
  a GitHub Release whose linked changelog section is stale, unrelated
  content.

- [x] BF8 `TestStdioProtocol::test_timeout_is_deterministic` is
  load-sensitive. Under machine load, the mcp library's read-timeout race can
  surface a raw `CancelledError` out of `McpToolConnection.call_tool`
  (`runtimes/adk/src/osa/runtimes/adk/mcp_client.py`) instead of the expected
  `McpToolExecutionError` — on Python ≥3.8 `CancelledError` derives from
  `BaseException`, so the retry loop's `except Exception` neither converts
  nor retries it. Reproduced twice during the 2026-09-04 merge-verification
  run (full suite failed once under post-sync load; quiet re-run green: 525
  passed / 21 skipped). Failure: a CI run under load goes red on an unrelated
  change and masks real failures. Fix direction: bound
  `session.call_tool` with our own `asyncio.wait_for(..., timeout_seconds)`
  inside `call_tool` so timeout → `TimeoutError` conversion is deterministic
  at the OSA boundary; implement together with BF6, which reworks the same
  retry/timeout block.

## Review Log

- 2026-09-05 — BF1 resolved: `GenericAdkAgent._invocation_runner()` builds a
  fresh `LlmAgent` + `Runner` per invocation with the effective instruction
  (base + this caller's policy-loaded memory context) baked in; `invoke`,
  `stream_invoke`, and `_run_adk` consume the per-invocation runner instead of
  mutating the shared `llm_agent.instruction`, so one caller's memory can no
  longer leak into the next caller's request (regression test asserts user B's
  prompt never contains user A's memory and the shared agent object stays at
  its base instruction). `build_llm_agent` gained an `instruction` override.
  Sessions remain continuous: every per-invocation runner shares the app name
  and the OSA-backed session service. Full suite: 534 passed / 22 skipped at
  86.32% coverage.
- 2026-09-05 — Control Panel fixes F1–F15: dark-mode now restyles every
  muted surface and pill (F1); route changes reset scroll (F2); agent lists
  page past the first 100 records on the Agents list and the Deployments
  picker (F3); template-source creation requires a selected template (F4);
  closing the create panel clears `?create=1`/`?cloneOf` (F5); disabled
  buttons render and read as disabled (F6); the console timeout input is
  clamped to 1–300s with the effective value shown (F7); Resources and
  Templates error states gained Retry paths (F8); the `/console` footer no
  longer claims managed invocation is pending (F9); an error boundary keeps
  render exceptions from blanking the app (F10); an `osa:unauthorized` event
  from the API client clears rejected tokens so the panel falls back to
  anonymous mode (F11); sessionStorage writes are guarded for private-mode
  storage (F12); every fetch carries an abort deadline — runtime/external
  invokes get margins beyond their server-side timeouts (F13); rollback now
  requires an explicit confirmation and Enter in the version field drives the
  same flow (F14); `.detail-grid` no longer overflows 320px viewports (F15).
  Gates: tsc clean, 32 vitest tests passing, production build clean.
- 2026-09-05 — Backend finding resolutions (BF1 remains open, in progress):
  BF2 tenant-scoped `ExternalAgentCatalog` (`for_tenant` namespaces mirroring
  `ResourceCatalogs`; records carry `tenant_id`; cross-tenant list/get/invoke/
  refresh/delete return 404 — regression-tested with two JWT tenants);
  BF3 `create_control_plane_app` now wires `PostgresDeploymentRecordRepository`
  when a DSN is set (wiring unit test + CI persistence round-trip test);
  BF4 `PATCH /agents/{id}` persists derived `skills` alongside the definition
  (copy-on-write repository regression test mimicking Postgres read
  semantics); BF5 agent-scoped `/deployments` routes resolve to
  `deployment:read|write` (route-mapping test); BF6+BF8 `call_tool` retries
  only connection-level failures, in-flight failures surface immediately as
  `McpToolExecutionError`, and a client-side `asyncio.wait_for` deadline makes
  timeouts deterministic (fixture `slow_tool` sleep and test timeout margins
  rebalanced so connect cannot eat the call budget under load); BF7 `test_a2a`
  is collection-guarded without the `a2a` extra and the quickstart docs
  (README/CONTRIBUTING/AGENTS) now sync with CI's
  `--extra postgres --extra a2a`; BI1 security guide documents the runtime
  CORS posture (`OSA_RUNTIME_ALLOWED_ORIGINS` / `OSA_DEPLOY_RUNTIME_ALLOWED_ORIGINS`);
  BI2 release validation pins the release heading to the FIRST section after
  `[Unreleased]` (which must be empty), so historical dev-milestone headings
  can no longer satisfy a release by collision. Cleanup: removed stale empty
  `.claude/` and `control-plane/ui/` directories.
- 2026-09-04 — Control Panel UI presentation review of
  `control-plane/frontend` filed 15 fixes (F1–F15) and 7 improvements
  (I1–I7). Read-only review; no source changes made.
- 2026-09-04 — Full-project review and test pass. Ran the automated gates
  (`uv run ruff check/format`, `uv run mypy .`, `uv run pytest --cov`
  matching CI's exact `--extra postgres --extra a2a` sync, and
  `npm run typecheck/test/build` for `control-plane/frontend`) — all clean:
  0 lint/type errors, 525 passed/21 skipped at 85.78% coverage (≥84% floor),
  9/9 frontend test files passing, clean frontend build. Used a gitignored
  `.sandbox/` (see `.gitignore`) to reproduce the documented no-extras
  quickstart in an isolated `UV_PROJECT_ENVIRONMENT` and to empirically
  confirm the release-validation gap against copied manifests/changelog —
  neither touched the real checkout. Two parallel focused reviews (backend
  API/persistence/deployment providers; generic-agent + ADK runtime
  auth/memory/session/MCP/A2A) plus a docs/release-tooling pass filed 7
  fixes (BF1–BF7) and 2 improvements (BI1–BI2) above; every finding was
  independently re-verified against current source before filing, so all
  are marked confirmed rather than merely reported. Also checked and ruled
  out (no genuine issue found): JWT/OIDC validation paths (expired/
  malformed/audience/`active`-claim handling all fail closed, verified with
  a real signed test token), session/memory scope ownership checks in
  `session.py`/`session_service.py`, local and Kubernetes deployment
  provider idempotent-redeploy/terminal-state guards, CORS env-var naming
  agreement between the Control Plane and runtime, credential handling in
  bundle export and generated Kubernetes manifests (`secretKeyRef` only,
  never inline), and `scripts/rollback_release.py`'s channel/digest/tag
  validation. Read-only against tracked files; only `TODO.md` and
  `.gitignore` (adding `.sandbox/`) were changed.
