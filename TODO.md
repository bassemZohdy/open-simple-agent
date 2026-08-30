# Open Simple Agent — TODO

This file contains the implementation backlog for Open Simple Agent.

Tasks should only be marked complete after implementation, automated tests, and relevant documentation are complete.

---

# Status Snapshot

- **Completed**: Milestones 0–12 and 14 (foundation, generic agent contracts, configuration, model/tool/MCP/skill/session/memory domains, ADK runtime with tool execution and ADK `LlmAgent`/`Runner` construction, agent catalog, templates, resource catalogs, control plane API, runtime HTTP API, local deployment provider)
- **In-memory implementations**: Agent Catalog, Templates, Resource Catalogs (PostgreSQL persistence pending)
- **Latest session (2026-08-30)**: ADK `LlmAgent` + `Runner` construction from definitions (`osa.runtimes.adk.llm_agent`), policy-controlled memory wiring in `GenericAdkAgent` (context injection + explicit `remember()`), Milestone 13 `DeploymentProvider` contract + `LocalDeploymentProvider`
- **Open**: routing live invocation through the ADK Runner (replaces the transitional `TOOL_CALL` protocol), MCP client manager + MCP tool resolution, Control Plane persistence, container/K8s deployment providers, A2A (M15), UI (M17), auth (M18), observability (M20)
- **Verification**: 222 tests passing, mypy strict (no `osa.*` exemptions), ruff clean, CI green on `main`

---

# Open Work Items

## Milestone 8 Leftovers — ADK Runtime

- [x] Build ADK `LlmAgent` from AgentDefinition (`osa.runtimes.adk.llm_agent`; name sanitized to an ADK identifier, tools bridged as `FunctionTool`s)
- [x] Resolve native tools (RV-009 — construction-time resolution + `TOOL_CALL` execution loop with timeout)
- [ ] Route invocation through the ADK Runner with a live model (replaces the transitional `TOOL_CALL` protocol; blocked on a real model provider)
- [ ] Resolve MCP tools
- [x] Resolve skills metadata (RV-009 — `SkillCatalog` resolution at construction)
- [x] Integrate Memory (policy-controlled context injection + explicit `remember()`; raw interactions never auto-persisted)
- [x] Configure ADK Runner (`Runner` with in-memory session + memory services, exposed as `agent.runner`)
- [ ] Add streaming capability if practical

---

## Milestone 7 Leftovers — Memory Runtime Wiring

- [x] Load relevant memory before agent reasoning (`_load_memory_context`; only when `spec.memory.enabled` + provider configured)
- [x] Avoid storing every raw interaction as permanent memory by default (nothing auto-persists; covered by test)
- [ ] Persist selected memory after interaction (only explicit `remember()` exists; policy-driven extraction pending)
- [ ] Select first persistent memory implementation (PostgreSQL / Redis evaluation)

---

## Milestone 9 Leftovers — Agent Catalog Persistence

- [ ] Create database migrations (PostgreSQL)
- [ ] Catalog survives Control Plane restart

---

## Milestone 13 — Deployment Providers

### Provider contract

- [x] Define `DeploymentProvider` (deploy / restart / stop / status / list_deployments)
- [x] Keep it separate from `AgentRuntime` (`osa.control_plane.backend.deployment`; owns process lifecycle only)

### Local provider

- [x] Implement local development provider (`LocalDeploymentProvider`)
- [x] Start/stop local agent runtime (subprocess per deployment; SIGTERM → SIGKILL grace)
- [x] Report status (running / stopped / failed with exit-code error; liveness via `poll()`)

### Container provider

- [ ] Evaluate Docker runtime provider

### Kubernetes/OpenShift
- [ ] Implement Kubernetes provider when required
- [ ] Generate Deployment/Service
- [ ] Configure readiness/liveness
- [ ] Configure ConfigMap/Secret references
- [ ] Handle scale/rolling update/rollback

**Acceptance**: Control Plane can manage agent lifecycle without understanding ADK internals (partially met — local provider demonstrates the contract; production providers pending)

---

## Milestone 14 Leftovers — Runtime API

- [ ] Add streaming endpoint if supported
- [ ] Multiple replicas behave consistently for external callers where session storage allows

---

## Milestone 15 — A2A

### Agent exposure
- [ ] Integrate supported A2A SDK
- [ ] Generate Agent Card from AgentDefinition
- [ ] Map Skills to Agent Card
- [ ] Expose A2A endpoint
- [ ] Support required security configuration
- [ ] Validate interoperability with external A2A client

