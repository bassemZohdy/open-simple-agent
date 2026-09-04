# Changelog

### Added — P3.1: Managed-agent runtime invocation from Control Panel
- Runtime CORS support: opt-in `OSA_RUNTIME_ALLOWED_ORIGINS` environment
  variable adds `CORSMiddleware` to the runtime FastAPI application so browser
  clients (e.g. the Control Panel) can call `/v1/invoke` cross-origin.
  Preflight OPTIONS requests bypass the bearer boundary; CORS runs outermost.
- Deployment service `OSA_DEPLOY_RUNTIME_ALLOWED_ORIGINS` is forwarded as
  `OSA_RUNTIME_ALLOWED_ORIGINS` to launched runtime processes.
- Control Panel `invokeRuntimeEndpoint` client method posts directly to the
  runtime endpoint (never through the Control Plane); authentication is not
  forwarded — runtimes behind `OSA_AUTH_MODE=require` need their own
  credential story (see ADR-008).
- DeploymentsPage managed invocation form: message input + "Send test message"
  button appears on deployments that publish an `invoke_url`; response output
  rendered inline.
- Backend tests for CORS origin parsing, preflight behavior, actual-request
  headers, and unconfigured behavior (`tests/unit/test_runtime_cors.py`).
- Frontend test for managed invocation round-trip.

### Added — P3.3: Image channel rollback automation
- `Rollback image channel` workflow (manual dispatch from `main`): re-points
  the mutable `latest` tag at any previously published digest with
  `docker buildx imagetools create` — no rebuild, immutable version tags are
  untouchable, and digest-bound Cosign signatures/attestations carry over.
  Policy validation and command planning live in
  `scripts/rollback_release.py` with unit tests.

### Added — P1.5/P3.1: Deployment invoke URLs (ADR-008)
- Deployment records expose an optional public runtime endpoint synthesized
  server-side from `OSA_DEPLOY_INVOKE_URL_TEMPLATE` (placeholders
  `{deployment_id}`, `{agent_id}`, `{version}`, `{port}`); `null` when unset.
  Persisted via migration 0007 and shown in the Control Panel deployment
  detail view. ADR-008 records the decision: no Control Plane proxy —
  invocation traffic stays direct to runtimes.

### Added — P3.1: Control Panel keyboard accessibility
- Skip-to-content link targeting the main content region, and focus moves to
  the content region on every route change so keyboard and screen-reader
  navigation starts at the page body; covered by AppShell tests.

### Added — P2.2: Enterprise identity lifecycle semantics (ADR-007)
- ADR-007 defines claim-driven lifecycle semantics with the IdP as lifecycle
  authority: provisioning/deprovisioning expectations, disabled identities,
  role/group synchronization, service-account lifecycle, the permission
  revocation bound (token lifetime; unknown-`kid` JWKS refresh is immediate),
  and stale-token behavior. The external policy engine stays deferred with an
  explicit revisit trigger.
- The shared JWT/introspection validation path now rejects tokens carrying an
  `active` claim that is not boolean `true`; operator guidance added to the
  security guide.

### Added — P3.1: A2A invocation test console
- `InvocationPage` at `/console`: registered external agents listed with
  card/version, URL, and health status; message + timeout invocation through
  `POST /external-agents/{id}/invoke` with inline response rendering and
  502/remote-failure surfacing. Managed-agent sessions/streaming/tool traces
  remain pending the runtime-access design decision.

### Added — P3.1: Agent authoring Control Panel flows
- Validated create/clone panel on the Agents page: empty draft, built-in
  template, or pasted JSON definition sources with client pre-flight
  validation (required name, JSON parse, `metadata.name` match) and inline
  surfacing of Control Plane 422 validation errors; successful creates
  navigate to the new agent's detail page.
- Clone deep-link from agent detail pages pre-fills name/description metadata
  (definitions are write-only in the Control Plane API, so copies re-select a
  template or paste a definition).

### Added — P3.1: Audit and metrics Control Panel
- `AuditPage`: recent tenant-scoped audit events (`GET /audit-events`) with
  client-side action filtering, bounded limit selection, and truncated
  redaction-safe detail rendering, plus operational metrics (`GET /metrics`)
  parsed into a sample table with labels and a collapsible raw Prometheus
  exposition view.

