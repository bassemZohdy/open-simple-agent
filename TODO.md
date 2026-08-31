# Open Simple Agent — Prioritized Backlog

This backlog is based on a source, test, CI, packaging, and documentation review
of `main` on 2026-08-30, updated on 2026-08-31 after the P0 release gate
landed.

A task is complete only after implementation, automated tests, relevant
documentation, and appropriate failure/security behavior are complete.

## Current baseline

- ~320 tests pass; strict mypy, Ruff format, and Ruff lint pass with no
  project-controlled warnings.
- Latest GitHub Actions run on `main` is green, plus a container job that
  builds the runtime image and runs a ready/invoke/SIGTERM smoke test.
- Deployment bundles with fail-fast validation, secret resolution, a LiteLLM
  production model adapter (ADR-001), ADK Runner invocation with native
  function calling, owned/TTL'd/bounded sessions, the `osa-runtime` CLI, a
  non-root container image, Control Plane contract hardening, and lockstep
  versioning exist.
- MCP runtime, persistence, production deployment providers, A2A,
  authentication, policy, observability, and UI do not exist.

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

## P1.1 PostgreSQL Control Plane persistence

- [ ] Define repository interfaces for agents, versions, templates, resources,
  deployment records, and audit metadata.
- [ ] Choose async database/ORM and migration tooling; record an ADR.
- [ ] Implement PostgreSQL repositories and migrations.
- [ ] Add transactions, uniqueness constraints, optimistic locking, and startup
  migration policy.
- [ ] Add isolated PostgreSQL integration tests.
- [ ] Verify records and version history survive restart.

**Acceptance:** two Control Plane replicas share consistent state and restart
without data loss.

## P1.2 Resource and template APIs

- [ ] Expose CRUD/list/search APIs for models, MCPs, tools, skills, memory
  policies, session policies, and templates.
- [ ] Remove direct mutation of catalog private dictionaries; add explicit
  update/delete contracts.
- [ ] Add reference-usage checks before deleting a resource.
- [ ] Prevent API responses from exposing secret values.
- [ ] Add import/export validation for configuration bundles.

**Acceptance:** an administrator can create every resource required by an agent
without application code and cannot delete an in-use resource accidentally.

## P1.3 MCP runtime client

- [ ] Select the supported MCP SDK and protocol-version policy; record an ADR.
- [ ] Implement connection manager for stdio and Streamable HTTP; decide whether
  legacy SSE remains supported.
- [ ] Resolve credentials, TLS, timeout, retry, response-size, and lifecycle
  settings from `McpDefinition`.
- [ ] Discover tools/resources/prompts and apply server/agent tool filters.
- [ ] Namespace tool names and preserve MCP origin metadata.
- [ ] Bridge MCP tools to ADK with schemas and bounded results.
- [ ] Add connection pooling, reconnect, cancellation, and graceful close.
- [ ] Add protocol-level tests using a deterministic local MCP server.

**Acceptance:** a configured agent discovers and invokes a filtered MCP tool;
timeout/auth/oversize/disconnect failures are predictable and observable.

## P1.4 Memory policy and persistence

- [ ] Resolve `MemoryConfig.policy` through the policy catalog.
- [ ] Define scope IDs for user, agent, tenant, and application scopes.
- [ ] Enforce enabled state, limits, retention, and deletion.
- [ ] Design explicit/policy-driven extraction; never persist every raw turn by
  default.
- [ ] Select the first persistent provider based on access/search requirements
  (PostgreSQL vs Redis/vector extension); record an ADR.
- [ ] Add authorization and cross-scope isolation tests.

**Acceptance:** selected memory survives restart, respects scope/retention/limit,
and is never visible to an unauthorized user or agent.

## P1.5 Deployment API and providers

- [ ] Persist deployment intent and observed state.
- [ ] Expose deploy/status/stop/restart/log APIs through the Control Plane.
- [ ] Harden the local provider: capture bounded logs, startup failure, health
  probing, concurrent calls, cleanup, and idempotency.
- [ ] Ensure arbitrary process commands are never accepted from an untrusted API.
- [ ] Implement a container provider only if it remains a required local target.
- [ ] Implement Kubernetes/OpenShift provider: Deployment, Service, probes,
  ConfigMap/Secret references, scale, rolling update, rollback, and status watch.

**Acceptance:** the Control Plane deploys a versioned agent without importing
ADK internals, observes readiness, restarts it, and rolls back safely.

---

# P2 — Interoperability and production controls

## P2.1 A2A and external agents

- [ ] Select and pin the supported A2A specification/SDK version; record an ADR.
- [ ] Generate Agent Cards from validated definitions and resolved skills.
- [ ] Expose A2A invocation and required task/status semantics.
- [ ] Define security-scheme configuration and caller identity propagation.
- [ ] Add external-agent record type distinct from managed agents.
- [ ] Register/validate/refresh Agent Cards by URL and track health.
- [ ] Implement remote A2A invocation, authentication, errors, and timeouts.
- [ ] Add external compatibility tests.

**Acceptance:** Managed Agent A invokes Managed Agent B and a deterministic
external A2A agent; external records cannot be deployed by OSA.

## P2.2 Authentication and authorization

- [ ] Add OIDC/OAuth authentication to Control Plane and runtime APIs.
- [ ] Define administrator, operator, viewer, agent, caller, user, and service
  identities and permissions.
- [ ] Enforce ownership/tenant boundaries for agents, sessions, and memory.
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
| RV-015 | MCP is schema/catalog only; no runtime client exists | P1.3 |
| RV-016 | Control Plane resource refs not validated before deployment; deployment routes missing | P1.5 |
| RV-017 | Resource and deployment implementations are not exposed by the API | P1.2, P1.5 |
| RV-018 | Memory policy/limits/retention and persistence are not enforced | P1.4 |
| RV-019 | Both APIs are unauthenticated; local deploy accepts trusted arbitrary commands | P1.5, P2.2 |
| RV-023 | Current tests have no live provider/MCP/database/A2A coverage or coverage threshold | P3.3 |
| RV-024 | Request metadata (beyond `tenant_id`) is accepted but not used by model/tool policy | P2.2 |

Resolved on 2026-08-31 (documented here, then removed from the active table on
the next backlog review): RV-010 (Runner now used for invocation), RV-011
(lifespan bundle bootstrap), RV-012 (runnable image with CMD), RV-013 (session
ownership/TTL/bounded history), RV-014 (no silent model fallback), RV-016
(Control Plane 400/404/409/422 mapping and stable error schema), RV-020/RV-021
(dependency ranges and uv config), RV-022 (lockstep version source).
Resolved findings RV-001 through RV-009 remain documented in the git history
and changelog.
