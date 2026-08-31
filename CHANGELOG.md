# Changelog

All notable changes to Open Simple Agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

> **Versioning note:** entries `0.1.0` through `0.14.0` record internal
> development milestones; they are not published package releases. All package
> manifests now share one lockstep release version, enforced by
> `tests/unit/test_versioning.py`. Automated release publishing remains
> tracked in `TODO.md` (P3.3).

## [Unreleased]

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