### Added — P3.1: Deployment lifecycle/status/logs Control Panel
- `DeploymentsPage`: per-agent deployment history with intent-only deploy,
  stop/restart/rollback lifecycle actions, live observed-status refresh, and
  bounded captured-log inspection with tail selection; failed deployments get
  a distinct status badge. Agent detail pages deep-link to the agent's
  deployment history.
- Fixed mislabeled deployment audit actions: observed-status checks now
  record `deployment.status` and stops record `deployment.stop`.

### Added — P3.4: API schema validation in CI + operations guides
- `tests/unit/test_openapi_contract.py`: both FastAPI applications must emit
  spec-valid OpenAPI 3.1, every route documented in `docs/API.md` must exist
  in the matching app's schema (path-parameter wildcards normalized), and
  undocumented Control Plane routes fail the suite — the API reference
  cannot drift from the code.
- `docs/guides/`: operations (health, observability, deployments, database,
  upgrades), deployment (runtime/Control Plane configuration, containers,
  migrations, multi-replica notes), security (auth, tenancy, secrets,
  policy, supply chain, audit), and upgrade (lockstep versions, migrations,
  rolling replicas, rollback) guides, linked from the documentation index.


### Added — P3.3: License scanning and SBOMs
- **License scanning + SBOMs (P3.3)**
  - CI `security` job: pip-licenses exact-string allow-list over the
    exported runtime lock (new/unreviewed licenses fail the build)
  - CycloneDX SBOM for the Python dependency lock (`security` job) and Syft
    CycloneDX SBOMs for both container images (`container` job), uploaded as
    workflow artifacts

### Added — P3.3/P3.4: Packaging, CI hardening, and runnable examples
- **Runnable examples (P3.4)**: `examples/minimal`, `examples/native-tool`,
  `examples/memory`, and `examples/mcp` (bundles a real stdio MCP server) —
  each schema- and reference-validated in CI by
  `tests/unit/test_examples.py`; the MCP server is spawned over stdio by the
  runtime.
- **CI/Packaging (P3.3)**: coverage reporting with an 84% gate in the test
  job; new `security` job running pip-audit over the exported full lock;
  `Dockerfile.control-plane` (non-root, health check, PG-ready) built and
  health-smoke-tested in CI alongside the runtime image.

### Added — P2.4: Streaming and replica behavior

- `POST /v1/invoke/stream` (SSE): stable OSA event contract (`osa.started`,
  `osa.message.delta`, `osa.message`, `osa.error`) with JSON payloads
  (`type`, `invocation_id`, `session_id`, `text`, monotonic `seq`);
  `osa.message` text is exactly what `POST /v1/invoke` returns.
- `GenericAdkAgent.stream_invoke` maps ADK Runner events to OSA events in
  one pass, enforcing `runtime.timeout_seconds` across the stream lifetime
  and `max_iterations` mid-stream; no server-side buffering (consumer
  backpressure is natural); disconnect cancels the underlying ADK run.
- The shared bearer/OIDC auth middleware covers the stream route identically
  to non-streaming invoke.
- Replica tests: two agents over a shared `SessionProvider` keep
  conversation context across replicas and enforce ownership identically;
  concurrent streams never leak another invocation's events; 8-way
  concurrent stream load; disconnect-cancellation and timeout paths.

### Added — P2.2: Definition resource policy

- Added exact allow/deny policy rules for model, tool, MCP, skill, and inbound
  A2A resources. Policies are checked before runtime construction and denied
  references return the stable `policy_violation` error.

All notable changes to Open Simple Agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

> **Versioning note:** entries `0.1.0` through `0.14.0` record internal
> development milestones; they are not published package releases. All package
> manifests now share one lockstep release version, enforced by
> `tests/unit/test_versioning.py`. Automated release publishing remains
> tracked in `TODO.md` (P3.3).

## [Unreleased]

### Added — P3.1: Agent detail and lifecycle Control Panel
- Added a typed `GET /agents/{agent_id}/versions` endpoint that exposes only
  immutable version metadata; definitions remain outside the UI/API history
  response because they may contain credentials or deployment-only settings.
- Added the Control Panel agent detail route with version history, immutable
  version snapshot creation, and guarded activate/disable/archive actions.
