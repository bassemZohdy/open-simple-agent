# Open Simple Agent — TODO

This file contains the implementation backlog for Open Simple Agent.

Tasks should only be marked complete after implementation, automated tests, and relevant documentation are complete.

---

# Milestone 0 — Repository and Project Foundation

## Project structure

- [ ] Create repository structure:

```text
control-plane/
  backend/
  ui/

generic-agent/

runtimes/
  adk/

docs/
tests/
```

- [ ] Add Python workspace/project configuration.
- [ ] Define dependency-management approach.
- [ ] Add linting.
- [ ] Add formatting.
- [ ] Add static typing.
- [ ] Add unit-test framework.
- [ ] Add base CI workflow.
- [ ] Add Docker build strategy.
- [ ] Add license.
- [ ] Add contribution guidelines.
- [ ] Add `README.md`.
- [ ] Add `PROJECT_DEFINITION.md`.
- [ ] Add architecture decision record directory.

## Acceptance

- [ ] Repository builds successfully.
- [ ] CI runs automatically.
- [ ] Empty module skeletons import correctly.
- [ ] Formatting/lint/type checks pass.

---

# Milestone 1 — Generic Agent Contracts

## Agent domain

- [ ] Define `AgentId`.
- [ ] Define `AgentMetadata`.
- [ ] Define `AgentRequest`.
- [ ] Define `AgentResponse`.
- [ ] Define `AgentDefinition`.
- [ ] Define `AgentStatus`.
- [ ] Define `AgentCapabilities`.

## Runtime contracts

- [ ] Define `Agent` interface/protocol.
- [ ] Define `AbstractAgent`.
- [ ] Define `AgentRuntime`.
- [ ] Define `AgentFactory`.

## Configuration

- [ ] Define strict typed configuration models.
- [ ] Define YAML loader.
- [ ] Define environment-variable overrides.
- [ ] Reject unknown configuration properties.
- [ ] Define configuration validation errors.
- [ ] Define secret-reference type.

## Acceptance

- [ ] Minimal AgentDefinition loads successfully.
- [ ] Invalid definitions fail clearly.
- [ ] Unknown properties fail validation.
- [ ] No ADK dependency exists inside the generic domain contracts unless explicitly justified.

---

# Milestone 2 — Model Catalog

## Model domain

- [ ] Define `ModelDefinition`.
- [ ] Define `ModelCapabilities`.
- [ ] Define `ModelCatalog`.
- [ ] Define model credential references.
- [ ] Define model runtime settings.
- [ ] Define default model handling.

## Providers

- [ ] Implement initial model-provider integration required by ADK.
- [ ] Evaluate LiteLLM as generic model access layer.
- [ ] Pin supported dependency versions.
- [ ] Support environment/secret-based credentials.
- [ ] Add deterministic fake model for tests.

## Acceptance

- [ ] Agent can reference:

```yaml
model:
  ref: default
```

- [ ] Model is resolved from the catalog.
- [ ] Credentials do not exist directly in ordinary agent definitions.
- [ ] CI does not require a paid model API.

---

# Milestone 3 — Tool Infrastructure

## Tool domain

- [ ] Define `ToolDefinition`.
- [ ] Define `ToolCatalog`.
- [ ] Define tool capability metadata.
- [ ] Define runtime tool interface.

## Native tools

- [ ] Implement deterministic example/test tool.
- [ ] Implement tool registration mechanism.
- [ ] Add tool validation.
- [ ] Add tool execution error model.
- [ ] Add tool timeout handling.

## Acceptance

- [ ] Agent definition can reference native tools.
- [ ] Runtime resolves tools dynamically.
- [ ] Tool failures are observable and structured.

---

# Milestone 4 — MCP

## MCP domain

- [ ] Define `McpDefinition`.
- [ ] Define `McpCatalog`.
- [ ] Define MCP authentication references.
- [ ] Define MCP connection options.
- [ ] Define supported transports.

## MCP runtime

- [ ] Implement MCP client manager.
- [ ] Connect configured MCP servers.
- [ ] Discover MCP tools.
- [ ] Preserve MCP resources metadata.
- [ ] Preserve MCP prompts metadata.
- [ ] Register MCP tools with runtime agents.
- [ ] Add MCP timeout handling.
- [ ] Add MCP retry policy where appropriate.
- [ ] Add TLS validation.
- [ ] Add maximum response limits.
- [ ] Add safe connection lifecycle management.

