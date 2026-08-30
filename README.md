# Open Simple Agent

**Open Simple Agent (OSA)** is a lightweight, configuration-driven platform for creating, running, managing, and discovering AI agents.

The project focuses on simple autonomous agents rather than workflows. An agent is defined through configuration, connected to models, tools, MCP servers, skills, memory, and session services, and executed through a runtime implementation.

The initial runtime is based on **Google ADK**.

---

## Goals

Open Simple Agent aims to make creation and operation of AI agents simple enough that application code is not required for common agent scenarios.

A user should be able to define an agent using configuration such as:

```yaml
apiVersion: osa/v1alpha1
kind: Agent

metadata:
  name: customer-support
  description: Customer support assistant

spec:
  instruction: |
    Assist customers with support requests.
    Use the available tools when required.
    Do not invent customer information.

  model:
    ref: default

  mcps:
    - ref: crm
    - ref: knowledge

  tools:
    - calculator

  skills:
    - customer-support
    - case-resolution

  memory:
    enabled: true
    policy: user-memory
```

Bare strings are accepted for `model`, `tools`, `skills`, and `mcps` references
(`- calculator` is equivalent to `- ref: calculator`).

The platform then:

```text
Agent Definition
      ↓
Validate Configuration
      ↓
Resolve Model
Resolve MCPs
Resolve Tools
Resolve Skills
Resolve Memory
Resolve Session
      ↓
Create Runtime Agent
      ↓
Expose Agent API / A2A
```

---

# Core Principles

## 1. Configuration-Driven Agents

Agent behavior should normally be created through configuration rather than application code.

Creating:

```text
SupportAgent
BillingAgent
RenewalAgent
```

should not require classes such as:

```text
SupportAgent extends Agent
BillingAgent extends Agent
```

Instead, a generic runtime agent is created from an `AgentDefinition`.

---

## 2. Simple Agent Runtime

Open Simple Agent is not a workflow engine.

The normal execution model is:

```text
Request
   ↓
Agent
   ↓
Model Reasoning
   ↓
Tools / MCP / Memory
   ↓
Response
```

There is no workflow definition or workflow execution layer.

---

## 3. Control Plane and Data Plane Are Separate

The Control Plane manages:

```text
Agent definitions
Agent versions
Agent catalog
Models
MCP servers
Tools
Skills
Memory policies
Deployments
Health
Configuration
```

The agent data plane handles:

```text
Agent invocation
Tool execution
MCP interaction
Memory access
A2A communication
```

Agent requests should not need to pass through the Control Plane.

---

# Project Structure

```text
open-simple-agent/
│
├── control-plane/
│   ├── backend/
│   └── ui/
│
├── generic-agent/
│
└── runtimes/
    └── adk/
```

---

# Modules

## Control Plane

The Control Plane manages the lifecycle and configuration of agents.

Responsibilities include:

```text
Agent Catalog
Agent Templates
Agent Definitions
Agent Versions
Model Catalog
MCP Catalog
Tool Catalog
Skill Catalog
Memory Policies
Deployment Management
Runtime Status
Health
Observability
```

The Control Plane backend is implemented in **Python**.

The Control Panel UI is implemented in **TypeScript / React**.

---

## Generic Agent

The Generic Agent module defines the Open Simple Agent domain model and runtime contract.

Primary concepts include:

```text
Agent
AbstractAgent
AgentDefinition
AgentMetadata
AgentRequest
AgentResponse

ModelDefinition
ToolDefinition
McpDefinition
SkillDefinition

MemoryConfig
MemoryPolicy
SessionConfig

AgentRuntime
AgentFactory
```

The Generic Agent module must not contain application-specific agent implementations.

---

## Runtimes

Runtime implementations convert an `AgentDefinition` into a running agent.

Initial runtime:

```text
runtimes/adk
```

The ADK runtime is responsible for integrating Open Simple Agent definitions with Google ADK.

It handles:

```text
ADK agent creation
Model binding
Tool registration
MCP integration
Session integration
Memory integration
A2A exposure
Runtime invocation
Streaming where supported
```

Open Simple Agent does not initially promise compatibility across multiple runtime frameworks.

Additional runtimes may be introduced later only when there is a concrete requirement.

---

# Agent Model

Conceptually:

```text
Agent
  ▲
  │
AbstractAgent
  ▲
  │
GenericAdkAgent
```

The public agent contract is conceptually:

```python
class Agent:
    async def invoke(self, request): ...
```

The runtime implementation may wrap framework-native objects internally.

Framework-native state must not leak into Control Plane contracts.

---

# Agent Definition

An agent definition describes agent behavior.

Example:

```yaml
apiVersion: osa/v1alpha1
kind: Agent

metadata:
  name: customer-support
  version: 1.0.0

spec:
  description: >
    Handles customer support requests.

  instruction: |
    Assist the customer using available information and tools.

  model:
    ref: reasoning-default

  mcps:
    - ref: crm
    - ref: customer-knowledge

  tools:
    - calculator

  skills:
    - customer-support
    - case-resolution

  memory:
    enabled: true
    policy: user-memory

  session:
    persistence: true

  a2a:
    enabled: true
```

---

# Models

Models should normally be referenced through the Model Catalog.

Example:

```yaml
model:
  ref: reasoning-default
```

Instead of duplicating provider configuration in every agent.

