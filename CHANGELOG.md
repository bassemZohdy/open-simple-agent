# Changelog

All notable changes to Open Simple Agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- **ADK `LlmAgent` / `Runner` construction**
  - `osa.runtimes.adk.llm_agent`: `build_llm_agent`, `build_runner`, `build_function_tools`
  - `GenericAdkAgent.llm_agent` / `GenericAdkAgent.runner` expose the ADK objects for live-model routing
  - Runtime tools bridged as ADK `FunctionTool`s; agent names sanitized to ADK identifiers
- **Runtime memory integration**
  - Policy-controlled memory context injection before reasoning (`spec.memory.enabled` + provider)
  - Explicit `remember()` API; raw interactions are never auto-persisted
  - `AdkRuntime` / `AdkAgentFactory` accept `memory_provider`
- **Milestone 13 — deployment providers (local)**
  - `DeploymentProvider` contract (deploy / restart / stop / status / list) in the Control Plane backend
  - `LocalDeploymentProvider` running each deployment as a local OS process with liveness-based status
- **Tool & skill runtime wiring (RV-009)**
  - `GenericAdkAgent` resolves `spec.tools` / `spec.skills` against their catalogs at construction and fails fast on missing references
  - `execute_tool()` with `ToolDefinition.timeout_seconds` enforcement (`ToolTimeoutError`)
  - Transitional `TOOL_CALL <name> {json}` model protocol in `invoke()`; `ToolResult` fed back to the model until a final answer (to be replaced by ADK `LlmAgent` function-calling)
  - `AdkRuntime` / `AdkAgentFactory` accept `skill_catalog`; resolved tools/skills exposed via `agent.tools` / `agent.skills`

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