## Acceptance

An agent such as:

```yaml
mcps:
  - ref: crm
```

must successfully resolve the configured MCP and expose its allowed capabilities to the runtime agent.

- [ ] No raw credentials appear in logs.
- [ ] MCP is represented separately from native Tools.

---

# Milestone 5 — Skills

## Skill domain

- [ ] Define `SkillDefinition`.
- [ ] Define `SkillCatalog`.
- [ ] Define skill ID.
- [ ] Define name.
- [ ] Define description.
- [ ] Define input/output metadata where useful.
- [ ] Define tags/categories.
- [ ] Define optional policy metadata.

## Agent integration

- [ ] Allow agents to reference skills.
- [ ] Include skills in catalog metadata.
- [ ] Prepare skill metadata for future A2A Agent Cards.

## Acceptance

- [ ] Skills are discoverable through the Agent Catalog.
- [ ] Skills remain semantic capabilities rather than executable plugin implementations.

---

# Milestone 6 — Sessions

## Session domain

- [ ] Define `SessionId`.
- [ ] Define `Session`.
- [ ] Define `SessionManager`.
- [ ] Define session metadata.
- [ ] Define session lifecycle.

## Storage

- [ ] Implement in-memory session provider.
- [ ] Define persistent session provider contract.
- [ ] Decide first persistent implementation.
- [ ] Add session expiry configuration.

## Runtime integration

- [ ] Pass session context into agent invocation.
- [ ] Preserve conversation context where configured.
- [ ] Support creation of session when none is supplied.

## Acceptance

- [ ] Multiple independent sessions can use the same agent.
- [ ] Session data does not become long-term Memory automatically.

---

# Milestone 7 — Memory

## Memory domain

- [ ] Define `MemoryProvider`.
- [ ] Define `MemoryPolicy`.
- [ ] Define `MemoryScope`.
- [ ] Define `MemoryEntry`.
- [ ] Define memory lookup contract.
- [ ] Define memory update contract.
- [ ] Define deletion contract.

## Providers

- [ ] Implement in-memory provider.
- [ ] Select first persistent memory implementation.
- [ ] Evaluate PostgreSQL-based provider.
- [ ] Evaluate Redis only where semantics justify it.

## Memory policies

- [ ] User-scoped memory.
- [ ] Agent-scoped memory.
- [ ] Tenant/application scopes where required.
- [ ] Retention policy.
- [ ] Maximum memory limits.
- [ ] Enable/disable policy.

## Runtime integration

- [ ] Load relevant memory before agent reasoning.
- [ ] Provide memory to ADK runtime.
- [ ] Persist selected memory after interaction.
- [ ] Avoid storing every raw interaction as permanent memory by default.

## Acceptance

- [ ] Memory survives sessions when persistent provider is enabled.
- [ ] Session and Memory remain independent concepts.
- [ ] Memory behavior is policy-controlled.

---

# Milestone 8 — ADK Runtime Vertical Slice

## Runtime implementation

- [ ] Create `runtimes/adk`.
- [ ] Implement `GenericAdkAgent`.
- [ ] Implement `AdkRuntime`.
- [ ] Implement ADK AgentFactory.
- [ ] Build ADK `LlmAgent` from AgentDefinition.
- [ ] Resolve configured model.
- [ ] Resolve native tools.
- [ ] Resolve MCP tools.
- [ ] Resolve skills metadata.
- [ ] Integrate Session.
- [ ] Integrate Memory.
- [ ] Configure ADK Runner.
- [ ] Implement shutdown lifecycle.

## Invocation

- [ ] Implement generic invocation API.
- [ ] Map `AgentRequest` to ADK.
- [ ] Map ADK response to `AgentResponse`.
- [ ] Handle runtime errors.
- [ ] Add streaming capability if practical.

## Acceptance

The following must work:

```text
AgentDefinition
     ↓
ADK Runtime
     ↓
Fake Model
     ↓
Tool/MCP invocation
     ↓
Response
```

- [ ] Runtime works without the Control Plane.
- [ ] Generic agent can be started directly from configuration.

---

# Milestone 9 — Agent Catalog

## Persistence

- [ ] Select Control Plane database.
- [ ] Initial preference: PostgreSQL.
- [ ] Create database migrations.

## Agent records

