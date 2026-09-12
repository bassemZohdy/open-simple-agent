# DAR-001: ADK vs. LangChain vs. LangGraph for Open Simple Agent

## Status

Accepted — retain two runtime backends behind shared OSA contracts. Keep ADK
2.x as the packaged HTTP/A2A runtime and use LangChain v1 plus direct LangGraph
as a programmatic backend for provider portability and explicit graph
orchestration. Direct LangGraph remains the preferred foundation for future
durable-workflow work, but it is not yet a replacement for the ADK service.

## Date

2026-09-07

## Decision owner

Open Simple Agent maintainers

## Executive decision

The bounded implementation POC is complete. OSA now has two framework-specific
backends behind a shared composition layer:

1. **ADK 2.x** remains the packaged runtime service. It owns the existing
   FastAPI, SSE, MCP, and optional A2A surface and invokes through the ADK
   `Runner`.
2. **LangChain v1 + LangGraph** is available as `osa-langgraph-runtime`. It
   uses LangChain chat-model/tool integrations and a direct LangGraph
   `StateGraph` model/tool loop while implementing the shared OSA invocation,
   session, memory, policy, timeout, response, and streaming contracts.

The backend is selected at runtime composition/deployment time; no
framework-specific selector was added to `AgentDefinition`. The shared
abstraction keeps resource and security policy in OSA while each backend owns
framework construction and execution.

The POC validates contract compatibility and bounded coexistence. It does not
claim full parity: the LangGraph slice is programmatic only, rejects MCP
references until an adapter is added, does not expose A2A, and does not replace
the packaged ADK HTTP service. Optional LangGraph checkpointing is experimental
and does not yet replace the OSA `SessionProvider` as the contractual session
boundary.

This decision separates technology selection from delivery planning. A later
implementation plan must estimate migration effort, coexistence, and schedule
impact separately; none of those costs are included in the weighted score.

## Context and scope

OSA is a configuration-driven agent platform. Its target object is an agent
assembled from instructions, a model, tools, MCP servers, skills, memory, and
session state. OSA deliberately focuses on agents, not workflows. The
repository now has an ADK 2.x runtime and a LangChain/LangGraph runtime; the
generic package remains independent of framework-specific imports.

The comparison covers three implementation options:

1. **ADK — framework candidate.** ADK's agent, workflow, model, tool, session,
   memory, A2A, evaluation, and deployment capabilities as an OSA runtime
   option. The existing `runtimes/adk` implementation is repository context,
   not a scoring advantage.
2. **LangChain — high-level agent runtime.** LangChain v1's `create_agent`
   abstraction, including the LangGraph runtime underneath it.
3. **LangGraph — direct orchestration runtime.** A direct graph/runtime
   adapter, using LangChain model/tool integrations when useful.

The weighted comparison below remains a greenfield technology assessment. The
bare libraries do not automatically inherit OSA's
tenant isolation, redaction, deployment, or API contracts; each option must
meet those requirements through an equivalent adapter. No current ADK code,
sunk cost, migration effort, delivery timing, or existing test coverage is
included in the score.

The implementation outcome is recorded separately below. It confirms that
both backends can share OSA contracts, but it does not award implementation
leverage points or turn the bounded LangGraph slice into full framework parity.

This is an architecture decision, not a latency, token-cost, or model-quality
benchmark. Those variables depend primarily on the selected model, prompts,
tools, deployment topology, and workload. The weights and scores are explicit
so they can be changed when OSA's product priorities change.

## Repository baseline

Evidence from the current repository:

- `uv.lock` resolves `google-adk` 2.8.0; the ADK package permits the tested
  2.x line with `google-adk>=2.0,<3.0`.
- `GenericAdkAgent` resolves model, tool, skill, MCP, memory-policy, and
  session resources at construction and invokes through the ADK `Runner`.
- The runtime already exposes OSA's stable invoke and SSE contracts, filtered
  and namespaced MCP tools, A2A Agent Card/JSON-RPC support, OSA-owned auth and
  tenant binding, redaction-safe telemetry, and deployment-bundle bootstrap.
