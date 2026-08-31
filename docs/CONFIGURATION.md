# Configuration Reference

This document reflects `osa.generic_agent.config` and the deployment-bundle
loader (`osa.generic_agent.bundle`) on `main`.

## Loading and validation

`load_agent_definition()` accepts a YAML string or a `pathlib.Path`. It applies
supported environment overrides and validates the result as an immutable
Pydantic model. Unknown properties are rejected at every schema level.

```python
from pathlib import Path

from osa.generic_agent import load_agent_definition

definition = load_agent_definition(Path("agent.yaml"))
```

## Complete schema

```yaml
apiVersion: osa/v1alpha1
kind: Agent
metadata:
  name: example
  version: 0.1.0
  description: Example agent
  labels:
    team: platform
spec:
  description: Runtime-facing description
  instruction: Assist the user.
  model:
    ref: default
    parameters:
      temperature: 0.2
  mcps:
    - ref: knowledge
      tools_filter:
        - search
  tools:
    - ref: calculator
  skills:
    - ref: arithmetic
  memory:
    enabled: false
    policy: null
    scope: user
    max_entries: null
  session:
    persistence: false
    ttl_seconds: null
  a2a:
    enabled: false
  runtime:
    timeout_seconds: null
    max_iterations: null
```

## Fields

### Root and metadata

| Path | Type | Default | Current behavior |
|---|---|---:|---|
| `apiVersion` | string | `osa/v1alpha1` | Must equal `osa/v1alpha1`; other values are rejected |
| `kind` | string | `Agent` | Must equal `Agent`; other values are rejected |
| `metadata.name` | string | required | Used for runtime metadata and ADK name derivation |
| `metadata.version` | string | `0.1.0` | No semantic-version validation |
| `metadata.description` | string | empty | Used in generic metadata |
| `metadata.labels` | map of string | empty | Stored as metadata |

`spec.description` is separate from `metadata.description`; the ADK `LlmAgent`
uses `spec.description`.

### Runtime references