- [ ] Persist AgentDefinition.
- [ ] Persist AgentMetadata.
- [ ] Persist AgentVersion.
- [ ] Persist status.
- [ ] Persist runtime metadata.
- [ ] Persist endpoint metadata.
- [ ] Persist skills.
- [ ] Persist deployment references.

## Catalog operations

- [ ] Create agent.
- [ ] Read agent.
- [ ] List agents.
- [ ] Search agents.
- [ ] Update agent.
- [ ] Disable agent.
- [ ] Delete/archive agent.
- [ ] Filter by skill.
- [ ] Filter by status.
- [ ] Filter by runtime.

## Acceptance

- [ ] Catalog survives Control Plane restart.
- [ ] Catalog does not store in-memory runtime Agent objects.

---

# Milestone 10 — Agent Templates

## Templates

- [ ] Define `AgentTemplate`.
- [ ] Persist templates.
- [ ] Create generic template.
- [ ] Create example support template.
- [ ] Create example research template.

## Creation

- [ ] Create AgentDefinition from template.
- [ ] Apply default values.
- [ ] Allow user overrides.
- [ ] Resolve final definition.
- [ ] Persist final AgentDefinition independently.

## Acceptance

- [ ] Updating a template does not silently modify existing agents.

---

# Milestone 11 — Resource Catalogs in Control Plane

## Model Catalog API

- [ ] CRUD model definitions.
- [ ] Validate model configuration.
- [ ] Test model connectivity safely.

## MCP Catalog API

- [ ] CRUD MCP definitions.
- [ ] Validate MCP configuration.
- [ ] Discover MCP capabilities.
- [ ] Test connectivity.

## Tool Catalog API

- [ ] List registered tools.
- [ ] Expose tool metadata.

## Skill Catalog API

- [ ] CRUD skill definitions.
- [ ] Search skills.

## Memory Policies

- [ ] CRUD Memory Policies.
- [ ] Validate policies.

## Acceptance

- [ ] Agent creation can select resources entirely by catalog reference.

---

# Milestone 12 — Control Plane API

## Agent management API

- [ ] `POST /agents`
- [ ] `GET /agents`
- [ ] `GET /agents/{id}`
- [ ] `PUT/PATCH /agents/{id}`
- [ ] Agent versioning.
- [ ] Agent validation.
- [ ] Agent cloning.

## Deployment APIs

- [ ] Create deployment.
- [ ] Read deployment.
- [ ] Start.
- [ ] Stop.
- [ ] Restart.
- [ ] Scale.
- [ ] Update.
- [ ] Rollback.
- [ ] Delete.

## Runtime APIs

- [ ] Get runtime status.
- [ ] Get health.
- [ ] Get capabilities.
- [ ] Access logs through appropriate observability integration.

## Acceptance

- [ ] Full managed-agent lifecycle can be performed through APIs without the UI.

---

# Milestone 13 — Deployment Providers

## Provider contract

- [ ] Define `DeploymentProvider`.
- [ ] Keep it separate from `AgentRuntime`.

## Local provider

- [ ] Implement local development provider.
- [ ] Start/stop local agent runtime.
- [ ] Report status.

## Container provider

- [ ] Evaluate Docker runtime provider.

## Kubernetes/OpenShift

- [ ] Implement Kubernetes provider when required.
- [ ] Generate Deployment.
- [ ] Generate Service.
- [ ] Configure readiness/liveness.
- [ ] Configure ConfigMap/Secret references.
- [ ] Configure resource requests/limits.
- [ ] Handle scale.
- [ ] Handle rolling update.
- [ ] Handle rollback.

## Acceptance

- [ ] Control Plane can manage agent lifecycle without understanding ADK internals.

---

# Milestone 14 — Agent Runtime HTTP API

## Endpoints

- [ ] `POST /v1/invoke`
- [ ] `GET /health/live`
- [ ] `GET /health/ready`
- [ ] `GET /v1/capabilities`
- [ ] Add streaming endpoint if supported.

## Request model

Include support for:

```text
agent identity
user/caller correlation
session ID
input
metadata
```

## Acceptance

- [ ] Agent can run independently of the Control Plane.
- [ ] Multiple replicas behave consistently for external callers where session storage allows.

---

# Milestone 15 — A2A

## Agent exposure

- [ ] Integrate supported A2A SDK.
- [ ] Generate Agent Card from AgentDefinition.
- [ ] Map Skills to Agent Card.
- [ ] Expose A2A endpoint.
- [ ] Support required security configuration.
- [ ] Validate interoperability with external A2A client.