- `runtimes/langgraph` is now a fourth workspace package,
  `osa-langgraph-runtime`. `OsaLangGraphAgent` and `LangGraphRuntime` use
  LangChain chat models/tools with a concrete LangGraph `StateGraph` model/tool
  loop and share OSA's catalogs, policies, sessions, memory, timeouts, stable
  responses, and streaming events.
- `RuntimeDependencies` and `AgentStreamEvent` are framework-neutral seams in
  `generic-agent`; the ADK and LangGraph runtimes consume those seams without
  importing either framework into the generic package.
- Current gaps are durable runtime sessions, migration-owned memory schema,
  distributed A2A task state, capability-level audit telemetry, rate limiting,
  and deployment reliability hardening. These are in `TODO.md` and should not
  be mistaken for framework capabilities that a replacement would get for
  free.
- The backlog now records the bounded LangGraph POC as present and keeps MCP,
  A2A, a shared HTTP service, and durable checkpoint validation as follow-up
  work.

These facts are baseline context only. They do not award points to ADK. This
DAR intentionally excludes existing implementation leverage and migration
cost because it is revisiting the technology choice itself.

See [Project Definition](../PROJECT_DEFINITION.md),
[Current Architecture](ARCHITECTURE.md), and [Active Backlog](../TODO.md).

## Implementation outcome

The implementation validates a dual-backend architecture, not a framework
replacement. Both backends construct agents from the same OSA definition and
can receive the same `RuntimeDependencies` composition object. Framework
construction, model binding, tool execution, and graph/runner lifecycle remain
inside their respective runtime packages.

| Surface | ADK 2.x backend | LangChain/LangGraph backend |
|---|---|---|
| Package/runtime | `runtimes/adk`, `AdkRuntime`, `GenericAdkAgent` | `runtimes/langgraph`, `LangGraphRuntime`, `OsaLangGraphAgent` |
| Model execution | ADK `Runner` and ADK model adapters | LangChain chat models; OSA fake provider bridge, provider-specific `init_chat_model` adapters, and optional `langchain-litellm` adapter |
| Agent loop | ADK model-driven function calling and workflow support | Concrete LangGraph `StateGraph`: model node → conditional `ToolNode` → model node, bounded by OSA iteration limits |
| OSA contracts | Invoke/response, sessions, memory policy, tools/MCP, timeouts, stable errors, SSE, auth, deployment bootstrap, optional A2A | Invoke/response, native tools, skills metadata, sessions, memory policy/context, timeouts, stable errors, and shared streaming events |
| Service boundary | Packaged FastAPI runtime API, `osa-runtime` CLI, SSE, MCP, optional A2A | Programmatic backend and bundle bootstrap; no framework-neutral HTTP service, MCP, or A2A surface yet |
| State/durability | OSA `SessionProvider` bridged into ADK sessions; A2A task state is process-local by default with an optional PostgreSQL SDK store plus scoped OSA ownership leases/fencing, while the SDK active-task registry and full late-event recovery remain open | OSA `SessionProvider` remains authoritative; optional LangGraph checkpointer is experimental and has no validated durable production contract |

The LangGraph implementation deliberately fails fast when an agent declares
MCP references instead of silently dropping them. This keeps OSA's resource
and security policy explicit while the LangGraph MCP adapter is still absent.

### POC evidence

The repository-level validation completed with:

- six LangGraph integration tests covering fake-provider invocation, native
  LangGraph tool execution, shared streaming events, session ownership, shared
  dependency composition, and explicit MCP rejection;
- `uv run pytest -q`: **544 passed, 23 skipped**;
- Ruff format/check, strict mypy, `uv lock --check`, and the
  `osa-langgraph-runtime` package build all passing.

This is contract and integration validation only. It is not a performance,
durability, restart/resume, multi-replica, or live-provider benchmark.

## Important terminology

LangChain and LangGraph are related layers:

| Layer | Primary role | Consequence for OSA |
|---|---|---|
| LangChain v1 | Higher-level agent framework with model, tool, middleware, and agent-loop abstractions | Faster path for standard tool-calling agents and broad provider integrations |
| LangGraph | Lower-level orchestration framework/runtime for stateful, long-running graphs | Stronger fit for explicit state machines, durable execution, interrupts, and mixed deterministic/agentic workflows |
| ADK 2.x | Agent toolkit with LLM agents, deterministic workflow agents, graph workflows, tools, sessions, memory, evaluation, and deployment ecosystem | Strong candidate for Google-native, multimodal, live, and A2A-oriented requirements |

LangChain v1 agents run on LangGraph, so adopting both is normally a layered
choice, not two additive runtimes. Direct LangGraph gives OSA more control but
also exposes more orchestration and state machinery to the adapter.

## Capability comparison

The following comparison describes the practical architectural shape of each
option for OSA. “Native” means the framework documents the capability as a
first-class concept; it does not mean OSA's security or operational policy is
automatically supplied. The direct LangGraph column describes framework
capability; the current OSA slice is deliberately narrower, as documented in
the implementation outcome above.

| Capability | ADK candidate | LangChain v1 | Direct LangGraph | OSA consequence |
|---|---|---|---|---|
| Primary abstraction | `LlmAgent`/`Agent`, workflow agents, `Runner` | `create_agent`, middleware, model/tool abstractions | State graph, nodes, edges, compiled graph | ADK is a natural agent fit; LangGraph needs an explicit agent-to-graph convention |
| Execution model | Model-driven agent loop plus sequential, parallel, loop, dynamic, and graph workflows | Prebuilt model/tool loop on LangGraph, customizable through middleware | Explicit graph execution; deterministic and agentic nodes can coexist | LangGraph is strongest when execution topology is product behavior, not merely an implementation detail |
| Configuration-driven agents | Can be represented through an OSA adapter; ADK's agent/config model is a reasonable fit | Possible through an adapter, but runtime objects and middleware policy must be synthesized | Possible, but graph topology/state schema must be represented or fixed by the adapter | All three require an equivalent OSA adapter; existing implementation is excluded from scoring |
| Model portability | Optimized for Gemini, with documented OpenAI, Anthropic, Ollama, vLLM, LiteLLM, and other adapters | Unified model interfaces and dedicated provider packages | Usually uses LangChain model integrations/core interfaces | LangChain/LangGraph lead on provider switching; OSA's model catalog reduces the practical gap |
| Native tool calling | ADK-native function calling and structured tools | Tool decorators, schemas, sequential/parallel calls, retries, dynamic selection | Nodes/tools are explicit; LangChain tools can be used | All are viable; compare capability and adapter semantics, not migration cost |
| MCP | First-class MCP client/toolset; OSA adds pooling, filters, namespaces, limits, credentials, and deterministic preflight | MCP is available through the LangChain ecosystem | Available through LangChain components or direct integration; not the core graph abstraction | OSA's existing MCP boundary must remain authoritative whichever runtime is used |
| OpenAPI and built-in tools | ADK documents custom, OpenAPI, code-execution, search, and other tools | Large integration ecosystem; provider-specific packages | Depends on LangChain tools or custom nodes | LangChain offers breadth; ADK offers a more unified Google/ADK tool surface |
| Short-term state | OSA `SessionProvider` owns session identity, TTL, bounded history, and ADK event mapping | Message state and custom agent state; persistence comes from LangGraph | Checkpoints are first-class and thread-scoped | LangGraph is stronger by default; OSA must still enforce ownership and redaction |
| Long-term memory | ADK memory services exist; OSA has an explicit policy-driven memory contract and PostgreSQL provider | Long-term memory is commonly implemented using LangGraph stores or integrations | Checkpointers plus stores explicitly separate thread state from cross-thread memory | LangGraph has the best primitive; OSA has the best domain policy; adapter work is required either way |
| Durability/resume | ADK has session services and managed integrations; OSA sessions and A2A task state remain process-local by default, with explicit PostgreSQL providers and A2A ownership leases for shared deployments | Inherited from LangGraph when using a persistent checkpointer/server | Built-in checkpointing supports resume, time travel, fault tolerance, and pending writes | This is the clearest future reason to add LangGraph |
| Human-in-the-loop | ADK supports human input/tool confirmation and callbacks/plugins | Human-in-the-loop middleware with approve/edit/reject decisions | `interrupt()` pauses graph execution and resumes from persisted state | LangGraph has the cleanest durable pause/resume model |
| Multi-agent | Hierarchical agents, delegation, `AgentTool`, collaborative workflows, and A2A | Multi-agent patterns through middleware/subagents and LangGraph | Subgraphs, nodes, and explicit routing | ADK already covers OSA's current agent-to-agent direction |
| A2A | First-party ADK/A2A support | Agent Server documents A2A endpoints | Requires an adapter or Agent Server integration | ADK has a capability advantage for A2A, but no current-implementation points |
| Streaming | ADK documents text/audio bidirectional live streaming | Streaming through agent/graph APIs; middleware and model support vary | Typed graph/message/state/custom streaming modes | ADK is strongest for live/voice; all require an OSA streaming adapter |
| Long-running jobs | OSA runtime timeout/iteration controls; deployment/task durability still being hardened | Agent Server supplies runs, threads, crons, task queue, and persistence | Agent Server supplies the same operational layer around compiled graphs | LangGraph becomes attractive when jobs must survive process failure or await humans |
| Artifacts/code execution | ADK has artifact management and code-execution integrations | Available through tools and ecosystem packages | Available as nodes/tools; not a core graph primitive | No decisive framework advantage for current OSA scope |
| Guardrails and policy | OSA route auth, tenant binding, resource policy, secret resolver, redaction, stable errors; ADK callbacks/plugins/tool confirmation and model safety | Middleware for guardrails, retries, PII handling, and HITL | Graph boundaries and interrupts; policy still belongs in OSA/tool middleware | Keep policy outside the model prompt and outside framework defaults |
| Observability | OSA Prometheus/OpenTelemetry/redaction/audit baseline plus ADK traces and developer UI | LangSmith tracing, evaluation, and production feedback loop | LangSmith tracing/state transitions plus graph introspection | LangChain/LangGraph lead in integrated UX; OSA must keep framework-neutral telemetry labels |
| Evaluation | ADK eval sets, trajectory/tool-use criteria, response quality, safety, user simulation, CLI/UI | LangSmith offline/online datasets, evaluators, comparison, and alerts | Same LangSmith platform plus graph trajectory/state visibility | Both alternatives are viable; avoid making a managed platform a hard dependency |
| Deployment | Supports containerized deployment and Google Cloud/Agent Runtime options | LangSmith Agent Server or standalone containers; self-hosting adds platform choices | LangGraph Agent Server or standalone Docker/Kubernetes; database/task queue are part of the production topology | All require an OSA-preserving service/deployment design; current OSA deployment is not scored |
| API and service boundary | OSA FastAPI runtime API, health/readiness, auth, metrics, A2A, and direct invocation | Agent Server has a rich assistants/threads/runs API, but it is a different public contract | Same Agent Server model, centered on graphs/threads/runs | OSA should not replace its API with a framework-native API |
| Security and tenancy | Provides hooks and boundaries, but OSA JWT/OIDC, permissions, tenant binding, and redaction still belong in the adapter | Middleware helps, but no automatic match for OSA's tenant/session semantics | Durable state makes isolation more important; OSA policy remains external | Security score is primarily about adapter discipline, not marketing feature count |
| Language reach | ADK documents Python, TypeScript, Go, Java, and Kotlin | Python and JavaScript/TypeScript ecosystem | Python and JavaScript/TypeScript ecosystem | ADK is better if OSA expands beyond Python; current OSA remains Python |
| Licensing | ADK repository: Apache 2.0 | LangChain repositories/packages: MIT | LangGraph repository/package: MIT | All are permissive; license does not decide this DAR |
| Operational complexity | Current OSA runtime is known; Google-specific integrations are optional/replaceable behind OSA adapters | Many provider packages plus optional LangSmith service | More explicit graph/state infrastructure; Agent Server adds database/task-queue requirements | LangGraph's power has the highest infrastructure and adapter surface |

