# Configuration Reference

This document reflects `osa.generic_agent.config` on `main` as reviewed on
2026-08-30.

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
| `apiVersion` | string | `osa/v1alpha1` | Stored; supported-version enforcement is not implemented |
| `kind` | string | `Agent` | Stored; exact-kind enforcement is not implemented |
| `metadata.name` | string | required | Used for runtime metadata and ADK name derivation |
| `metadata.version` | string | `0.1.0` | No semantic-version validation |
| `metadata.description` | string | empty | Used in generic metadata |
| `metadata.labels` | map of string | empty | Stored as metadata |

`spec.description` is separate from `metadata.description`; the ADK `LlmAgent`
uses `spec.description`.

### Runtime references

| Path | Type | Default | Current behavior |
|---|---|---:|---|
| `spec.instruction` | string | empty | Included in the current generated prompt |
| `spec.model` | model reference or null | null | Model ID resolved from the catalog; absent/unknown falls back to `fake` |
| `spec.model.parameters` | map | empty | Accepted but not passed to the provider yet |
| `spec.mcps` | MCP references | empty | Accepted but not runtime-resolved yet |
| `spec.mcps[].tools_filter` | string list | empty | Accepted but not enforced yet |
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
| `spec.memory.policy` | string or null | null | Stored; policy catalog resolution is pending |
| `spec.memory.scope` | enum | `user` | `user`, `agent`, `tenant`, or `application` |
| `spec.memory.max_entries` | integer or null | null | Stored; not enforced |
| `spec.session.persistence` | boolean | false | Stored; not enforced |
| `spec.session.ttl_seconds` | integer or null | null | Stored; not enforced |
| `spec.a2a.enabled` | boolean | false | Stored; A2A is not implemented |
| `spec.runtime.timeout_seconds` | integer or null | null | Stored; invocation timeout is not enforced |
| `spec.runtime.max_iterations` | integer or null | null | Controls transitional tool-loop iterations; default behavior is 3 |

Positive/range validation for timeout, TTL, limits, and iterations is not yet
implemented. This is tracked in the backlog.

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

If an intermediate YAML value is a non-mapping, the override is skipped. In
particular, `OSA_MODEL_REF` does not replace `spec.model` when the YAML uses a
bare string model reference. Configuration bundle work must make this behavior
explicit and consistent.

## Secret references

Reusable resource definitions may contain:

```yaml
credential_ref:
  source: env
  key: provider-api-key
  env_var: PROVIDER_API_KEY
```

`SecretReference` stores metadata only. The current repository does not include
a secret resolver, so this does not yet inject a credential into a model or MCP
client.

## Resource definitions

Agent YAML refers to catalog names. Model, MCP, tool, skill, and memory-policy
catalog documents do not yet have a standard file format or loader. Until that
bootstrap is implemented, applications must construct and register these
objects in Python before creating the runtime agent.

## Precedence

The implemented precedence for agent fields is:

```text
Pydantic defaults < YAML < supported OSA_* environment variables
```

Secret resolution and layered catalog files are target behavior, not current
behavior.
