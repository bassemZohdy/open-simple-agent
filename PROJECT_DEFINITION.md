# Open Simple Agent

## Project Definition

Working repository name:

```text
open-simple-agent
```

Working product name:

```text
Open Simple Agent
```

Abbreviation:

```text
OSA
```

---

# 1. Product Definition

Open Simple Agent is a lightweight, configuration-driven platform for defining, running, managing, and discovering autonomous AI agents.

The platform focuses on agents rather than workflows.

An agent combines:

```text
Instructions
+
Model
+
Tools
+
MCP Servers
+
Skills
+
Memory
+
Session
```

and executes directly using an agent runtime.

Initial runtime:

```text
Google ADK
```

---

# 2. Primary Product Goal

A user should be able to create a useful agent without writing application code.

For example:

```yaml
agent:
  name: customer-support
  instruction: |
    Help customers resolve support requests.

model:
  ref: default

mcps:
  - ref: crm

memory:
  enabled: true
```

This configuration should be sufficient for the platform to create and expose a functioning runtime agent.

---

# 3. Architectural Philosophy

The project prioritizes:

```text
simplicity
configuration over code
runtime practicality
small conceptual surface
centralized management
independent agent execution
standard protocols
operational readiness
```

The project intentionally does not attempt to make every underlying agent framework interchangeable.

---

# 4. Major Modules

The repository contains three primary modules.

```text
Open Simple Agent
│
├── Control Plane
│
├── Generic Agent
│
└── Runtimes
    └── ADK
```

---

# 5. Control Plane

The Control Plane is responsible for management.

It owns:

```text
Agent Catalog
Agent Definitions
Agent Templates
Agent Versions

Model Catalog
MCP Catalog
Tool Catalog
Skill Catalog
Memory Policies

Deployment metadata
Runtime status
Health
Configuration
Administration
Observability integration
```

The Control Plane does not execute normal agent requests.

---

# 6. Generic Agent

The Generic Agent module contains the stable Open Simple Agent domain model.

It defines:

```text
Agent
AbstractAgent
AgentDefinition
AgentMetadata
AgentRequest
AgentResponse

AgentRuntime
AgentFactory

ModelDefinition
ToolDefinition
McpDefinition
SkillDefinition

MemoryConfig
MemoryPolicy
SessionConfig

A2A metadata
Agent Card metadata
```

The Generic Agent module must not contain business-specific agents.

---

# 7. Runtime

The runtime converts an `AgentDefinition` into an executable agent.

Initial runtime:

```text
runtimes/adk
```

The runtime owns framework-specific behavior.

For ADK this includes:

```text
LlmAgent construction
Runner configuration
Model binding
Tool registration
MCP integration
Memory integration
Session integration
Callbacks/plugins
A2A exposure
Framework lifecycle
```

---

# 8. Runtime Strategy

Open Simple Agent does not initially attempt to support multiple equivalent agent frameworks.

Therefore:

```text
ADK is the implementation.
```

There is no requirement for:

```text
ADK result == LangChain result
```

There is no cross-framework compatibility test requirement.

Additional runtime implementations should only be introduced when there is an actual product requirement.

---

# 9. Runtime Boundary

A lightweight runtime contract may exist for internal architectural separation.

Conceptually:

```python
class AgentRuntime:
    async def create(definition: AgentDefinition) -> Agent: ...
```

This interface exists to separate:

```text
Open Simple Agent domain
```

from:

```text
ADK framework APIs
```

It must not evolve into an unnecessary portability framework.

---

# 10. Agent Contract

Conceptually:

```python
class Agent:
    async def invoke(self, request: AgentRequest) -> AgentResponse: ...
```

A common base implementation may provide:

```text
metadata
configuration
session handling
memory context
runtime diagnostics
capability reporting
```

Conceptually:

```text
Agent
  ▲
AbstractAgent
  ▲
GenericAdkAgent
```

---

# 11. No Business-Specific Agent Classes

Do not create classes such as:

```text
SupportAgent
PaymentAgent
BillingAgent
RenewalAgent
```

for normal configured agents.

Instead:

```text
AgentDefinition
       ↓
AgentFactory
       ↓
GenericAdkAgent
```

Business behavior comes from configuration.

---

# 12. Agent Definition vs Agent Instance

These are different concepts.

## AgentDefinition

Persistent configuration.

Contains:

```text
Identity
Instruction
Model reference
MCP references
Tool references
Skill references
Memory configuration
Session configuration
A2A configuration
Runtime configuration
```

