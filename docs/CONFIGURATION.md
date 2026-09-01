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
| `spec.memory.enabled` | boolean | false | Enables search-based context only when a provider is configured |
| `spec.memory.policy` | string or null | null | Must resolve in the bundle when memory is enabled; an attached policy is authoritative for scope, limits, and retention, and `enabled: false` on the policy disables memory |
| `spec.memory.scope` | enum | `user` | `user`, `agent`, `tenant`, or `application` (used when no policy is attached) |
| `spec.memory.max_entries` | integer or null | null | Per-scope cap; oldest entries are evicted beyond it (used when no policy is attached) |
| `spec.session.persistence` | boolean | false | Stored; persistent session providers pending (P1) |
| `spec.session.ttl_seconds` | integer or null | null | Must be > 0 when set; expired sessions are deleted on access |
| `spec.session.max_history_messages` | integer | 20 | Bounds the per-session conversation history |
| `spec.a2a.enabled` | boolean | false | Enables the runtime Agent Card and JSON-RPC A2A routes when the optional A2A extra is installed |
| `spec.runtime.timeout_seconds` | integer or null | null | Must be > 0 when set; the invocation is cancelled with `invocation_timeout` when exceeded |
| `spec.runtime.max_iterations` | integer or null | null | Must be >= 1 when set; caps ADK function-call rounds (default 3), fails with `iteration_limit_exceeded` |

## Memory runtime behavior

Scope IDs are derived from the invocation context: `user` -> caller ID,
`agent` -> agent name, `tenant` -> `tenant_id` request metadata, and
`application` -> the deployment constant. Entries are never visible across
scope IDs or scopes.

When `spec.memory.policy` references a policy, that policy is authoritative:
its `scope`, `max_entries`, and `retention_days` replace the spec-level
fields, and `enabled: false` disables memory for the agent (writes raise,
reads return nothing). Limits are enforced after every write: the oldest
entries beyond `max_entries` are evicted per `(scope, scope_id)`, and entries
not updated within `retention_days` are purged.

Extraction is explicit — `remember()` only; raw interactions are never
persisted automatically. `MemoryPolicy.auto_extract` is reserved for a future
opt-in extraction pipeline (ADR-003).

## Memory persistence

By default memory is in-memory (single process, lost on restart). Setting
`OSA_MEMORY_DATABASE_URL` (e.g. `postgresql+asyncpg://user:pass@host/db`)
selects the PostgreSQL provider (ADR-003, `osa-adk-runtime[postgres]` extra):
entries survive restarts, are shared across replicas, and the runtime
verifies connectivity and its schema at startup — an unreachable database
aborts boot before readiness.

Timeouts, TTLs, limits, and iterations carry positive/range validation
(`timeout_seconds > 0`, `ttl_seconds > 0`, `max_iterations >= 1`,
`max_entries >= 1`, model `temperature` in 0..2, `top_p` in 0..1, token
limits > 0, MCP connection options likewise).

## Agent-definition environment overrides

These environment variables override fields in an agent definition:

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

## HTTP authentication

Control Plane and runtime HTTP applications share
`osa.generic_agent.auth`. Authentication is disabled by default for local
development. Set `OSA_AUTH_MODE=required` in a deployed service to require a
signed JWT Bearer token on every endpoint except liveness, readiness, and
OpenAPI discovery. `optional` validates a token when one is supplied but
permits anonymous requests. Required mode fails configuration if issuer,
audience, or JWKS URL is missing.

OSA validates the JWT locally against the configured JWKS document. The
supported signing algorithms are RS256/384/512 and ES256/384/512. The token
must contain `exp`, `iss`, and `sub`, and its issuer/audience must match the
configured values. `OSA_AUTH_REQUIRED_SCOPES` is a space-separated list; a
missing scope returns 403. JWKS is cached and refreshed when an unknown key ID
is encountered.

| Variable | Meaning | Default |
|---|---|---:|
| `OSA_AUTH_MODE` | `disabled`, `optional`, or `required` | `disabled` |
| `OSA_AUTH_ISSUER` | Expected JWT `iss` and OIDC issuer URL | unset |
| `OSA_AUTH_AUDIENCE` | Expected JWT `aud` value | unset |
| `OSA_AUTH_JWKS_URL` | HTTP JSON Web Key Set URL | unset |
| `OSA_AUTH_REQUIRED_SCOPES` | Required scopes separated by spaces | empty |
| `OSA_AUTH_CLOCK_SKEW_SECONDS` | JWT clock leeway, 0..300 seconds | `30` |
| `OSA_AUTH_JWKS_TIMEOUT_SECONDS` | JWKS request timeout, >0 and <=30 seconds | `2.0` |
| `OSA_AUTH_JWKS_CACHE_SECONDS` | JWKS cache lifetime, >0 and <=86400 seconds | `300` |

The current authorization behavior is intentionally narrow: the validated
token subject is the runtime caller identity when `user_id` is omitted, and a
different supplied `user_id` is rejected. Role/tenant/resource policies, audit
events, API-key and mTLS adapters, and A2A security-scheme enforcement remain
P2.2 work. Token material is never logged or retained after validation.

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
