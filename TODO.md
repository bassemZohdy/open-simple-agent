# Open Simple Agent — Prioritized Backlog

This backlog is based on a source, test, CI, packaging, and documentation review
of `main` on 2026-08-30.

A task is complete only after implementation, automated tests, relevant
documentation, and appropriate failure/security behavior are complete.

## Current baseline

- 221 tests pass; strict mypy, Ruff format, and Ruff lint pass.
- Latest GitHub Actions run on `main` is green.
- Agent definitions, in-memory catalogs, native tool execution, skills,
  in-memory sessions/memory, ADK object construction, agent management API,
  runtime API, and local process deployment provider exist.
- Real-model ADK invocation, configuration bootstrap, MCP runtime, persistence,
  production deployment, A2A, authentication, policy, observability, and UI do
  not exist.
- The Dockerfile defines a non-root base image but has no executable service
  command and is not built by CI.

## Next release gate: runnable agent

The immediate goal is one externally configured agent that starts as a service,
invokes a real model through ADK, calls a native tool through native function
calling, preserves isolated session context, and shuts down cleanly.

The gate is complete only when the same acceptance test passes locally and from
the built container without modifying source code or installing packages at
startup.

---

# P0 — Runnable, correct vertical slice

## P0.1 Configuration bootstrap and resource resolution

- [ ] Define a versioned deployment-bundle format for an `AgentDefinition` plus
  model, tool, skill, MCP, memory, and session references.
- [ ] Add loaders for catalog definitions; reject duplicate names and unknown
  resource types.
- [ ] Add a `SecretResolver` contract and an environment implementation; never
  include resolved values in models, responses, logs, or exceptions.
- [ ] Validate `apiVersion == osa/v1alpha1` and `kind == Agent`.
- [ ] Add positive/range validation for timeouts, TTLs, limits, and iterations.
- [ ] Make all referenced resources fail fast, including models, MCPs, memory
  policies, and secret references; remove silent model fallback in configured
  deployments.
- [ ] Decide and test behavior when `OSA_MODEL_REF` targets a bare-string model
  reference.
- [ ] Document supported file layout and precedence.

**Acceptance:** one command loads an external bundle; invalid/missing references
produce deterministic validation errors before service readiness.

## P0.2 Real model execution through ADK

- [ ] Select and implement the first production model adapter (LiteLLM is the
  leading option; record the decision in an ADR).
- [ ] Route `GenericAdkAgent.invoke()` through the ADK `Runner` for configured
  live models.
- [ ] Replace the text `TOOL_CALL` protocol with ADK-native function calling.
- [ ] Generate tool parameter schemas from `ToolDefinition.capabilities` and
  validate tool arguments.
- [ ] Apply model reference parameters and `ModelRuntimeSettings` with explicit
  precedence.
- [ ] Enforce `runtime.timeout_seconds`, cancellation, and iteration limits.
- [ ] Map ADK/model/tool failures to stable OSA error types.
- [ ] Keep `FakeModelProvider` as a deterministic test adapter, not a production
  fallback.

**Acceptance:** a live model completes a multi-turn request and invokes the
calculator tool through ADK function calling; fake-provider CI remains offline
and deterministic.

## P0.3 Session continuity and isolation

- [ ] Define `SessionProvider`; keep the in-memory provider for tests.
- [ ] Include bounded conversation history in subsequent model turns.
- [ ] Enforce session ownership by agent, caller/user, and tenant where present.
- [ ] Reject user/session identity changes and cross-agent session reuse.
- [ ] Enforce TTL and explicit deletion; define maximum history size.
- [ ] Define behavior for caller-supplied unknown session IDs.
- [ ] Return a stable session ID and make multi-replica requirements explicit.

**Acceptance:** two users and two agents cannot access each other's sessions;
conversation context survives the second request in the same session.

## P0.4 Runtime service lifecycle and image

- [ ] Add a supported CLI or application factory that loads configuration and
  initializes the runtime during FastAPI lifespan startup.
- [ ] Add graceful shutdown for the runtime, sessions, MCP connections, and
  provider clients.
- [ ] Make readiness verify successful configuration/resource initialization.
- [ ] Add a production runtime image target with `CMD`/entrypoint, non-root
  execution, arbitrary UID support, health check, and external configuration.
- [ ] Remove build tools/cache/source not required at runtime and measure image
  size.
- [ ] Add a container smoke test to CI.