## Reviewed weighted decision model

The previous revision was not valid for this DAR because it included a
“migration and implementation leverage” criterion. That criterion rewarded
ADK for already being implemented in OSA, while this DAR is explicitly
revisiting the technology choice. It has therefore been removed completely.

The corrected model treats all three options as greenfield candidates that
must satisfy OSA's requirements through an equivalent adapter. It increases
the weight of openness/vendor neutrality, state/durability, and explicit
workflow control; it keeps interoperability together; and it prevents
ecosystem breadth from overwhelming platform requirements. Scores remain
expert judgments based on OSA's target requirements and official
documentation; no framework performance benchmark is implied.

The completed POC does not retroactively award points for implementation
effort or test coverage. It does, however, replace the former “pending POC”
assumption with concrete evidence that the shared contract can support both
backends for the validated slice.

### Scale

Each option receives a score from 0 to 5:

- **5** — excellent fit / first-class capability / little additional OSA work
- **4** — strong fit with bounded integration or operational caveats
- **3** — workable but requires meaningful adapter work or has material gaps
- **2** — weak fit for OSA's current scope
- **1** — poor fit or mostly requires replacing existing boundaries
- **0** — does not satisfy the requirement

Weighted points are calculated as:

```text
weighted points = criterion weight × raw score / 5
total score = sum(weighted points)
```