### A2A client
- [ ] Support remote A2A agent registration
- [ ] Invoke remote A2A agents
- [ ] Handle A2A errors
- [ ] Handle authentication

**Acceptance**: Managed Agent A → A2A → Managed Agent B must work

---

## Milestone 16 — External Agents

### Catalog
- [ ] Define external-agent catalog entry
- [ ] Store Agent Card, endpoint, skills, capabilities, security metadata

### Registration
- [ ] Register by Agent Card URL
- [ ] Validate Agent Card
- [ ] Refresh metadata
- [ ] Health/status checks

**Acceptance**: External agent appears in catalog searches; platform cannot accidentally deploy external agent as managed

---

## Milestone 17 — Control Panel UI

### Foundation
- [ ] Create TypeScript/React application
- [ ] Implement authentication shell
- [ ] Create API client

### Agent screens
- [ ] Agent list, search/filter, details
- [ ] Create/Edit/Clone Agent
- [ ] Version history, deployment status, runtime health

### Agent creation
Provide selectors for: Template, Model, MCP Servers, Tools, Skills, Memory Policy, Session Policy, A2A

### Catalog screens
- [ ] Model Catalog, MCP Catalog, Tool Catalog, Skill Catalog, Memory Policies

### Testing console
- [ ] Invoke managed agent, view response, session support, streaming, A2A test invocation

**Acceptance**: Common platform operations require no manual API calls

---

## Milestone 18 — Authentication and Authorization

### Control Plane
- [ ] OIDC/OAuth authentication
- [ ] Administrator/read-only roles
- [ ] Agent/catalog/deployment management permissions

### Agent runtime
- [ ] Runtime authentication
- [ ] Agent/caller/user identity
- [ ] Authorization policies

### MCP
- [ ] Credential references, OAuth, API key, mTLS

### A2A
- [ ] Supported authentication schemes
- [ ] Authorization policy

**Acceptance**: No production management endpoint is unauthenticated; secrets don't appear in responses/logs

---

## Milestone 19 — Policy and Guardrails

### Runtime policy
- [ ] Tool/MCP allow/deny policy
- [ ] Model restrictions
- [ ] Skill exposure policy
- [ ] Memory policy enforcement
- [ ] External A2A policy

### ADK integration
- [ ] Evaluate ADK plugins for global runtime policies
- [ ] Add auditing/policy plugin

**Acceptance**: Policy is enforced independently from agent instructions

---

## Milestone 20 — Observability

### Metrics
- [ ] Invocation count/latency, model latency, token usage
- [ ] Tool/MCP/memory/session/A2A usage, errors

### Tracing
- [ ] OpenTelemetry integration
- [ ] Agent/model/tool/MCP/A2A spans

### Logs
- [ ] Structured logging with correlation IDs
- [ ] Session/agent/invocation ID
- [ ] Secret redaction

**Acceptance**: One agent invocation can be traced across model/tool/MCP operations

---

## Milestone 21 — Manager Agent

*Do not start until deterministic Control Plane APIs are stable*

### Manager Agent
- [ ] Create Manager Agent using ADK
- [ ] Expose controlled Control Plane tools (search_agents, get_agent, create_agent_draft, validate_agent, clone_agent, compare_versions, create_version, deploy_agent, restart_agent, scale_agent, rollback_agent, get_health, get_logs)

### Safety
- [ ] High-impact actions require explicit policy/approval
- [ ] Manager Agent cannot access raw secrets, bypass validation, modify database, or manipulate Kubernetes

**Acceptance**: "Create a new complaint-resolution agent based on support-agent" must produce validated draft before deployment

---

## Milestone 22 — Packaging and Images

### Generic Agent runtime
- [ ] Create production ADK runtime image
- [ ] Keep image minimal, run non-root, support arbitrary UID
- [ ] Externalize configuration/secrets
- [ ] Add health endpoints, graceful shutdown

### Control Plane
- [ ] Backend image
- [ ] UI image/static packaging
- [ ] Database migrations

**Acceptance**: Containers run locally and on Kubernetes/OpenShift; no package installation during runtime startup

---

## Milestone 23 — CI/CD

- [ ] Unit tests, integration tests, E2E tests
- [ ] Container build
- [ ] Security scanning, dependency scanning, SBOM
- [ ] Image signing if appropriate
- [ ] Version tagging, GitHub release automation
- [ ] Container registry publishing