- Added frontend and API contract coverage for version metadata redaction and
  lifecycle/version interactions.

### Added — P2.2: Resource tenant ownership
- Control Plane resource definitions now have tenant-scoped repository keys and catalog namespaces; equal model, tool, skill, MCP, and memory-policy names can exist independently across tenants.
- Resource CRUD, import/export, deletion reference checks, agent activation, and deployment bundle export resolve only the authenticated tenant's resources.
- PostgreSQL migration 0005 adds the resource tenant owner, preserves existing unauthenticated data in the shared scope, and changes uniqueness to `(tenant_id, kind, name)`.
- Authentication regression coverage verifies cross-tenant reads are hidden and activation cannot resolve another tenant's resource.

### Added — P2.2: Authentication foundation
- **Shared JWT/OIDC authentication boundary**
  - `AuthSettings` reads `OSA_AUTH_*` configuration for disabled, optional, or required Bearer authentication in both FastAPI applications
  - JWKS-backed validation checks issuer, audience, expiry, subject, allowed signing algorithms, and configured scopes without retaining or logging token material
  - OIDC discovery is supported when `OSA_AUTH_JWKS_URL` is omitted; the standard or explicitly configured discovery document must match the configured issuer and advertise an absolute HTTP `jwks_uri`, while an explicit JWKS URL remains authoritative
  - Non-public HTTP routes return stable 401/403 envelopes; runtime invocations derive omitted `user_id` from the validated subject and reject identity spoofing
  - Optional `OSA_AUTH_ENFORCE_PERMISSIONS` maps stable route permissions from roles, explicit permission claims, and scopes; built-in administrator/operator/viewer/agent/caller/user/service mappings are covered by tests
  - Runtime `tenant_id`/`tid` claims bind to invocation metadata, with injection for omitted metadata and rejection of mismatches or tenant spoofing
  - Offline generated-key tests cover configuration, signature/claim failures, scope denial, role/permission extraction, Control Plane middleware, runtime middleware, and tenant binding; resource policy, A2A security schemes, and credential adapters remain open

### Added — P2.2: Audit events
- Successful Control Plane agent, resource, deployment, external-agent, and privileged external-agent invocation operations now append redaction-safe, tenant-scoped audit events.
- Added `GET /audit-events`, the `audit:read` permission, in-memory and PostgreSQL repositories, and migration 0006; event details exclude prompts, definitions, credentials, request payloads, and remote outputs.