The score is out of 100. Weights describe OSA's target priorities: contract
fit, openness, production boundaries, durable state, and workflow control
matter more than raw ecosystem size. Existing ADK implementation, sunk cost,
migration effort, and delivery timing are intentionally not scored.

### Criteria and weights

| ID | Criterion | Weight | Why it matters to OSA |
|---|---|---:|---|
| C1 | OSA architecture and product fit | 12 | Preserve configuration-driven agents, framework isolation, and the contract-first shape |
| C2 | Openness and vendor neutrality | 15 | Keep the runtime portable across model vendors, deployment environments, and self-hosted infrastructure |
| C3 | Runtime/service and deployment fit | 10 | Support direct data-plane invocation, bundles, health/readiness, Control Plane separation, and deployable images |
| C4 | Security, governance, and tenant-boundary fit | 9 | Preserve auth, permissions, secret redaction, policy enforcement, and tenant isolation |
| C5 | Sessions, memory, and durability | 12 | Support bounded sessions and durable/resumable state for production workflows |
| C6 | Orchestration and workflow control | 12 | Support agent loops, explicit graphs, retries, branching, and human approval |
| C7 | Tools, MCP, A2A, and interoperability | 9 | Preserve cataloged tools, MCP lifecycle, filtering, limits, credentials, Agent Cards, and remote invocation |
| C8 | Model/provider portability | 7 | Avoid unnecessary coupling to one model vendor and preserve model catalog semantics |
| C9 | Observability and evaluation | 4 | Support redaction-safe telemetry, regression evaluation, quality signals, and operations without requiring a managed platform |
| C10 | Streaming, long-running, and real-time behavior | 4 | Preserve SSE now and leave room for live/voice or long-running interactions |
| C11 | Developer experience, ecosystem, and language reach | 3 | Reduce build friction and maintain a healthy integration surface |
| C12 | Operational complexity, license, and vendor exposure | 3 | Keep the runtime operable, permissively licensed, and not dependent on a hosted control plane |
|  | **Total** | **100** |  |

### Full weighted score

Scores are shown as **raw score / weighted points**. Weighted points are
rounded to one decimal for display; totals are calculated from the displayed
precision.