## Agent

Running runtime object.

Created from an `AgentDefinition`.

Therefore:

```text
Control Plane
      ↓
AgentDefinition
      ↓
Catalog
      ↓
Runtime
      ↓
Agent Instance
```

The Control Plane must not use in-memory Agent objects as its persistent catalog representation.

---

# 13. Agent Catalog

The Agent Catalog stores persistent agent metadata.

Conceptually:

```text
AgentRecord
├── id
├── name
├── description
├── version
├── status
├── definition
├── skills
├── runtime
├── endpoint
├── agent card
└── deployment information
```

The catalog is a logical source of truth for managed agents.

---

# 14. Agent Templates

Templates accelerate agent creation.

Example templates:

```text
generic
support
research
developer
knowledge-assistant
classifier
```

Creation behavior:

```text
Template
    ↓
Apply defaults
    ↓
User customization
    ↓
Resolved AgentDefinition
```

The resulting definition should normally be self-contained.

Changing the original template must not unexpectedly change an existing production agent.

---

# 15. Model Catalog

Models are reusable managed definitions.

Agent configuration should preferably use:

```yaml
model:
  ref: reasoning-default
```

rather than repeating model connection configuration.

Model definitions may contain:

```text
Provider
Model identifier
Endpoint
Credential reference
Timeout
Generation settings
Capabilities
```

Secrets must not be stored directly in ordinary agent definitions.

---

# 16. MCP Is a First-Class Concept

MCP must not be modeled only as a generic tool.

An MCP server may expose:

```text
Tools
Resources
Prompts
```

Therefore the Control Plane contains an:

```text
MCP Catalog
```

Conceptually:

```text
McpDefinition
├── id
├── name
├── transport
├── endpoint
├── authentication reference
├── capabilities
└── runtime options
```

Agents reference MCP definitions.

```yaml
mcps:
  - ref: crm
  - ref: payments
```

---

# 17. Tools

Tools represent executable capabilities.

Tool sources may include:

```text
Native agent runtime tools
Application integrations
OpenAPI
MCP-provided tools
Future extensions
```

MCP-provided tools are exposed to the agent as tools while preserving their MCP source metadata.

---

# 18. Skills

Skills describe what an agent can accomplish.

Example:

```text
check-renewal-status
submit-renewal
resolve-support-case
```

Skills are intended for:

```text
Agent discovery
Catalog search
Capability description
A2A Agent Cards
Policy
Administration
```

A Skill is not necessarily an executable component.

---

# 19. Memory

Memory is a first-class platform concept.

Memory means information retained across interactions.

Possible scopes include:

```text
user
agent
tenant
application
custom
```

Memory configuration should use reusable policy definitions.

Example:

```yaml
memory:
  enabled: true
  policy: customer-memory
```

---

# 20. Session

Session represents active runtime state.

Session may contain:

```text
Conversation history
Execution context
Temporary state
Current user context
Invocation metadata
```

Session and Memory must remain separate concepts.

```text
Session != Memory
```

---

# 21. Memory Provider

Memory storage should be isolated behind a provider/service contract.

Conceptually:

```python
class MemoryProvider:
    async def load(key): ...

    async def store(key, update): ...

    async def delete(key): ...
```

Potential implementations may include:

```text
In-memory
PostgreSQL
Redis
External memory service
```

Only implementations required by current milestones should be built.

---

# 22. Control Plane vs Data Plane

These concerns must remain separated.

## Control Plane

```text
Create Agent
Update Agent
Version Agent
Deploy Agent
Stop Agent
Scale Agent
Configure Agent
Catalog Agent
Observe Agent
```

## Data Plane

```text
Invoke Agent
Model calls
Tool calls
MCP calls
Memory operations
A2A calls
Streaming
```

Normal agent invocation must not depend on routing through the Control Plane.

---

# 23. Managed Agent

A managed agent is an agent whose definition and lifecycle are owned by Open Simple Agent.

The platform may manage:

```text
Configuration
Versions
Deployment
Scaling
Restart
Rollback
Health
Logs
Policies
```

Managed does not imply a proprietary communication protocol.

Managed agents may expose A2A.

---

# 24. External Agent

An external agent is known to the Agent Catalog but is not lifecycle-managed by Open Simple Agent.

The platform may know:

```text
Agent Card
Endpoint
Skills
Capabilities
Security requirements
Health information
```

but does not own:

```text
Deployment
Scaling
Configuration
Version lifecycle
```

External agents are expected to use interoperable protocols such as A2A where applicable.

---

# 25. A2A

A2A is the preferred protocol for agent-to-agent interoperability.

It may be used for:

```text
Agent discovery
Agent Card publication
Skill discovery
Agent invocation
Agent-to-agent communication
```

A2A belongs primarily to the data plane.

Do not use A2A as the primary deployment-management protocol.

---

# 26. Control Plane API

The Control Plane exposes administrative APIs.

Conceptual examples:

```text
POST   /agents
GET    /agents
GET    /agents/{id}
PUT    /agents/{id}

POST   /agents/{id}/versions

POST   /deployments
GET    /deployments
GET    /deployments/{id}
PATCH  /deployments/{id}

POST   /deployments/{id}/start
POST   /deployments/{id}/stop
POST   /deployments/{id}/restart
POST   /deployments/{id}/scale
POST   /deployments/{id}/rollback

GET    /models
GET    /mcps
GET    /tools
GET    /skills
GET    /memory-policies
```

Exact contracts should be refined during implementation.

---

# 27. Agent Runtime API

The runtime exposes agent-facing endpoints.

Initial examples may include:

```text
POST /v1/invoke

GET /health/live
GET /health/ready
```

A2A endpoints are exposed according to the supported A2A specification.

Streaming may be added where supported.

---

# 28. Deployment Model

The preferred production model is:

```text
One logical managed agent
        =
One independently deployable runtime
```

Multiple replicas of the same agent may exist.

Example:

```text
support-agent
 ├── replica 1
 ├── replica 2
 └── replica 3
```

This provides clean:

```text
identity
scaling
resource limits
secrets
health
versioning
rollbacks
```

---

# 29. Multiple Agents in One Runtime

Running multiple unrelated agents inside one runtime may be supported later for:

```text
development
small installations
edge scenarios
testing
```

It is not the preferred initial production deployment model.

---

# 30. Runtime Deployment Provider

The Control Plane may use a deployment-provider abstraction.

Conceptually:

```python
class DeploymentProvider:
    validate(...)
    deploy(...)
    update(...)
    stop(...)
    start(...)
    scale(...)
    rollback(...)
    status(...)
```

Initial deployment providers may target:

```text
local process/container
Docker
Kubernetes/OpenShift
```

Only required providers should be implemented.

This is different from the Agent Runtime.

---

# 31. Manager Agent

The Manager Agent is optional.

It acts as an intelligent administrative interface to the Control Plane.

It may perform actions such as:

```text
search agents
inspect configuration
create draft definitions
validate definitions
compare versions
request deployment
request scaling
inspect health
inspect logs
request rollback
```

The Manager Agent must operate through controlled tools backed by Control Plane APIs.

---

# 32. Manager Agent Safety Boundary

The Manager Agent must not directly:

```text
write directly to the catalog database
manipulate Kubernetes resources without the Control Plane
read raw secrets
bypass policy
become the authoritative source of configuration
```

The Control Plane remains authoritative.

Approval may be required for high-impact changes.

---

# 33. Configuration Philosophy

Preferred precedence:

```text
Built-in Defaults
        ↓
Configuration
        ↓
Environment Variables
        ↓
Secret References
```

Configuration should be validated strictly.

Unknown properties should fail rather than silently being ignored.

---

# 34. Authentication and Authorization

Authentication and authorization must be configurable.

The platform should distinguish:

```text
administrator identity
application/user identity
agent identity
service/runtime identity
```

Agent identity and user identity must not be implicitly treated as the same concept.

Detailed security design is deferred to its implementation milestone.

---

# 35. Secrets

Secrets must remain externalized.

Agent definitions should reference:

```text
credential aliases
secret names
environment variables
secret stores
```

rather than containing credential values.

Never log secrets.

---

# 36. Observability

The platform should eventually expose:

```text
Agent invocation metrics
Model latency
Token usage
Tool calls
MCP calls
Errors
Session metrics
Memory operations
A2A calls
Deployment health
```

OpenTelemetry should be preferred where practical.

---

# 37. Language Choices

Initial choices:

```text
Control Plane Backend  Python
Generic Agent          Python
ADK Runtime            Python
Manager Agent          Python
Control Panel UI       TypeScript / React
```

Avoid unnecessary inter-language service boundaries during early development.

---

# 38. Persistence

Persistent Control Plane state should eventually use a database such as PostgreSQL.

Persist:

```text
Agent definitions
Agent versions
Templates
Model definitions
MCP definitions
Skill definitions
Memory policies
Deployment metadata
Catalog metadata
```

Runtime/session/memory persistence may use different stores depending on their semantics.

---

# 39. Container and Kubernetes Compatibility

Runtime and Control Plane containers should:

```text
run non-root
support arbitrary UID where practical
externalize state
externalize secrets
expose readiness/liveness
handle SIGTERM
avoid runtime package installation
support read-only root filesystem where practical
```

---

# 40. Testing Principles

Testing should include:

```text
Configuration validation
Catalog behavior
Agent creation
Model resolution
MCP resolution
Tool resolution
Skill resolution
Memory behavior
Session behavior
ADK runtime invocation
A2A behavior
Control Plane APIs
Deployment lifecycle
Container E2E tests
```

Tests should not depend on paid models.

Use deterministic fake/test models where possible.

---

# 41. Non-Goals

Do not initially build:

```text
Workflow engine
Workflow DSL
BPMN
Visual workflow designer
Multi-framework parity layer
Distributed workflow scheduler
Arbitrary Python plugin execution
Arbitrary shell execution
Custom MCP protocol
Custom A2A protocol
Enterprise vector database
Autonomous infrastructure management without controls
```

---

# 42. Architectural Decisions

## ADR-001

**Open Simple Agent is an agent runtime and management platform, not a workflow platform.**

---

## ADR-002

**Normal agent behavior is configuration-driven.**

Business-specific Python agent classes should not normally be required.

---

## ADR-003

**Google ADK is the initial runtime implementation.**

No runtime portability requirement exists for v1.

---

## ADR-004

**Python is the backend/runtime implementation language.**

TypeScript/React is used for the Control Panel UI.

---

## ADR-005

**AgentDefinition is persistent configuration; Agent is a runtime object.**

The catalog stores definitions, not runtime instances.

---

## ADR-006

**Models, MCPs, Tools, Skills, Memory, and Sessions are distinct concepts.**

Do not collapse them into a generic extension abstraction.

---

## ADR-007

**MCP is first-class.**

MCP is not represented merely as a Tool.

---

## ADR-008

**Skill means advertised semantic capability.**

A Skill is not necessarily executable.

---

## ADR-009

**Session and Memory remain separate.**

---

## ADR-010

**Control Plane and agent data plane remain separate.**

Normal requests do not route through the Control Plane.

---

## ADR-011

**A2A is used for agent interoperability.**

It is not the primary management/deployment protocol.

---

## ADR-012

**One logical agent per independently deployable runtime is the preferred production model.**

---

## ADR-013

**The Manager Agent is optional.**

The platform must operate fully without it.

---

## ADR-014

**The Manager Agent uses Control Plane tools and APIs.**

It does not directly control infrastructure or persistence.

---

# 43. Initial Target Architecture

```text
                           OPEN SIMPLE AGENT

                             CONTROL PLANE
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
   Agent Catalog              Resource Catalogs        Control Panel
        │                          │                          │
        │                  ┌───────┼────────┐                 │
        │                  │       │        │                 │
        │                Models   MCPs    Skills              │
        │                          │                           │
        │                        Tools                         │
        │                          │                           │
        │                       Memory                        │
        │                                                     
        └───────────────────────┬─────────────────────────────┘
                                │
                         AgentDefinition
                                │
                                ▼
                         GENERIC AGENT
                                │
                                ▼
                           ADK RUNTIME
                                │
                 ┌──────────────┼──────────────┐
                 │              │              │
               Model           MCP          Memory
                 │              │              │
                 └──────────────┼──────────────┘
                                │
                             Agent
                                │
                     ┌──────────┴──────────┐
                     │                     │
                   API                   A2A
                     │                     │
                     ▼                     ▼
                 Application            Agents
```

---

# 44. Project Success Criteria

Open Simple Agent succeeds when a user can:

1. Define an agent through configuration.
2. Select a model from the Model Catalog.
3. Attach MCP servers.
4. Attach native tools.
5. Declare discoverable skills.
6. Enable memory.
7. Configure session persistence.
8. Register the agent in the catalog.
9. Start the agent through the ADK runtime.
10. Invoke it through an API.
11. Expose it through A2A.
12. Manage its lifecycle from the Control Plane.
13. Create and update agents through a web Control Panel.
14. Later optionally perform administration through a Manager Agent.

The common path must remain simple even as advanced capabilities are added.