**Acceptance:** `docker run` starts a configured agent, readiness becomes green,
an invocation succeeds, and SIGTERM exits cleanly.

## P0.5 Control Plane correctness

- [ ] Require exactly one of `template` or `definition` when creating a
  deployable agent; explicitly model draft-without-definition if required.
- [ ] Ensure request name, definition metadata name, record description, labels,
  skills, and runtime stay consistent.
- [ ] Validate referenced resources before activation/deployment.
- [ ] Map unknown template/resource/agent to 404, duplicate name/version to 409,
  invalid filters/transitions to 400/422.
- [ ] Define a stable error response schema.
- [ ] Make list filters cumulative and add pagination/sort semantics.
- [ ] Make versions immutable snapshots and define optimistic concurrency.
- [ ] Add lifecycle transitions (`draft -> active -> disabled/archived`) and
  reject invalid transitions.

**Acceptance:** API contract tests cover successful paths and every documented
validation/conflict/not-found transition without unexpected 500 responses.

## P0.6 Build and dependency hygiene

- [ ] Replace deprecated `[tool.uv].dev-dependencies` with
  `[dependency-groups].dev` and refresh the lock file.
- [ ] Replace `google-adk>=0.1.0` with a tested compatibility range.
- [ ] Resolve the ADK `BaseAgentConfig` deprecation warning.
- [ ] Establish one authoritative version source across all three packages,
  runtime API metadata, tags, and changelog.
- [ ] Clarify whether packages are published independently or as one release.

**Acceptance:** clean setup/checks emit no project-controlled deprecation
warnings, and all artifacts report the same release version.

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
  usage, tokens, and errors.
- [ ] Add OpenTelemetry spans across the full invocation path.
- [ ] Expose deployment/runtime health without revealing sensitive data.

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
- [ ] Add unit, integration, protocol compatibility, database, container, and
  end-to-end CI stages with appropriate gates.
- [ ] Add coverage reporting and a justified threshold.
- [ ] Add dependency/license/security scans, SBOM, and image signing.
- [ ] Publish versioned images and packages with provenance.
- [ ] Automate tags, changelog validation, GitHub releases, and rollback.

## P3.4 Documentation and examples

- [x] Separate project definition from current implementation documentation.
- [x] Add current architecture, configuration, and API references.
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
- [x] Transitional native tool loop with timeout enforcement.
- [x] In-memory Agent Catalog, immutable Pydantic definitions, templates, and
  resource catalog wrapper.
- [x] Control Plane agent CRUD/version/disable API and health endpoints.
- [x] Runtime invoke/capabilities/health API with programmatic initialization.
- [x] Local process deployment-provider contract and implementation.
- [x] Policy-controlled memory context search and explicit `remember()` writes.

---

# Open review findings

| ID | Finding | Backlog owner |
|---|---|---|
| RV-010 | ADK `Runner` is built but not used for invocation | P0.2 |
| RV-011 | Runtime API has no external configuration/lifespan bootstrap | P0.1, P0.4 |
| RV-012 | Docker image has no runnable command | P0.4 |
| RV-013 | Session history is stored but not used; ownership/TTL are unenforced | P0.3 |
| RV-014 | Unknown configured model silently falls back to fake | P0.1, P0.2 |
| RV-015 | MCP is schema/catalog only; no runtime client exists | P1.3 |
| RV-016 | Control Plane accepts inconsistent/incomplete records and leaks some domain errors as 500 | P0.5 |
| RV-017 | Resource and deployment implementations are not exposed by the API | P1.2, P1.5 |
| RV-018 | Memory policy/limits/retention and persistence are not enforced | P1.4 |
| RV-019 | Both APIs are unauthenticated; local deploy accepts trusted arbitrary commands | P1.5, P2.2 |
| RV-020 | ADK dependency range is too broad and emits a deprecation warning | P0.6 |
| RV-021 | uv development dependency configuration is deprecated | P0.6 |
| RV-022 | Package versions, API versions, and changelog milestone versions are inconsistent | P0.6 |
| RV-023 | Current tests have no live provider/MCP/database/container/A2A coverage or coverage threshold | P3.3 |
| RV-024 | Model parameters, runtime timeout, request metadata, and several config fields are accepted but ignored | P0.1, P0.2 |

Resolved findings RV-001 through RV-009 remain documented in the git history and
changelog; they are omitted here to keep the active backlog focused.