### Added — P2.1: A2A and external agents
- **A2A and external agents (ADR-005)**
  - `a2a-sdk[http-server]>=1.0,<2` (matching google-adk's a2a range) as the optional `osa-adk-runtime[a2a]` extra; client utilities available through `osa-generic-agent[a2a]`
  - Agent Cards generated from validated definitions plus resolved skills; served at the well-known path when `spec.a2a.enabled`
  - A2A JSON-RPC server: `message/send` maps one task per invocation (submitted → working → completed artifact / failed with deterministic error text); A2A context ids map to OSA sessions created on first contact
  - External-agent records distinct from managed agents: register by URL with card fetch + validation (422 on unreachable), refresh with health tracking, invoke through the A2A client with bounded timeouts (`a2a_remote_failed`), delete; duplicates 409
  - External records are structurally barred from deployment (422 naming the record type)
  - Acceptance covered offline: served agent B invoked by agent A through the A2A protocol via a native tool bridge, plus a deterministic external A2A server

### Added — P1.5: Deployment APIs and provider hardening
- **Deployment APIs and providers (P1.5)**
  - `LocalDeploymentProvider` hardened: bounded per-deployment log capture (`logs(tail)`), health-probe window during startup (early exit or missed probe fails the deployment with captured logs), dead-process detection on status, idempotent re-deploy of the same running command, process cleanup on stop/shutdown; restart preserves deployment identity
  - `DeploymentService` deploys an active agent's current definition: exports the definition plus referenced resources to a bundle directory and launches a runtime via a server-owned command template (`OSA_DEPLOY_COMMAND_TEMPLATE`) — launch commands are never accepted from API input (unknown request fields rejected); no ADK internals imported
  - Deployment routes: `POST /agents/{id}/deploy`, `GET /agents/{id}/deployments`, `GET /deployments/{id}`, stop, restart, `GET /deployments/{id}/logs`, and rollback to an earlier immutable version snapshot
  - Intent and observed state persist through `DeploymentRecordRepository` (`PostgresDeploymentRecordRepository` + Alembic migration 0002 `osa_deployments`; in-memory default)

### Added — P1.2: Resource and template APIs
- **Resource and template APIs (P1.2)**
  - CRUD/list/search routes for models, tools, skills, MCPs, and memory policies (`/resources/{kind}`), validated against the domain schema and persisted write-through to the `ResourceDefinitionRepository` (survive restarts, shared across replicas)
  - Explicit update/delete contracts on `ResourceCatalogs` and the domain catalogs (`delete()` methods) — no private-dictionary mutation anywhere in the API path
  - Reference-usage checks before deletion: a resource referenced by any agent definition returns 409 naming the referencing agents
  - Secret redaction pinned by tests: `credential_ref` exposes only non-secret coordinates (source/key/env_var) and is redacted defensively in every response
  - Bundle import/export: `POST /resources/import` and `GET /resources/export` use the deployment-bundle resource envelope format (`{apiVersion, kind, spec}`)
  - Read-only `GET /templates` listing of built-in agent templates

### Added — P1.1: Control Plane PostgreSQL persistence
- **Control Plane persistence (ADR-004)**
  - Repository contracts: `AgentRepository` (records + versions), `ResourceDefinitionRepository` (models/tools/skills/MCPs/memory policies as kind + JSONB spec), plus `DeploymentRecordRepository` and `AuditEventRepository` interfaces (wired in P1.5/P2.2)
  - PostgreSQL implementations (SQLAlchemy 2.0 async over asyncpg, optional `osa-control-plane[postgres]` extra): transactional writes, unique constraints (agent name; `(agent_id, version)`) mapped to the existing typed errors, compare-and-set optimistic locking on `current_version`, `FOR UPDATE` row locks for lifecycle transitions, cascade version history
  - Alembic owns the schema (`osa_agents`, `osa_agent_versions`, `osa_resource_definitions`); migrations are an explicit ops step via the `osa-cp-migrate` CLI — the app verifies connectivity and never auto-migrates (multi-replica race)
  - `create_control_plane_app()` selects backends from `OSA_CONTROL_PLANE_DATABASE_URL`; in-memory remains the default; persisted resource definitions materialize into the catalogs at startup
  - Integration tests against PostgreSQL 16 (CI service): restart survival, two-replica shared state, unique-name/version conflicts, CAS conflicts, cascade deletes, resource materialization

### Added — P1.4: Memory policy and persistence
- **Memory policy and persistence (ADR-003)**
  - `MemoryPolicyCatalog`; a referenced policy is authoritative for scope, limits, and retention, missing references fail fast, and `enabled: false` on a policy disables memory (writes raise, reads return nothing)
  - Scope IDs derived from invocation context: `user` -> caller, `agent` -> agent name, `tenant` -> `tenant_id` metadata, `application` -> deployment constant; entries never cross scope IDs
  - Per-scope `max_entries` eviction (oldest first) and `retention_days` purging enforced through `MemoryProvider.enforce()` after every write and before reads; `InMemoryProvider` implements it
  - `PostgresMemoryProvider` (ADR-003, `osa-adk-runtime[postgres]` extra): SQLAlchemy 2.0 async over asyncpg, dedicated `osa_memory_entries` table, `ILIKE` substring search with escaped metacharacters, SQL-enforced limits/retention; selected via externalized `OSA_MEMORY_DATABASE_URL`, connectivity and schema verified at startup, closed on shutdown
  - Extraction stays explicit (`remember()`); `auto_extract` is reserved — raw turns are never auto-persisted
  - Integration tests against PostgreSQL 16 (CI service container; local runs skip without `OSA_TEST_DATABASE_URL`): restart survival, scope isolation, case-insensitive search, LIKE escaping, eviction, retention

### Added — P1.3: MCP runtime client
- **MCP runtime client (ADR-002)**
  - Official `mcp` SDK (`>=1.24,<2`, matching google-adk's extra) as a core dependency of `osa-adk-runtime`
  - stdio and Streamable HTTP transports; legacy `sse` rejected with `mcp_transport_not_supported`
  - Lazy connections pooled per runtime (agents sharing a server share one connection), owned by a keeper task so anyio scopes enter/exit in one task; closed on runtime shutdown
  - Settings resolved from `McpDefinition`: `timeout_seconds`, `max_retries`/`retry_delay_seconds`, `tls_verify`, `max_response_bytes` (`mcp_response_too_large`), `credential_ref` via `SecretResolver` (bearer header for HTTP, subprocess env for stdio; values never stored or logged); new `env` field for non-secret stdio environment
  - Server-level and agent-level `tools_filter` intersect; tools namespaced `<server>_<tool>` with origin metadata; declarations generated from MCP `inputSchema`; arguments validated before dispatch
  - `OsaMcpToolset` bridges to ADK per invocation; `GenericAdkAgent` pre-flights MCP connections so a dead/unauthorized server fails deterministically (`mcp_connection_failed`, `mcp_tool_failed`) instead of ADK's fail-open tool loss
  - Protocol tests: deterministic stdio server (discovery, filters, invocation, tool errors, timeout, oversize, unreachable, credential) plus localhost Streamable HTTP (discovery/invocation, 401 auth failure); end-to-end agent acceptance through the ADK Runner

### Added — P0 release gate: runnable agent
- **Configuration bootstrap and resource resolution (P0.1)**
  - Versioned deployment-bundle format (`AgentBundle` document or directory layout) loading one `AgentDefinition` plus model, tool, skill, MCP, and memory-policy resources
  - Fail-fast bundle validation: unknown resource kinds, unsupported apiVersions, duplicate resource names, and unresolvable agent references raise deterministic `BundleError` subclasses
  - `SecretResolver` contract with `EnvironmentSecretResolver`; resolved values are never stored, logged, or surfaced in errors
  - `apiVersion`/`kind` enforcement on agent definitions; positive/range validation for timeouts, TTLs, limits, iterations, model settings, and MCP connection options
  - `OSA_MODEL_REF` now replaces bare-string model references (environment precedence), decided and tested
- **Real model execution through ADK (P0.2, ADR-001)**
  - LiteLLM production model adapter via ADK `LiteLlm` (`osa-adk-runtime[litellm]` optional extra); `fake` provider remains an explicit deterministic test adapter
  - `GenericAdkAgent.invoke()` routes through the ADK `Runner`; the transitional `TOOL_CALL` text protocol is removed in favor of ADK-native function calling
  - Tool declarations generated from `ToolDefinition.capabilities` with argument validation; tool timeouts enforced inside the ADK loop
  - Generation settings precedence: `ModelRuntimeSettings` (catalog) overridden by `ModelRef.parameters` (per agent)
  - `runtime.timeout_seconds` cancellation and `max_iterations` enforcement with stable error types (`InvocationTimeoutError`, `IterationLimitExceededError`, `ModelInvocationError`, `ModelConfigurationError`)
- **Session continuity and isolation (P0.3)**
  - `SessionProvider` contract with ownership by `(agent_name, user_id, tenant_id)`; TTL expiry, bounded history (`max_history_messages`), and explicit deletion
  - Caller-supplied unknown session IDs rejected (`session_not_found`); identity changes and cross-agent reuse rejected (`session_access_denied`)
  - `OsaAdkSessionService` backs ADK sessions with the OSA provider so bounded conversation history reaches the model
- **Runtime service lifecycle and image (P0.4)**
  - `osa-runtime` CLI and `create_runtime_app` factory: bundle bootstrap during FastAPI lifespan startup; invalid bundles abort startup before readiness
  - Graceful shutdown on SIGTERM; readiness reflects successful initialization
  - Production container image: non-root (UID 10001), arbitrary-UID friendly, health check, externally mounted bundle, no build tooling or source in the runtime stage
  - CI container job: build, ready → invoke → SIGTERM smoke test, `uv lock --check`
- **Control Plane correctness (P0.5)**
  - Stable error envelope `{"error": {"code", "message"}}` across Control Plane and runtime APIs
  - Create validation (template XOR definition, explicit draft placeholders, definition/request name agreement), activation-time resource-reference validation, lifecycle transitions (`draft -> active -> disabled/archived`, archived terminal) with invalid transitions rejected
  - Cumulative list filters with pagination (`limit`/`offset`) and sorting (`sort_by`/`order`); immutable version snapshots with duplicate-version conflicts; optimistic concurrency via `expected_version`
- **Versioning and dependency hygiene (P0.6)**
  - `[dependency-groups].dev` replaces deprecated `[tool.uv].dev-dependencies`
  - `google-adk>=2.0,<3.0` tested range; BaseAgentConfig deprecation filtered (upstream import noise, documented)
  - One lockstep release version across manifests, installed distributions, and API metadata, enforced by tests; release policy documented in CONTRIBUTING.md

### Changed
- Reworked README and project definition to separate implemented behavior from
  target architecture.
- Added current architecture, configuration, and HTTP API references; added
  ADR-001 (LiteLLM adapter) and the runnable smoke-bundle example.
- Rebuilt `TODO.md` as a dependency-ordered P0-P3 backlog with acceptance
  criteria and source-backed review findings.
- Corrected documentation that described the Control Panel, A2A, MCP runtime,
  persistent state, deployment APIs, and runnable containers as already
  available.

### Added (earlier, pre-gate)
- **ADK `LlmAgent` / `Runner` construction**
  - `osa.runtimes.adk.llm_agent`: `build_llm_agent`, `build_runner`, `build_function_tools`
  - `GenericAdkAgent.llm_agent` / `GenericAdkAgent.runner` expose the ADK objects
  - Runtime tools bridged as ADK `FunctionTool`s; agent names sanitized to ADK identifiers
- **Runtime memory integration**
  - Policy-controlled memory context injection before reasoning (`spec.memory.enabled` + provider)
  - Explicit `remember()` API; raw interactions are never auto-persisted
- **Milestone 13 — deployment providers (local)**
  - `DeploymentProvider` contract and `LocalDeploymentProvider` process lifecycle
- **Tool & skill runtime wiring (RV-009)**
  - Fail-fast resolution of `spec.tools` / `spec.skills`; `execute_tool()` with timeout enforcement

---

## [0.14.0] - 2026-08-30

### Added
- **Milestone 14 — Agent Runtime HTTP API**
  - FastAPI runtime API: `POST /v1/invoke`, `GET /health/live`, `GET /health/ready`, `GET /v1/capabilities`
  - `InvokeRequest` with input, session_id, user_id, metadata
  - `InvokeResponse` with output, invocation_id, session_id, error
  - `CapabilitiesResponse` with agent info, tools, skills
  - Agent runs independently of Control Plane

---

## [0.12.0] - 2026-08-30

### Added
- **Milestone 12 — Control Plane API**
  - FastAPI endpoints: `POST/GET/PATCH/DELETE /agents`
  - Agent versioning via `POST /agents/{id}/versions`
  - Agent disable via `POST /agents/{id}/disable`
  - Health endpoints: `/health/live`, `/health/ready`
  - Agent creation from template or definition
  - List with filtering by status, skill, runtime, search query

---

## [0.11.0] - 2026-08-30

### Added
- **Milestone 11 — Resource Catalogs**
  - `ResourceCatalogs` unified wrapper for Model, MCP, Tool, Skill, Memory Policy catalogs
  - Full CRUD operations for all resource types
  - Search capabilities for skills
  - Agent creation can select resources by catalog reference

---

## [0.10.0] - 2026-08-30

### Added
- **Milestone 10 — Agent Templates**
  - `AgentTemplate` with `create_definition()` producing independent `AgentDefinition`
  - `TemplateCatalog` with register/get/list
  - 3 built-in templates: generic, support, research
  - User overrides take precedence over template defaults
  - Definition independence verified (template change doesn't affect existing agents)

---

## [0.9.0] - 2026-08-30

### Added
- **Milestone 9 — Agent Catalog**
  - `AgentCatalog` with full CRUD: create, get, get_by_name, list_all, search, update, disable, archive, delete
  - `AgentVersion` for versioned snapshots of agent definitions
  - `AgentRecord` with all required fields (definition, versions, skills, runtime, endpoint, labels)
  - `AgentRecordStatus` enum (draft, active, disabled, archived)
  - Filter by status, skill, runtime
  - Catalog stores definitions, not runtime Agent objects

---

## [0.8.0] - 2026-08-30

### Added
- **Milestone 8 — ADK Runtime Vertical Slice**
  - `runtimes/adk` module
  - `GenericAdkAgent`, `AdkRuntime`, `AdkAgentFactory`
  - Configured model resolution
  - Session integration (creation, conversation tracking)
  - Generic invocation API mapping `AgentRequest` → model → `AgentResponse`
  - Runtime error capture
  - Shutdown lifecycle
  - Runtime works stand-alone from configuration (no Control Plane)

---

## [0.7.0] - 2026-08-30

### Added
- **Milestone 7 — Memory**
  - `MemoryProvider` ABC (load / store / delete / search)
  - `InMemoryProvider`
  - `MemoryPolicy` (scope, max entries, retention, auto extract)
  - `MemoryEntry`, `MemoryScope`
  - User/agent/tenant/application scopes
  - Retention, limits, enable/disable policies
  - Memory remains policy-controlled and independent from Session

---

## [0.6.0] - 2026-08-30

### Added
- **Milestone 6 — Sessions**
  - `SessionId`, `Session` (conversation history)
  - `SessionManager` (create / get / get_or_create / delete)
  - In-memory provider
  - Expiry configuration
  - Automatic session creation
  - Multiple independent sessions per agent
  - Session and Memory remain separate concepts

---

## [0.5.0] - 2026-08-30

### Added
- **Milestone 5 — Skills**
  - `SkillDefinition`, `SkillCatalog` (register / resolve / search by name, description, tags)
  - Agents reference skills via `SkillRef`
  - Metadata prepared for future A2A Agent Cards
  - Skills remain semantic capabilities, not executable plugins

---

## [0.4.0] - 2026-08-30

### Added
- **Milestone 4 — MCP**
  - `McpDefinition`, `McpCatalog`, `McpTransport`, `McpConnectionOptions`
  - Tool/resource/prompt capability metadata
  - Credentials via `SecretReference`
  - Timeout, retry, TLS validation, response limits, connection lifecycle options
  - MCP modeled separately from native Tools

---

## [0.3.0] - 2026-08-30

### Added
- **Milestone 3 — Tool Infrastructure**
  - `ToolDefinition`, `ToolCatalog`, `ToolCapability`, `ToolCategory`
  - `Tool` runtime interface with `execute()`
  - `ToolResult`, `ToolError` / `ToolTimeoutError`
  - `CalculatorTool` deterministic example
  - Registration and validation
  - Structured, observable failures

---

## [0.2.0] - 2026-08-30

### Added
- **Milestone 2 — Model Catalog**
  - `ModelDefinition`, `ModelCapabilities`, `ModelRuntimeSettings`
  - `ModelCatalog` (register / resolve / get_default / list)
  - `ModelProvider` ABC + deterministic `FakeModelProvider`
  - `ModelResponse` / `TokenUsage`
  - Credentials via `SecretReference` only
  - Integration test resolves `model.ref` through the catalog
  - CI requires no paid API

---

## [0.1.0] - 2026-08-29

### Added
- **Milestone 1 — Generic Agent Contracts**
  - `AgentId`, `AgentMetadata`, `AgentRequest`, `AgentResponse`
  - `AgentDefinition`, `AgentStatus`, `AgentCapabilities`
  - `Agent` protocol, `AbstractAgent`, `AgentRuntime`, `AgentFactory`
  - Strict typed configuration (`extra="forbid"`)
  - YAML loader (`load_agent_definition`)
  - `OSA_*` environment overrides
  - `SecretReference`, `ConfigurationError`
  - No ADK dependency in the generic domain module

---

## [0.0.1] - 2026-08-29

### Added
- **Milestone 0 — Repository and Project Foundation**
  - Repository structure
  - uv workspace with 3 members sharing one PEP 420 namespace package `osa`
  - ruff lint+format
  - mypy strict
  - pytest with `asyncio_mode=auto`
  - GitHub Actions CI (lint / typecheck / test)
  - Multi-stage non-root Dockerfile (base image)
  - Apache-2.0 LICENSE
  - CONTRIBUTING.md, README.md, PROJECT_DEFINITION.md
  - docs/adrs/

---

## Legend

- **Added**: New features
- **Changed**: Changes to existing functionality
- **Deprecated**: Soon-to-be removed features
- **Removed**: Removed features
- **Fixed**: Bug fixes
- **Security**: Vulnerability fixes