## A2A client

- [ ] Support remote A2A agent registration.
- [ ] Invoke remote A2A agents.
- [ ] Handle A2A errors.
- [ ] Handle authentication.

## Acceptance

```text
Managed Agent A
      ↓
      A2A
      ↓
Managed Agent B
```

must work.

Also validate:

```text
Managed Agent
      ↓
      A2A
      ↓
External compatible agent
```

---

# Milestone 16 — External Agents

## Catalog

- [ ] Define external-agent catalog entry.
- [ ] Store Agent Card.
- [ ] Store endpoint.
- [ ] Store skills.
- [ ] Store capabilities.
- [ ] Store security metadata.

## Registration

- [ ] Register by Agent Card URL.
- [ ] Validate Agent Card.
- [ ] Refresh metadata.
- [ ] Health/status checks.

## Acceptance

- [ ] External agent appears in catalog searches.
- [ ] Platform cannot accidentally deploy/configure external agent as a managed agent.
- [ ] Managed agents can discover/invoke external agents where authorized.

---

# Milestone 17 — Control Panel UI

## Foundation

- [ ] Create TypeScript/React application.
- [ ] Implement authentication shell.
- [ ] Create API client.

## Agent screens

- [ ] Agent list.
- [ ] Search/filter.
- [ ] Agent details.
- [ ] Create Agent.
- [ ] Edit Agent.
- [ ] Clone Agent.
- [ ] Version history.
- [ ] Deployment status.
- [ ] Runtime health.

## Agent creation

Provide selectors for:

```text
Template
Model
MCP Servers
Tools
Skills
Memory Policy
Session Policy
A2A
```

## Catalog screens

- [ ] Model Catalog.
- [ ] MCP Catalog.
- [ ] Tool Catalog.
- [ ] Skill Catalog.
- [ ] Memory Policies.

## Testing console

- [ ] Invoke managed agent.
- [ ] View response.
- [ ] Session support.
- [ ] Streaming if supported.
- [ ] A2A test invocation.

## Acceptance

- [ ] Common platform operations require no manual API calls.

---

# Milestone 18 — Authentication and Authorization

## Control Plane

- [ ] OIDC/OAuth authentication.
- [ ] Administrator roles.
- [ ] Read-only roles.
- [ ] Agent management permissions.
- [ ] Catalog management permissions.
- [ ] Deployment permissions.

## Agent runtime

- [ ] Runtime authentication.
- [ ] Agent identity.
- [ ] Caller identity.
- [ ] User identity/correlation.
- [ ] Authorization policies.

## MCP

- [ ] Credential references.
- [ ] OAuth where required.
- [ ] API key support where required.
- [ ] mTLS where required.

## A2A

- [ ] Supported authentication schemes.
- [ ] Authorization policy.

## Acceptance

- [ ] No production management endpoint is unauthenticated.
- [ ] Secrets do not appear in agent configuration responses or logs.

---

# Milestone 19 — Policy and Guardrails

## Runtime policy

- [ ] Tool allow/deny policy.
- [ ] MCP allow/deny policy.
- [ ] Model restrictions.
- [ ] Skill exposure policy.
- [ ] Memory policy enforcement.
- [ ] External A2A policy.

## ADK integration

- [ ] Evaluate ADK plugins for global runtime policies.
- [ ] Add auditing plugin.
- [ ] Add policy plugin where useful.

## Acceptance

- [ ] Policy is enforced independently from agent instructions.

---

# Milestone 20 — Observability

## Metrics

- [ ] Invocation count.
- [ ] Invocation latency.
- [ ] Model latency.
- [ ] Token usage.
- [ ] Tool usage.
- [ ] MCP calls.
- [ ] Memory operations.
- [ ] Session count.
- [ ] A2A calls.
- [ ] Errors.

## Tracing

- [ ] OpenTelemetry.
- [ ] Agent invocation traces.
- [ ] Model spans.
- [ ] Tool spans.
- [ ] MCP spans.
- [ ] A2A spans.

## Logs

- [ ] Structured logging.
- [ ] Correlation IDs.
- [ ] Session ID.
- [ ] Agent ID.
- [ ] Invocation ID.
- [ ] Secret redaction.

## Acceptance

- [ ] One agent invocation can be traced across model/tool/MCP operations.

---

# Milestone 21 — Manager Agent

Do not start until the deterministic Control Plane APIs are stable.