| Criterion | Weight | ADK candidate | LangChain v1 candidate | LangGraph candidate |
|---|---:|---:|---:|---:|
| C1 Architecture/product fit | 12 | 4.5 / 10.8 | 4.0 / 9.6 | 3.5 / 8.4 |
| C2 Openness/vendor neutrality | 15 | 4.0 / 12.0 | 5.0 / 15.0 | 5.0 / 15.0 |
| C3 Runtime/deployment fit | 10 | 4.0 / 8.0 | 3.5 / 7.0 | 4.5 / 9.0 |
| C4 Security/governance/tenancy | 9 | 4.5 / 8.1 | 4.0 / 7.2 | 4.0 / 7.2 |
| C5 Sessions/memory/durability | 12 | 4.0 / 9.6 | 4.5 / 10.8 | 5.0 / 12.0 |
| C6 Orchestration/workflow control | 12 | 4.5 / 10.8 | 4.0 / 9.6 | 5.0 / 12.0 |
| C7 Tools/MCP/A2A/interoperability | 9 | 4.5 / 8.1 | 4.5 / 8.1 | 4.0 / 7.2 |
| C8 Model/provider portability | 7 | 4.0 / 5.6 | 5.0 / 7.0 | 4.5 / 6.3 |
| C9 Observability/evaluation | 4 | 4.5 / 3.6 | 4.5 / 3.6 | 4.5 / 3.6 |
| C10 Streaming/long-running/realtime | 4 | 5.0 / 4.0 | 4.0 / 3.2 | 4.5 / 3.6 |
| C11 Developer experience/ecosystem/languages | 3 | 4.5 / 2.7 | 5.0 / 3.0 | 4.5 / 2.7 |
| C12 Operations/license/vendor exposure | 3 | 4.0 / 2.4 | 3.5 / 2.1 | 3.5 / 2.1 |
|  | **100** | **85.7** | **86.2** | **89.1** |

### Result

| Rank | Option | Score | Interpretation |
|---:|---|---:|---|
| 1 | LangGraph candidate | **89.1 / 100** | Best neutral greenfield fit because it leads on openness, durable state, explicit workflows, and self-hostable runtime control |
| 2 | LangChain v1 candidate | **86.2 / 100** | Strong near-tie with the best provider/ecosystem experience; normally use it as a layer on LangGraph rather than a separate foundational runtime |
| 3 | ADK candidate | **85.7 / 100** | Strong across agent fit, tools, A2A, streaming, evaluation, and Google-native capabilities, but less vendor-neutral and less durable-workflow-oriented |

## Score rationale

### LangGraph — 89.1

LangGraph wins the neutral comparison because its core is permissively
licensed, can be used without LangChain, and provides first-class checkpoints,
stores, interrupts, streaming, and durable execution. The implemented OSA
slice now proves a concrete model/tool graph, shared tool wrappers, bounded
iteration, OSA session and memory bridging, and stable streaming events. Those
primitives still give OSA the strongest future foundation for explicit state,
long-running jobs, human approval, and restart/resume semantics.

The implementation is intentionally narrower than that capability assessment:
the current graph topology is fixed rather than bundle-defined, the optional
checkpointer is not a validated durable session store, and MCP/A2A/HTTP parity
is absent. LangGraph remains the preferred future foundation, not the current
packaged service replacement.

### LangChain — 86.2

LangChain is a close second and leads on provider portability, integration
breadth, and developer experience. The implemented backend uses LangChain chat
models, `StructuredTool`, provider-specific `init_chat_model` adapters, and an
optional LiteLLM adapter on top of a direct LangGraph graph. Its v1 agents are
built on LangGraph, so it remains best viewed as a high-level layer on the
LangGraph foundation rather than as a competing foundational runtime.

It scores slightly below direct LangGraph because the high-level abstraction
does not itself define the graph/state contract OSA needs for durable workflow
execution. LangChain remains a strong choice when standard tool-calling agents
and broad provider integrations are more important than direct control of
state-machine execution.

### ADK — 85.7

ADK remains a strong candidate and the current packaged OSA service: its
documented agent and workflow model, native tool calling, A2A support,
live/voice streaming, evaluation, and Google deployment options fit many OSA
requirements. It scores below the LangChain family in this neutral model
because its center of gravity is closer to the Google/ADK ecosystem and
because LangGraph exposes more direct durable graph state and workflow
control.

The score is not a penalty for OSA's existing ADK code. It is the capability
assessment ADK receives as a candidate under the same greenfield assumption as
the other two options. Its existing implementation can reduce real delivery
cost, but that belongs in a separate migration/business-case analysis.

## Sensitivity analysis

