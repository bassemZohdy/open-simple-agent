# Open Simple Agent — Prioritized Backlog

This backlog is based on a source, test, CI, packaging, and documentation review
of `main` on 2026-08-30, updated on 2026-09-01 after the A2A, deployment,
repository-boundary, authentication, authorization, and resource-ownership
slices landed.

A task is complete only after implementation, automated tests, relevant
documentation, and appropriate failure/security behavior are complete.

## Current baseline

- 434 tests are collected; 414 pass locally and 20 PostgreSQL integration tests
  skip when `OSA_TEST_DATABASE_URL` is unset. Strict mypy, Ruff format, and
  Ruff lint pass with no project-controlled warnings.
- Latest GitHub Actions run on `main` is green, plus a container job that
  builds the runtime image and runs a ready/invoke/SIGTERM smoke test.
- Deployment bundles with fail-fast validation, secret resolution, a LiteLLM
  production model adapter (ADR-001), ADK Runner invocation with native
  function calling, owned/TTL'd/bounded sessions, the `osa-runtime` CLI, a
  non-root container image, Control Plane contract hardening, lockstep
  versioning, the MCP runtime client (ADR-002), memory persistence with
policy-enforced scope/limits/retention (ADR-003), and PostgreSQL Control
Plane persistence with Alembic migrations (ADR-004), A2A interoperability
(ADR-005), and a shared JWT bearer-authentication foundation with opt-in
role/permission route enforcement and runtime tenant binding exist.
- The Kubernetes/OpenShift deployment provider, resource policy, audit events,
  observability, streaming/replica
behavior, and UI do not exist.

## Release gate status

The P0 gate — one externally configured agent that starts as a service,
invokes a real model through ADK, calls a native tool through native function
calling, preserves isolated session context, and shuts down cleanly — is
implemented. The same acceptance test passes locally and from the built
container without source modification: `uv run osa-runtime --config
examples/smoke-bundle` and the CI container job both verify ready → invoke →
SIGTERM. Live-provider verification requires credentials and is tracked under
P3.3.

---

# P0 — Runnable, correct vertical slice (complete)

## P0.1 Configuration bootstrap and resource resolution

- [x] Define a versioned deployment-bundle format (`AgentBundle` document or
  `agent.yaml` + resource directories) for an `AgentDefinition` plus model,
  tool, skill, MCP, memory, and session references.
- [x] Add loaders for catalog definitions; reject duplicate names and unknown
  resource types (`DuplicateResourceError`, `InvalidBundleError`).
- [x] Add a `SecretResolver` contract and an environment implementation; never
  include resolved values in models, responses, logs, or exceptions.
- [x] Validate `apiVersion == osa/v1alpha1` and `kind == Agent`.
- [x] Add positive/range validation for timeouts, TTLs, limits, and iterations
  (runtime, session, memory, model settings, tool timeout, MCP options).
- [x] Make all referenced resources fail fast, including models, MCPs, memory
  policies, and secret references; remove silent model fallback in configured
  deployments.
- [x] Decide and test behavior when `OSA_MODEL_REF` targets a bare-string model
  reference (the env var replaces the bare string; environment wins).
- [x] Document supported file layout and precedence (docs/CONFIGURATION.md).

**Acceptance:** one command loads an external bundle; invalid/missing references
produce deterministic validation errors before service readiness. ✅

## P0.2 Real model execution through ADK