## Manager Agent

- [ ] Create Manager Agent using ADK.
- [ ] Expose controlled Control Plane tools.

Tools may include:

```text
search_agents
get_agent
create_agent_draft
validate_agent
clone_agent
compare_versions
create_version
deploy_agent
restart_agent
scale_agent
rollback_agent
get_health
get_logs
```

## Safety

- [ ] High-impact actions require explicit policy/approval.
- [ ] Manager Agent cannot access raw secrets.
- [ ] Manager Agent cannot bypass Control Plane validation.
- [ ] Manager Agent cannot modify database directly.
- [ ] Manager Agent cannot directly manipulate Kubernetes.

## Acceptance

Example:

```text
Create a new complaint-resolution agent based on support-agent,
use the fast model, give it read-only CRM access,
and enable user memory.
```

must produce a validated draft before deployment.

---

# Milestone 22 — Packaging and Images

## Generic Agent runtime

- [ ] Create production ADK runtime image.
- [ ] Keep image minimal.
- [ ] Run non-root.
- [ ] Support arbitrary UID.
- [ ] Externalize configuration.
- [ ] Externalize secrets.
- [ ] Add health endpoints.
- [ ] Graceful shutdown.

## Control Plane

- [ ] Backend image.
- [ ] UI image/static packaging.
- [ ] Database migrations.

## Acceptance

- [ ] Containers run locally.
- [ ] Containers run on Kubernetes/OpenShift-compatible environments.
- [ ] No package installation occurs during runtime startup.

---

# Milestone 23 — CI/CD

- [ ] Unit tests.
- [ ] Integration tests.
- [ ] E2E tests.
- [ ] Container build.
- [ ] Security scanning.
- [ ] Dependency scanning.
- [ ] SBOM.
- [ ] Image signing if appropriate.
- [ ] Version tagging.
- [ ] GitHub release automation.
- [ ] Container registry publishing.

---

# Milestone 24 — Documentation and Examples

## Documentation

- [ ] Getting started.
- [ ] Configuration reference.
- [ ] AgentDefinition reference.
- [ ] Model Catalog guide.
- [ ] MCP guide.
- [ ] Tools guide.
- [ ] Skills guide.
- [ ] Memory guide.
- [ ] Sessions guide.
- [ ] A2A guide.
- [ ] Control Plane API guide.
- [ ] Deployment guide.
- [ ] Security guide.

## Examples

- [ ] Minimal agent.
- [ ] Tool-using agent.
- [ ] MCP agent.
- [ ] Memory agent.
- [ ] A2A agent.
- [ ] Support agent.
- [ ] External A2A registration.
- [ ] Manager Agent demonstration.

---

# Deferred Backlog

Do not implement unless required.

- [ ] Multiple unrelated agents inside one runtime process.
- [ ] Additional runtime frameworks.
- [ ] LangChain runtime.
- [ ] Remote runtime providers.
- [ ] Advanced multi-tenancy.
- [ ] Enterprise policy engine.
- [ ] Hosted agent marketplace.
- [ ] Advanced semantic agent discovery.
- [ ] Dynamic runtime plugin installation.
- [ ] Agent-to-agent delegation/consent framework.
- [ ] Human approval framework beyond basic management approvals.
- [ ] Advanced memory extraction and consolidation.
- [ ] Multi-region agent deployment.

---

# Immediate Implementation Order

Start with:

```text
1. Repository skeleton
2. Generic Agent contracts
3. Configuration
4. Model Catalog
5. Native Tool support
6. MCP
7. Skills
8. Session
9. Memory
10. ADK runtime vertical slice
11. Agent Catalog
12. Control Plane API
13. Control Panel
14. A2A
15. Deployment lifecycle
16. Manager Agent
```

Do not start the Manager Agent, external-agent lifecycle, or advanced deployment features before the basic configuration-driven ADK agent runtime is working end-to-end.

---

# First End-to-End Target

The first meaningful vertical slice should demonstrate:

```text
agent.yaml
    │
    ▼
Configuration Loader
    │
    ▼
AgentDefinition
    │
    ├── Model
    ├── Native Tool
    ├── MCP
    ├── Skill
    ├── Session
    └── Memory
    │
    ▼
ADK Runtime
    │
    ▼
GenericAdkAgent
    │
    ▼
POST /v1/invoke
    │
    ▼
Response
```

This vertical slice should work before significant Control Panel development begins.