| Path | Type | Default | Current behavior |
|---|---|---:|---|
| `spec.instruction` | string | empty | Used as the ADK `LlmAgent` instruction |
| `spec.model` | model reference or null | null | Resolved from the catalog; an unknown reference fails fast, an absent reference uses the catalog default (deterministic mode only when no default exists) |
| `spec.model.parameters` | map | empty | Per-agent generation overrides; override `ModelDefinition.runtime_settings` |
| `spec.mcps` | MCP references | empty | Resolved against the catalog; tools discovered and invoked at runtime (ADR-002) |
| `spec.mcps[].tools_filter` | string list | empty | Agent-level allowlist of server tool names (intersects the server definition's filter) |
| `spec.tools` | tool references | empty | Definitions and implementations resolve at construction |
| `spec.skills` | skill references | empty | Definitions resolve at construction as metadata |

Model, MCP, tool, and skill references accept either a bare string or an object:

```yaml
tools:
  - calculator
  - ref: lookup
```

### Memory, session, A2A, and runtime

| Path | Type | Default | Current behavior |
|---|---|---:|---|
| `spec.memory.enabled` | boolean | false | Enables search-based context only when a provider is injected |
| `spec.memory.policy` | string or null | null | Must resolve in the bundle when memory is enabled |
| `spec.memory.scope` | enum | `user` | `user`, `agent`, `tenant`, or `application` |
| `spec.memory.max_entries` | integer or null | null | Must be >= 1 when set; enforcement pending (P1.4) |
| `spec.session.persistence` | boolean | false | Stored; persistent providers pending (P1) |
| `spec.session.ttl_seconds` | integer or null | null | Must be > 0 when set; expired sessions are deleted on access |
| `spec.session.max_history_messages` | integer | 20 | Bounds the per-session conversation history |
| `spec.a2a.enabled` | boolean | false | Stored; A2A is not implemented |
| `spec.runtime.timeout_seconds` | integer or null | null | Must be > 0 when set; the invocation is cancelled with `invocation_timeout` when exceeded |
| `spec.runtime.max_iterations` | integer or null | null | Must be >= 1 when set; caps ADK function-call rounds (default 3), fails with `iteration_limit_exceeded` |

Timeouts, TTLs, limits, and iterations carry positive/range validation
(`timeout_seconds > 0`, `ttl_seconds > 0`, `max_iterations >= 1`,
`max_entries >= 1`, model `temperature` in 0..2, `top_p` in 0..1, token
limits > 0, MCP connection options likewise).

## Environment overrides

Only these environment variables are recognized:

| Variable | Target field | Parsing |
|---|---|---|
| `OSA_AGENT_NAME` | `metadata.name` | string |
| `OSA_AGENT_VERSION` | `metadata.version` | string |
| `OSA_AGENT_DESCRIPTION` | `metadata.description` | string |
| `OSA_MODEL_REF` | `spec.model.ref` | string |
| `OSA_MEMORY_ENABLED` | `spec.memory.enabled` | boolean |
| `OSA_SESSION_PERSISTENCE` | `spec.session.persistence` | boolean |

Boolean values are case-insensitive. Accepted true values are `1`, `true`,
`yes`, and `on`; false values are `0`, `false`, `no`, and `off`.

`OSA_MODEL_REF` applies to bare-string model references by replacing them
outright: `model: default` plus `OSA_MODEL_REF=other` resolves to
`{ref: other}`. Environment overrides always win over the file value.

If an intermediate YAML value is some other non-mapping, the override is
skipped and schema validation reports the underlying problem.

## Secret references

Reusable resource definitions may contain:

```yaml
credential_ref:
  source: env
  key: provider-api-key
  env_var: PROVIDER_API_KEY
```

`SecretReference` stores metadata only; resolved values are never stored on
models, returned in responses, logged, or embedded in error messages.

`EnvironmentSecretResolver` (`osa.generic_agent.secret`) resolves secrets from
environment variables: `source` must be `env`, and the variable is `env_var`
when set, otherwise `key` itself. Unresolvable secrets raise
`SecretResolutionError`, which identifies the reference - never the value.
Service bootstraps resolve every bundle secret before reporting ready.

## Deployment bundles

A deployment bundle is one agent plus the catalog resources it references
(`osa.generic_agent.bundle`). Two layouts are supported.

A single `AgentBundle` document:

```yaml
apiVersion: osa/v1alpha1
kind: AgentBundle
metadata:
  name: my-bundle
agent:
  apiVersion: osa/v1alpha1
  kind: Agent
  metadata:
    name: greeter
  spec:
    instruction: Say hello.
    model:
      ref: default
models:
  - name: default
    provider: litellm
    model_id: openai/gpt-4o-mini
```

A directory layout (see `examples/smoke-bundle`):

```text
my-bundle/
├── agent.yaml          # standard AgentDefinition document (required)
├── bundle.yaml         # optional bundle metadata (name, version, labels)
├── models/*.yaml       # resource envelopes
├── tools/*.yaml
├── skills/*.yaml
├── mcps/*.yaml
└── memory-policies/*.yaml
```

Each resource file is an envelope with `apiVersion: osa/v1alpha1`, a `kind`
(`Model`, `Tool`, `Skill`, `Mcp`, or `MemoryPolicy`), and the domain
definition under `spec`:

```yaml
apiVersion: osa/v1alpha1
kind: Model
spec:
  name: default
  provider: litellm
  model_id: openai/gpt-4o-mini
  credential_ref:
    source: env
    key: OPENAI_API_KEY
```

Loading (`load_bundle`) is fail-fast: unknown resource kinds, unsupported
apiVersions, duplicate resource names, and agent references to missing
resources all raise deterministic `BundleError` subclasses before the bundle
is usable. Native tool implementations ship with the runtime (see
`osa.runtimes.adk.service.BUILTIN_TOOLS`); an agent referencing a tool
definition without an available implementation fails at construction.

## MCP runtime behavior

MCP servers referenced by an agent connect lazily at invocation time
(`osa.runtimes.adk.mcp_client`, ADR-002) using the official `mcp` SDK.

- **Transports:** `stdio` (uses `command`/`args`/`env`) and
  `streamable_http` (uses `endpoint`). Legacy `sse` is not supported at
  runtime and fails with `mcp_transport_not_supported`.
- **Credentials:** `credential_ref` resolves through the `SecretResolver` at
  connect time; stdio injects the value into the subprocess environment
  (`env_var` or `key`), HTTP sends `Authorization: Bearer <value>`. Values
  are never stored or logged.
- **Options:** `timeout_seconds` bounds connection and call attempts;
  `max_retries`/`retry_delay_seconds` bound transient failures;
  `tls_verify` disables certificate verification for HTTP servers;
  `max_response_bytes` caps tool results (excess raises
  `mcp_response_too_large`).
- **Filtering and namespacing:** the server definition's `tools_filter` and
  the agent reference's `tools_filter` intersect; tools are exposed as
  `<server>_<tool>` with origin metadata preserved.
- **Failures are deterministic:** a server that cannot connect, authorize,
  answer in time, or stay within limits produces stable OSA errors
  (`mcp_connection_failed`, `mcp_tool_failed`, `mcp_response_too_large`) —
  the invocation fails with a clear message rather than silently losing the
  tools.

## Precedence

The implemented precedence for agent fields is:

```text
Pydantic defaults < YAML < supported OSA_* environment variables
```

Generation settings use explicit precedence:

```text
ModelDefinition.runtime_settings (catalog defaults) < ModelRef.parameters (per-agent overrides)
```

Bundle secrets are resolved before service readiness; a bundle whose secrets
cannot be resolved never starts.