The ranking is priority-dependent. The neutral profile already makes durable,
resumable workflows and openness material, which is why LangGraph leads. If
OSA changes the product priorities further, the margin can change:

| Profile | ADK | LangChain | LangGraph |
|---|---:|---:|---:|
| Neutral greenfield profile | 85.7 | 86.2 | **89.1** |
| If durable workflow state is weighted even more heavily | Lower | Middle | **Higher** |

This sensitivity view is qualitative because the weights for a future product
profile have not been approved. It also deliberately does not use migration
cost as a weighting input. A separate delivery model may show ADK as cheaper
to adopt because it already exists, but that would answer “what is cheapest to
change?” rather than “which technology is the better target?”

Other assumptions that could change the result:

- If Google Cloud/Vertex Agent Runtime becomes a mandatory deployment target,
  ADK's deployment score increases.
- If multi-provider model switching and third-party integrations become the
  dominant product value, LangChain's score increases.
- If OSA adds its own durable workflow DSL and scheduler, direct LangGraph's
  incremental value decreases because the platform would own more of the
  orchestration contract.
- If OSA requires framework-neutral observability and self-hosting without a
  managed platform, the LangSmith-related advantage must be discounted and
  the adapter's native telemetry work must be scored explicitly.

## Consequences of the decision

### Positive

- OSA now has a concrete dual-backend architecture instead of a technology
  choice that is only theoretical.
- The shared abstraction keeps generic contracts and policy independent of ADK,
  LangChain, and LangGraph.
- LangGraph provides the strongest neutral foundation for durable workflows,
  explicit state, and human approval/resume requirements.
- LangChain is available as a higher-level model/tool/provider layer without
  making it the foundational orchestration choice.
- ADK remains the packaged service for Google-native, multimodal, live, MCP,
  and A2A requirements while the second backend evolves independently.

### Trade-offs

- The current decision is coexistence, not migration. Moving the packaged
  service to LangGraph would still create real delivery and compatibility work;
  those costs remain outside this DAR.
- LangGraph's stronger state/workflow primitives bring more infrastructure and
  adapter responsibility than a simple agent loop.
- The LangGraph POC does not yet prove self-hosted durable checkpoints,
  restart/resume, MCP/A2A, or a shared HTTP service. These are explicit
  follow-up requirements, not implied capabilities of the current package.
- LangChain and LangGraph's strongest integrated observability experience is
  commonly associated with LangSmith, so production promotion must preserve
  framework-neutral, redaction-safe OSA telemetry.
- ADK may still be the better operational choice if Google Cloud or its live
  and A2A capabilities become mandatory; that should be decided by explicit
  requirements, not by existing code.

## Revisit triggers

Reopen this DAR when at least one of the following is approved as a product
requirement:

- A user-facing workflow must pause for hours or days for human approval and
  resume safely after process or deployment failure.
- OSA needs graph-level fan-out/fan-in, conditional routing, durable retries,
  time travel, or explicit state transitions as a managed product feature.
- Runtime sessions must be durable and replica-safe as a hard launch
  requirement rather than a backlog improvement.
- A customer requirement cannot be served by ADK's model/tool adapters or
  current MCP/A2A boundaries and requires a LangChain-only integration.
- OSA decides that the runtime API should expose a graph/run/thread model in
  addition to the current agent/invocation model.
- The LangGraph backend is promoted from programmatic use to the packaged
  service, which requires a parity review for HTTP, auth, MCP, A2A, deployment,
  and durable state.

## Future adapter guardrails

The completed POC satisfies the shared-contract, native-tool, session,
memory-policy, timeout, iteration-limit, stable-response, and streaming parts
of this list for the supported slice. Before expanding the LangGraph backend
or promoting it to the packaged service, the implementation must:

1. Keep `generic-agent` free of ADK, LangChain, and LangGraph imports.
2. Implement the existing `Agent`/`AgentRuntime` contract before adding any
   framework-native API.
3. Map `AgentDefinition` resources through OSA catalogs; never allow arbitrary
   runtime code or URLs from agent configuration.