- [x] Select and implement the first production model adapter (LiteLLM via
  ADK's `LiteLlm`; ADR-001). Optional extra `osa-adk-runtime[litellm]`.
- [x] Route `GenericAdkAgent.invoke()` through the ADK `Runner` for configured
  live models.
- [x] Replace the text `TOOL_CALL` protocol with ADK-native function calling.
- [x] Generate tool parameter schemas from `ToolDefinition.capabilities` and
  validate tool arguments (required/properties subset).
- [x] Apply model reference parameters and `ModelRuntimeSettings` with explicit
  precedence (catalog settings < per-agent `ModelRef.parameters`).
- [x] Enforce `runtime.timeout_seconds` (`invocation_timeout`), cancellation,
  and iteration limits (`iteration_limit_exceeded`).
- [x] Map ADK/model/tool failures to stable OSA error types
  (`osa.generic_agent.errors`).
- [x] Keep `FakeModelProvider` as a deterministic test adapter, not a production
  fallback (service bootstrap requires `OSA_ALLOW_FAKE_PROVIDER=1`).

**Acceptance:** a scripted ADK model drives the same Runner path offline — a
multi-turn request invokes the calculator through ADK function calling and the
final answer comes from the model; fake-provider CI remains offline and
deterministic. Live-provider verification is tracked under P3.3.

## P0.3 Session continuity and isolation

- [x] Define `SessionProvider`; keep the in-memory provider for tests.
- [x] Include bounded conversation history in subsequent model turns (ADK
  events stored in the OSA session via `OsaAdkSessionService`, bounded by
  `max_history_messages`).
- [x] Enforce session ownership by agent, caller/user, and tenant where present
  (`tenant_id` request metadata).
- [x] Reject user/session identity changes and cross-agent session reuse
  (`session_access_denied`).
- [x] Enforce TTL and explicit deletion; define maximum history size.
- [x] Define behavior for caller-supplied unknown session IDs (rejected with
  `session_not_found`, never silently created).
- [x] Return a stable session ID and make multi-replica requirements explicit
  (docs/ARCHITECTURE.md).

**Acceptance:** two users and two agents cannot access each other's sessions;
conversation context survives the second request in the same session. ✅

## P0.4 Runtime service lifecycle and image

- [x] Add a supported CLI (`osa-runtime`) and application factory
  (`create_runtime_app`) that loads configuration and initializes the runtime
  during FastAPI lifespan startup.
- [x] Add graceful shutdown for the runtime, sessions, and provider clients
  (lifespan shutdown; SIGTERM exits cleanly).
- [x] Make readiness verify successful configuration/resource initialization.
- [x] Add a production runtime image target with `ENTRYPOINT`/`CMD`, non-root
  execution (fixed UID 10001, arbitrary-UID friendly), health check, and
  external configuration (mounted bundle).
- [x] Remove build tools from the runtime image (`--no-editable` venv, no
  source tree copied).
- [x] Add a container smoke test to CI (build, ready, invoke, SIGTERM).

**Acceptance:** `docker run` starts a configured agent, readiness turns green,
an invocation succeeds, and SIGTERM exits cleanly — verified locally and wired
into CI. ✅

## P0.5 Control Plane correctness

- [x] Require at most one of `template` or `definition` when creating a
  deployable agent (422 on both); explicitly model draft-without-definition.
- [x] Ensure request name, definition metadata name, description, labels,
  skills, and runtime stay consistent (name mismatch → 422; skills derived
  from the definition).
- [x] Validate referenced resources before activation (422 listing missing
  refs).
- [x] Map unknown template/resource/agent to 404, duplicate name/version to
  409, invalid filters/transitions to 400/422.
- [x] Define a stable error response schema (`{"error": {"code", "message"}}`,
  shared with the runtime API).
- [x] Make list filters cumulative and add pagination/sort semantics.
- [x] Make versions immutable snapshots and define optimistic concurrency
  (`expected_version` → 409 on mismatch).
- [x] Add lifecycle transitions (`draft -> active -> disabled/archived`,
  archived terminal) and reject invalid transitions.

**Acceptance:** API contract tests cover successful paths and every documented
validation/conflict/not-found transition without unexpected 500 responses. ✅

## P0.6 Build and dependency hygiene

- [x] Replace deprecated `[tool.uv].dev-dependencies` with
  `[dependency-groups].dev` and refresh the lock file.
- [x] Replace `google-adk>=0.1.0` with a tested compatibility range
  (`>=2.0,<3.0`).
- [x] Resolve the ADK `BaseAgentConfig` deprecation warning (upstream import
  noise; filtered and documented in the pytest config).
- [x] Establish one authoritative version source across all three packages,
  runtime API metadata, tags, and changelog (lockstep manifests +
  `importlib.metadata`-driven API versions, enforced by
  `tests/unit/test_versioning.py`).
- [x] Clarify whether packages are published independently or as one release
  (one lockstep release; CONTRIBUTING.md).

**Acceptance:** clean setup/checks emit no project-controlled deprecation
warnings, and all artifacts report the same release version. ✅

---

# P1 — Managed platform foundation

## P1.1 PostgreSQL Control Plane persistence (complete 2026-08-31)

- [x] Define repository interfaces for agents, versions, templates, resources,
  deployment records, and audit metadata (`AgentRepository`,
  `ResourceDefinitionRepository`, plus `DeploymentRecordRepository` and
  `AuditEventRepository` interfaces; templates stay code-defined built-ins —
  ADR-004).
- [x] Choose async database/ORM and migration tooling; record an ADR
  (PostgreSQL 16 + SQLAlchemy 2.0 async + Alembic — ADR-004).
- [x] Implement PostgreSQL repositories and migrations
  (`osa_agents`, `osa_agent_versions`, `osa_resource_definitions`; Alembic via
  the `osa-cp-migrate` CLI).
- [x] Add transactions, uniqueness constraints, optimistic locking, and startup
  migration policy (transactional writes; unique name and `(agent_id, version)`
  constraints mapped to typed errors; compare-and-set on `current_version`;
  explicit migrations — apps verify connectivity and never auto-migrate).
- [x] Add isolated PostgreSQL integration tests (CI service container;
  skipped locally without `OSA_TEST_DATABASE_URL`).
- [x] Verify records and version history survive restart.

**Acceptance:** two Control Plane replicas share consistent state and restart
without data loss. ✅ (repository-level: two `PostgresAgentRepository`
instances over one database see each other's writes; a fresh engine sees all
records and version history)

## P1.2 Resource and template APIs (complete 2026-08-31)

- [x] Expose CRUD/list/search APIs for models, MCPs, tools, skills, memory
  policies, and templates (`/resources/{kind}` CRUD + import/export;
  `GET /templates` read-only; session policies arrive with P1 session
  provider work — tracked under P1.5 hardening).
- [x] Remove direct mutation of catalog private dictionaries; add explicit
  update/delete contracts (`register_*`/`delete_*` on `ResourceCatalogs`;
  `delete()` on every domain catalog).
- [x] Add reference-usage checks before deleting a resource (409 naming the
  referencing agents).
- [x] Prevent API responses from exposing secret values (`credential_ref`
  redacted to source/key/env_var; pinned by tests).
- [x] Add import/export validation for configuration bundles
  (`POST /resources/import`, `GET /resources/export`, envelope format shared
  with deployment bundles).

**Acceptance:** an administrator can create every resource required by an agent
without application code and cannot delete an in-use resource accidentally. ✅
(deployment APIs remain in P1.5; session policies are part of the session
provider contract, enforced in P0.3)

## P1.3 MCP runtime client (complete 2026-08-31)

- [x] Select the supported MCP SDK and protocol-version policy; record an ADR
  (official `mcp` SDK `>=1.24,<2` matching google-adk's extra; SDK majors are
  the compatibility boundary — ADR-002).
- [x] Implement connection manager for stdio and Streamable HTTP; decide
  whether legacy SSE remains supported (it does not — runtime rejects it with
  `mcp_transport_not_supported`).
- [x] Resolve credentials, TLS, timeout, retry, response-size, and lifecycle
  settings from `McpDefinition`.
- [x] Discover tools/resources/prompts and apply server/agent tool filters
  (tools; resources/prompts deferred until a concrete requirement).
- [x] Namespace tool names and preserve MCP origin metadata.
- [x] Bridge MCP tools to ADK with schemas and bounded results
  (`OsaMcpToolset` + `McpFunctionTool`).
- [x] Add connection pooling, reconnect, cancellation, and graceful close
  (per-runtime pool, keeper-task ownership, idempotent close).
- [x] Add protocol-level tests using a deterministic local MCP server
  (stdio subprocess + localhost Streamable HTTP; discovery, filters,
  invocation, tool errors, timeout, oversize, unreachable, credential, 401).

**Acceptance:** a configured agent discovers and invokes a filtered MCP tool
(through the ADK Runner with native function calling); timeout/auth/oversize/
disconnect failures are predictable and observable. ✅

## P1.4 Memory policy and persistence (complete 2026-08-31)

- [x] Resolve `MemoryConfig.policy` through the policy catalog
  (`MemoryPolicyCatalog`; referenced policy is authoritative, missing
  references fail fast, a disabled policy blocks writes).
- [x] Define scope IDs for user, agent, tenant, and application scopes
  (`memory_scope_id`; tenant from request metadata).
- [x] Enforce enabled state, limits, retention, and deletion (per-scope
  `max_entries` eviction and `retention_days` purging via
  `MemoryProvider.enforce()` after writes and before reads).
- [x] Design explicit/policy-driven extraction; never persist every raw turn by
  default (extraction is `remember()`-only; `auto_extract` reserved, ADR-003).
- [x] Select the first persistent provider based on access/search requirements
  (PostgreSQL via SQLAlchemy async + asyncpg; ILIKE substring search mirroring
  the in-memory provider; pgvector deferred — ADR-003).
- [x] Add authorization and cross-scope isolation tests (unit matrix plus
  PostgreSQL integration tests against a real PG 16 in CI; restart survival,
  scope isolation, case-insensitive search, LIKE escaping, eviction,
  retention).

**Acceptance:** selected memory survives restart, respects scope/retention/limit,
and is never visible to an unauthorized user or agent. ✅

## P1.5 Deployment API and providers (mostly complete 2026-08-31)

- [x] Persist deployment intent and observed state
  (`DeploymentRecordRepository`; in-memory + PostgreSQL, Alembic migration
  0002).
- [x] Expose deploy/status/stop/restart/log APIs through the Control Plane
  (plus rollback to an earlier immutable version snapshot and per-agent
  deployment history).
- [x] Harden the local provider: bounded log capture, startup failure with
  captured logs, health probing with a probe window, dead-process detection
  on status, idempotent re-deploy of the same running command, cleanup on
  stop/shutdown, restart preserving deployment identity.
- [x] Ensure arbitrary process commands are never accepted from an untrusted API
  (launch commands are synthesized from the server-owned
  `OSA_DEPLOY_COMMAND_TEMPLATE`; unknown request fields are rejected).
- [x] Container provider: not implemented — the runtime image + CLI already
  provides the container path, so a separate provider is unnecessary.
- [ ] Implement Kubernetes/OpenShift provider: Deployment, Service, probes,
  ConfigMap/Secret references, scale, rolling update, rollback, and status
  watch (remains open).

**Acceptance:** the Control Plane deploys a versioned agent without importing
ADK internals, observes readiness, restarts it, and rolls back safely. ✅
(local provider; Kubernetes scheduling remains open)

---

# P2 — Interoperability and production controls

## P2.1 A2A and external agents (complete 2026-08-31)

- [x] Select and pin the supported A2A specification/SDK version; record an ADR
  (`a2a-sdk[http-server]>=1.0,<2`, the protobuf-typed 1.x line within
  google-adk's own a2a range — ADR-005).
- [x] Generate Agent Cards from validated definitions and resolved skills
  (`build_agent_card`; served at the well-known path when `spec.a2a.enabled`).
- [x] Expose A2A invocation and required task/status semantics (JSON-RPC
  `message/send`: submitted → working → completed artifact / failed with
  deterministic error text; A2A context ids map to OSA sessions).
- [x] Define security-scheme configuration and caller identity propagation
  (schemes externalized via configuration; enforcement lands with P2.2 —
  recorded in ADR-005).
- [x] Add external-agent record type distinct from managed agents
  (`ExternalAgentRecord`; structurally barred from deployment — 422).
- [x] Register/validate/refresh Agent Cards by URL and track health
  (422 on unreachable at registration; refresh re-checks health).
- [x] Implement remote A2A invocation, authentication, errors, and timeouts
  (`invoke_remote_agent` with bounded timeout and `a2a_remote_failed`
  mapping; token auth deferred to P2.2).
- [x] Add external compatibility tests (offline protocol tests against a
  real in-process A2A server: card fetch, task completion, error mapping,
  A-invokes-B acceptance).

**Acceptance:** Managed Agent A invokes Managed Agent B and a deterministic
external A2A agent; external records cannot be deployed by OSA. ✅

## P2.2 Authentication and authorization

Current progress (2026-09-01): both APIs share externally configured JWT
Bearer validation using issuer, audience, JWKS, algorithm, expiry, and scope
checks. `OSA_AUTH_MODE=required` protects non-public routes. With
`OSA_AUTH_ENFORCE_PERMISSIONS=true`, common role/permission claims and scopes
enforce stable route permissions; runtime invocations bind omitted `user_id`
and `tenant_id` to token claims and reject spoofing. Control Plane agents,
deployments, and resources now use the same tenant boundary. This remains a
bounded authorization slice: OIDC token introspection/live-provider coverage,
policy evaluation, A2A security schemes, credential adapters, and audit events
are still open.

- [x] Resolve standard OIDC discovery metadata to a validated `jwks_uri` when
  no explicit JWKS URL is configured; support an explicit discovery URL and
  keep explicit JWKS configuration authoritative. This is deterministic
  offline coverage, not live provider validation.
- [ ] Complete OIDC/OAuth authentication for Control Plane and runtime APIs;
  add token introspection where required and live identity-provider coverage.
- [x] Define baseline administrator, operator, viewer, agent, caller, user, and
  service roles plus stable route permissions; accept common role, permission,
  and scope claims.
- [ ] Define enterprise identity lifecycle and permission semantics beyond the
  built-in baseline.
- [x] Enforce tenant ownership for Control Plane managed agent creation,
  listing, reads, updates, versions, lifecycle transitions, and deletion;
  persist the owner in PostgreSQL migration 0003.
- [x] Extend tenant ownership to Control Plane deployment records and
  operations; deployments inherit agent ownership and persist it in migration
  0004.
- [x] Extend HTTP ownership/tenant boundaries to Control Plane resources;
  persist resource owners in migration 0005, isolate equal names in separate
  catalog namespaces, and retain the existing domain-level session/memory
  isolation checks.
- [ ] Add tool/MCP/skill/model/A2A allow/deny policy independent of prompts.
- [ ] Add API key/OAuth/mTLS credential adapters for MCP/A2A as required.
- [ ] Add audit events for every management mutation and privileged invocation.

**Acceptance:** no production management or invocation endpoint is anonymous;
authorization and secret-redaction tests cover deny paths.

## P2.3 Observability

- [ ] Add structured logs with invocation, session, agent, user/caller, and
  deployment correlation IDs.
- [ ] Add secret and sensitive-payload redaction with bounded capture.
- [ ] Add metrics for invocation/model/tool/MCP/memory/session/A2A latency,
  token usage, and errors.
- [ ] Add OpenTelemetry spans across the full invocation path.
- [ ] Expose deployment/runtime health without leaking sensitive data.

**Acceptance:** one invocation can be traced end-to-end and a failed tool/MCP
call can be diagnosed without exposing secrets.

## P2.4 Streaming and replica behavior

- [ ] Define SSE or another supported streaming contract for the runtime API.
- [ ] Map ADK events to stable OSA events and handle client cancellation.
- [ ] Verify session consistency and idempotency across replicas.
- [ ] Add load, backpressure, timeout, and disconnect tests.

**Acceptance:** streaming works through two runtime replicas without losing
session ownership or leaking another caller's events.

---

# P3 — Product surface and distribution

## P3.1 Control Panel

*Start after P1 APIs and P2 authentication contracts are stable.*

- [ ] Create TypeScript/React application and authenticated shell.
- [ ] Add agents, versions, templates, resources, deployments, health, and audit
  views.
- [ ] Add validated agent creation/edit/clone flows.
- [ ] Add invocation console with sessions, streaming, tools, and A2A tests.
- [ ] Add accessibility, localization, responsive, error, and empty/loading
  states.

## P3.2 Manager Agent

*Start after deterministic Control Plane APIs, authorization, and approval
policy are stable.*

- [ ] Expose narrow Control Plane tools for search, draft, validate, compare,
  version, deploy, restart, scale, rollback, health, and logs.
- [ ] Require explicit approval for high-impact operations.
- [ ] Prevent raw secret access, direct database access, policy bypass, and
  direct Kubernetes manipulation.
- [ ] Test prompt-injection and confused-deputy scenarios.

## P3.3 Packaging, CI/CD, and release

- [ ] Add separate runtime, Control Plane, and UI images.
- [x] Add container build and smoke test to CI (ready → invoke → SIGTERM).
- [ ] Add unit, integration, protocol compatibility, database, and end-to-end
  CI stages with appropriate gates.
- [ ] Add a live-provider acceptance job (opt-in secret; runs the P0.2
  acceptance test against one real model).
- [ ] Add coverage reporting and a justified threshold.
- [ ] Add dependency/license/security scans, SBOM, and image signing.
- [ ] Publish versioned images and packages with provenance.
- [ ] Automate tags, changelog validation, GitHub releases, and rollback.

## P3.4 Documentation and examples

- [x] Separate project definition from current implementation documentation.
- [x] Add current architecture, configuration, and API references.
- [x] Add a runnable smoke bundle example (`examples/smoke-bundle`).
- [ ] Add runnable minimal, native-tool, memory, and support-agent examples.
- [ ] Add runnable MCP, A2A, and external-agent examples as those features land.
- [ ] Add operations, deployment, security, observability, upgrade, and recovery
  guides before production release.
- [ ] Generate or validate API/schema reference from source in CI.

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
- [ ] Enterprise external policy engine.

---

# Completed foundation

- [x] uv workspace, PEP 420 namespace, Python 3.12, CI, Apache-2.0 license.
- [x] Strict agent definition schema and selected environment overrides.
- [x] Agent, runtime, model-provider, tool, MCP, skill, session, and memory
  contracts.
- [x] In-memory model/tool/MCP/skill/memory catalogs and session manager.
- [x] Deterministic fake model provider and calculator tool.
- [x] ADK `LlmAgent`/`Runner` construction and native tool wrappers.
- [x] In-memory Agent Catalog, immutable Pydantic definitions, templates, and
  resource catalog wrapper.
- [x] Control Plane agent CRUD/version/disable API and health endpoints.
- [x] Runtime invoke/capabilities/health API with programmatic initialization.
- [x] Local process deployment-provider contract and implementation.
- [x] Policy-controlled memory context search and explicit `remember()` writes.
- [x] P0 release gate (see above): bundles, secrets, LiteLLM adapter, Runner
  invocation, native function calling, session isolation, service CLI, runtime
  image, Control Plane contract hardening, lockstep versioning.

---

# Open review findings

| ID | Finding | Backlog owner |
|---|---|---|
| RV-016 | Control Plane deployment routes missing (refs are validated at activation) | P1.5 |
| RV-017 | Resource and deployment implementations are not exposed by the API | P1.2, P1.5 |
| RV-019 | JWT bearer authentication and opt-in role/permission route enforcement are available but disabled by default; A2A credential enforcement, policy authorization, and audit events remain open | P2.2 |
| RV-023 | Current tests have no live-provider, Kubernetes, live-identity-provider, multi-replica, or coverage-threshold gate | P2.2, P2.4, P3.3 |
| RV-024 | Runtime binds `tenant_id`/`tid` claims to invocation metadata; Control Plane agents, deployments, and resources are tenant-owned, while model/tool policy still does not use tenant metadata | P2.2 |

Resolved on 2026-08-31 (documented here, then removed from the active table on
the next backlog review): RV-018 (memory policy/limits/retention/persistence,
ADR-003), RV-015 (MCP runtime client, ADR-002), RV-010 (Runner
now used for invocation), RV-011
(lifespan bundle bootstrap), RV-012 (runnable image with CMD), RV-013 (session
ownership/TTL/bounded history), RV-014 (no silent model fallback), RV-016
(Control Plane 400/404/409/422 mapping and stable error schema), RV-020/RV-021
(dependency ranges and uv config), RV-022 (lockstep version source).
Resolved findings RV-001 through RV-009 remain documented in the git history
and changelog.