---

## Milestone 24 — Documentation and Examples

### Documentation
- [ ] Getting started, configuration reference
- [ ] AgentDefinition reference
- [ ] Model/MCP/Tools/Skills/Memory/Sessions guide
- [ ] A2A guide, Control Plane API guide
- [ ] Deployment guide, security guide

### Examples
- [ ] Minimal agent, tool-using agent, MCP agent
- [ ] Memory agent, A2A agent, support agent
- [ ] External A2A registration, Manager Agent demonstration

---

# Deferred Backlog

*Do not implement unless required*

- [ ] Multiple unrelated agents inside one runtime process
- [ ] Additional runtime frameworks (LangChain, etc.)
- [ ] Remote runtime providers
- [ ] Advanced multi-tenancy
- [ ] Enterprise policy engine
- [ ] Hosted agent marketplace
- [ ] Advanced semantic agent discovery
- [ ] Dynamic runtime plugin installation
- [ ] Agent-to-agent delegation/consent framework
- [ ] Human approval framework beyond basic management approvals
- [ ] Advanced memory extraction and consolidation
- [ ] Multi-region agent deployment

---

# Implementation Order (Current)

```text
 1. Repository skeleton            ✅ done
 2. Generic Agent contracts        ✅ done
 3. Configuration                  ✅ done
 4. Model Catalog                  ✅ done
 5. Native Tool support            ✅ done (runtime wiring resolved — RV-009)
 6. MCP                            ✅ done (domain; runtime client open)
 7. Skills                         ✅ done (runtime metadata resolution done; A2A mapping open)
 8. Session                        ✅ done (domain; persistence open)
 9. Memory                         ✅ done (domain; persistence + wiring open)
10. ADK runtime vertical slice     ✅ done (tools/skills/memory wired; LlmAgent built; live-model routing + streaming open)
11. Agent Catalog                  ✅ done (in-memory; persistence open)
12. Control Plane API              ✅ done
13. Control Panel                  ⬜ not started
14. A2A                            ⬜ not started
15. Deployment lifecycle           ◑ local provider done; container/K8s open (Milestone 13)
16. Manager Agent                  ⬜ not started
```

**Next priority**: route invocation through the ADK Runner with a live model → Control Plane persistence (PostgreSQL) → MCP client manager → container deployment provider

---

# Review Findings

## Resolved Findings

- **RV-001**: `osa` namespace package issue — resolved (PEP 420 namespace)
- **RV-002**: `.gitignore` missing Python artifacts — resolved
- **RV-003**: Dockerfile missing ENTRYPOINT — resolved (documented base image)
- **RV-004**: mypy exempting `osa.*` code — resolved (strict checking enabled)
- **RV-005**: Milestones checked without backing — resolved (CI green, tests added)
- **RV-006**: Documented YAML not loading — resolved (bare string coercion)
- **RV-007**: Env override crashes — resolved (robust error handling)
- **RV-008**: CI `uv sync` not installing dependencies — resolved (`--all-packages`)
- **RV-009**: Tool/skill runtime wiring — resolved 2026-08-30:
  - `GenericAdkAgent` now resolves `spec.tools` and `spec.skills` against
    `ToolCatalog`/`SkillCatalog` at construction and fails fast with a clear
    `ValueError` when a referenced resource is missing.
  - `invoke()` executes a tool-calling loop: a model response of
    `TOOL_CALL <name> {json}` triggers `execute_tool()` and the `ToolResult`
    is fed back to the model until a final answer (transitional protocol until
    ADK `LlmAgent` function-calling lands; bounded by `runtime.max_iterations`,
    default 3).
  - `execute_tool()` enforces `ToolDefinition.timeout_seconds` via
    `asyncio.wait_for` and raises `ToolTimeoutError`; timeouts surface in
    `AgentResponse.error` on the invoke path.
  - `AdkRuntime`/`AdkAgentFactory` accept and pass through `skill_catalog`;
    resolved tools/skills are exposed via `agent.tools` / `agent.skills`.
  - Verified: real `ToolCatalog` lookup/`execute` call sites exist in
    `runtimes/adk/src` (previously only the constructor assignment); a
    `CalculatorTool`-equipped agent produces a response derived from the tool's
    `ToolResult`; a deliberately slow tool raises `ToolTimeoutError` past its
    configured timeout; 9 new runtime tests (207 total passing).