4. Enforce OSA session ownership, TTL, bounded history, tenant binding,
   memory policy, secret redaction, stable error types, and timeouts outside
   the model's instructions.
5. Preserve direct runtime invocation; the Control Plane must not become a
   synchronous inference proxy.
6. Preserve OSA's A2A card, auth, audit, metrics, and streaming contracts when
   the backend is exposed through the service boundary.
7. Define durable checkpoint retention, encryption, tenant partitioning,
   idempotency, cancellation, replay, retry, and migration semantics before
   shipping a durable graph runtime.
8. Add the remaining adapter test matrix: construction/reference failures,
   auth and tenant isolation, tool/MCP filtering, session/memory behavior,
   streaming, A2A, timeouts, concurrency, restart/resume, and redaction.
9. Run a workload benchmark separately from this DAR covering quality,
   p50/p95 latency, token usage, concurrency, failure recovery, and operating
   cost on the same models and tools.

## Evidence and sources

### OSA sources

- [Project Definition](../PROJECT_DEFINITION.md)
- [Current Architecture](ARCHITECTURE.md)
- [Active Backlog](../TODO.md)
- [Shared runtime dependencies](../generic-agent/src/osa/generic_agent/runtime.py)
- [Shared streaming events](../generic-agent/src/osa/generic_agent/streaming.py)
- [ADK runtime manifest](../runtimes/adk/pyproject.toml)
- [LangGraph runtime manifest](../runtimes/langgraph/pyproject.toml)
- [LangGraph runtime implementation](../runtimes/langgraph/src/osa/runtimes/langgraph/runtime.py)
- [LangGraph model adapters](../runtimes/langgraph/src/osa/runtimes/langgraph/model_adapter.py)
- [LangGraph integration tests](../tests/integration/test_langgraph_runtime.py)
- [Resolved dependency lock](../uv.lock)

### Official ADK sources

- [ADK Technical Overview](https://adk.dev/get-started/about/)
- [ADK 2.0 overview and graph-based workflows](https://adk.dev/2.0/)
- [ADK MCP tools](https://adk.dev/tools-custom/mcp-tools/)
- [ADK sessions and state](https://adk.dev/sessions/)
- [ADK memory](https://adk.dev/sessions/memory/)
- [ADK A2A exposure](https://adk.dev/a2a/quickstart-exposing/)
- [ADK evaluation](https://adk.dev/evaluate/)
- [ADK observability](https://adk.dev/observability/)
- [ADK safety and security](https://adk.dev/safety/)
- [ADK deployment](https://adk.dev/deploy/)
- [ADK Python repository and license](https://github.com/google/adk-python)

### Official LangChain and LangGraph sources

- [LangChain overview](https://docs.langchain.com/oss/python/langchain/overview)
- [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents)
- [LangChain model initialization](https://docs.langchain.com/oss/python/langchain/models)
- [LangChain providers and models](https://docs.langchain.com/oss/python/concepts/providers-and-models)
- [LangChain integrations](https://docs.langchain.com/oss/python/integrations/providers/overview)
- [LangChain LiteLLM integration](https://docs.langchain.com/oss/python/integrations/providers/litellm)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming)
- [LangSmith evaluation](https://docs.langchain.com/langsmith/evaluation)
- [LangSmith Agent Server](https://docs.langchain.com/langsmith/agent-server)
- [Agent Server API reference, including A2A and MCP endpoints](https://docs.langchain.com/langsmith/server-api-ref)
- [Self-hosted LangSmith deployment models](https://docs.langchain.com/langsmith/self-hosted)
- [LangGraph package metadata and license](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/pyproject.toml)

## Validation

This DAR is validated by repository inspection, official framework
documentation, the completed bounded implementation POC, and an explicit
weighted model. The POC validates shared-contract coexistence and the
LangGraph model/tool/session/memory/streaming slice; it does not claim MCP,
A2A, HTTP-service, durable checkpoint, restart/resume, multi-replica, or
performance benchmark validation. Re-score after a material ADK, LangChain,
LangGraph, OSA product, or deployment change, and review at least once per
major runtime release.