A Model Catalog entry may define:

```text
Provider
Model name
Endpoint
Authentication reference
Timeout
Model capabilities
Generation defaults
```

The initial implementation may use LiteLLM where appropriate to support multiple model providers while retaining a single agent runtime implementation.

---

# MCP

MCP is a first-class platform concept.

An MCP server is not modeled merely as a Tool because MCP may expose:

```text
Tools
Resources
Prompts
```

Example agent configuration:

```yaml
mcps:
  - ref: crm
  - ref: payments
```

The MCP Catalog stores reusable connection definitions.

Conceptually:

```text
MCP Catalog
   │
   ├── CRM MCP
   ├── Payment MCP
   └── Knowledge MCP
          │
          ▼
      Agent Runtime
```

Credentials should be referenced through external secret configuration rather than stored directly inside agent definitions.

---

# Tools

Tools are executable capabilities available to an agent.

Tools may come from:

```text
Native runtime tools
Application-provided tools
OpenAPI integrations
MCP tools
Future integrations
```

MCP itself remains separately modeled even when it exposes tools.

---

# Skills

A Skill describes what an agent claims it can accomplish.

Example:

```yaml
skills:
  - residency-status
  - submit-renewal
```

A Skill is primarily used for:

```text
Discovery
Catalog search
A2A Agent Card metadata
Capability description
Access policy
Administration
```

A Skill is not necessarily an executable function.

---

# Memory

Memory is a first-class runtime service.

Memory represents information retained across interactions.

Memory is different from Session.

```text
Session
    Current conversation/runtime context

Memory
    Information retained across interactions
```

Possible memory scopes include:

```text
user
agent
tenant
application
custom
```

Memory behavior should be defined through reusable Memory Policies.

Example:

```yaml
memory:
  enabled: true
  policy: user-memory
```

---

# Sessions

Sessions represent the active conversational/runtime state.

A session may include:

```text
Conversation history
Runtime context
Temporary state
User correlation
Execution metadata
```

Session persistence should be configurable independently from long-term memory.

---

# Agent Catalog

The Agent Catalog is the source of discoverable agent metadata.

An Agent Catalog entry may include:

```text
Agent ID
Name
Description
Version
Status
Skills
Capabilities
Endpoint
Runtime
Deployment
Agent Card
Ownership metadata
Health
```

The catalog stores agent definitions and metadata.

It does not store in-memory runtime agent objects.

---

# Agent Templates

Templates provide starting points for creating agents.

Example templates:

```text
generic
customer-support
research
developer
classifier
knowledge-assistant
```

Creating an agent from a template produces a complete `AgentDefinition`.

Runtime behavior should not depend permanently on inheritance from a template.

```text
Template
   ↓
Create
   ↓
AgentDefinition
```

---

# Managed Agents

A managed agent is an agent whose definition and lifecycle are controlled by Open Simple Agent.

The platform may control:

```text
Configuration
Versioning
Deployment
Scaling
Restart
Rollback
Health
Logs
Policies
```

Managed agents may still expose A2A for interaction with other agents.

---

# External Agents

The catalog may later contain external agents that are not deployed or configured by Open Simple Agent.

External agents may be registered through their A2A metadata or Agent Card.

Open Simple Agent may discover and interact with them, but does not control their lifecycle.

---

# A2A

A2A is used for agent interoperability.

Conceptually:

```text
Managed Agent A
      │
      │ A2A
      ▼
Managed Agent B

Managed Agent A
      │
      │ A2A
      ▼
External Agent
```

A2A is part of the agent data plane.

It is not the primary mechanism used by the Control Plane to deploy or configure managed agents.

---

# Manager Agent

A Manager Agent may be introduced later as an optional administrative interface.

Example requests:

```text
Create a new support agent.

Which agents currently use the old model?

Show unhealthy agents.

Create an agent similar to support-agent but only allow CRM read access.

Rollback billing-agent.
```

The Manager Agent operates through controlled Control Plane APIs.

It is not the source of truth.

```text
Administrator
      ↓
Manager Agent
      ↓
Control Plane API
      ↓
Agent Catalog / Deployment Manager
```

The platform must work completely without the Manager Agent.

---

# Technology

Initial technology choices:

```text
Control Plane Backend   Python
Control Panel UI        TypeScript / React
Generic Agent           Python
Runtime                 Google ADK / Python
API                     FastAPI or equivalent
Configuration           YAML + environment variables
Persistence             PostgreSQL initially where persistence is required
Containers              OCI / Docker
Deployment              Kubernetes / OpenShift compatible
```

---

# Initial Scope

Initial releases focus on:

```text
Configuration-driven agents
ADK runtime
Model catalog
MCP catalog
Tool catalog
Skill catalog
Memory
Sessions
Agent catalog
Control Plane API
Control Panel UI
Agent runtime lifecycle
A2A exposure
```

---

# Non-Goals

The initial project does not aim to provide:

```text
Workflow execution
Workflow DSL
BPMN
Generic multi-framework runtime portability
Distributed workflow scheduling
Arbitrary runtime code execution
Visual workflow design
Agent traffic proxying through the Control Plane
Automatic autonomous infrastructure changes without policy controls
```

---

# Status

The project is currently in the architecture and initial implementation phase.

See:

```text
PROJECT_DEFINITION.md
TODO.md
```

for architectural decisions and implementation milestones.