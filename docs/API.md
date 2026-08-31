# HTTP API Reference

This document describes routes implemented on `main`. Both applications are
in-memory and have no authentication, persistence, or rate limits; both use
the stable OSA error envelope `{"error": {"code", "message"}}`.

## Control Plane API

Application: `osa.control_plane.backend.api:app`

Development command:

```bash
uv run uvicorn osa.control_plane.backend.api:app --reload
```

Interactive OpenAPI documentation is available at `/docs` while the service is
running.

### Routes

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/agents` | Create a draft agent record from a built-in template, an inline definition, or neither (explicit draft placeholder) |
| `GET` | `/agents` | List records; filters (`q`, `status`, `skill`, `runtime`) combine with AND; results are sorted and paginated |
| `GET` | `/agents/{agent_id}` | Get one record |
| `PATCH` | `/agents/{agent_id}` | Replace selected record fields; optional `expected_version` for optimistic concurrency |
| `POST` | `/agents/{agent_id}/versions` | Snapshot the current definition as a new immutable version |
| `POST` | `/agents/{agent_id}/activate` | Transition to `active` after validating the definition and its resource references |
| `POST` | `/agents/{agent_id}/disable` | Transition to `disabled` |
| `POST` | `/agents/{agent_id}/archive` | Transition to `archived` (terminal) |
| `DELETE` | `/agents/{agent_id}` | Delete a record |
| `GET` | `/health/live` | Process liveness response |
| `GET` | `/health/ready` | Always reports ready in the current implementation |

### Create request

```json
{
  "name": "support-agent",
  "description": "Support agent",
  "template": "support",
  "labels": {
    "team": "service"
  }
}
```

`template` and `definition` are mutually exclusive: providing both returns
422. With neither, the API creates an explicit draft placeholder without a
definition. A definition whose `metadata.name` differs from the request name
is rejected with 422.

The built-in templates are `generic`, `support`, and `research`. Template skill
names are not automatically registered in the Skill Catalog.

### Listing, pagination, and sorting

`GET /agents` query parameters:

| Parameter | Behavior |
|---|---|
| `status` | One of `draft`, `active`, `disabled`, `archived`; unknown values return 400 |
| `skill` / `runtime` / `q` | Combine with `status` (AND) |
| `sort_by` | `name` (default), `created_at`, or `updated_at`; unknown values return 422 |
| `order` | `asc` (default) or `desc` |
| `limit` | Page size, 1..200, default 50 |
| `offset` | Page offset, >= 0 |

The response reports `total` (count after filters, before pagination) plus
`limit` and `offset`.

### Lifecycle transitions

Allowed transitions: `draft -> active`, `draft -> archived`,
`active -> disabled`, `active -> archived`, `disabled -> active`,
`disabled -> archived`. `archived` is terminal. Invalid transitions return
400 with code `invalid_transition`. Activation requires a definition whose
model, tool, skill, MCP, and memory-policy references resolve in the resource
catalogs (422 otherwise).

### Versions

`POST /agents/{agent_id}/versions` takes a JSON body:

```json
{
  "version": "2.0.0",
  "change_summary": "Major update"
}
```

Each version is an immutable snapshot of the agent's definition at creation
time; later record updates never mutate it. Duplicate version identifiers
return 409.

### Optimistic concurrency

`PATCH /agents/{agent_id}` accepts `expected_version`; when it does not match
the record's current version the request fails with 409 (code `conflict`).

### Error envelope

Every error response uses:

```json
{
  "error": {
    "code": "conflict",
    "message": "Agent with name 'x' already exists"
  }
}
```

Documented codes: `not_found` (404), `conflict` (409), `bad_request` (400),
`invalid_transition` (400), `validation_error` (422). Resource catalog
operations and deployment providers exist as Python classes but are not
exposed as routes yet (P1.2/P1.5).

## Runtime API

Application: `osa.runtimes.adk.api:runtime_app`

Two ways to serve it:

- Service (production path): the `osa-runtime` CLI or
  `osa.runtimes.adk.service.create_runtime_app(bundle_path)` bootstraps from a
  deployment bundle during startup:

  ```bash
  OSA_BUNDLE=/app/config uv run osa-runtime --config ./my-bundle --port 8080
  ```

  Startup validates the bundle and resolves every secret before the process
  reports ready; an invalid bundle aborts startup. SIGTERM triggers uvicorn's
  graceful shutdown, which closes the runtime.

- Programmatic: call `initialize_runtime(definition)` and serve
  `runtime_app` directly (tests and embedding).

### Routes

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/v1/invoke` | Invoke the single initialized agent |
| `GET` | `/v1/capabilities` | Return agent name, version, session flag, tools, and skills |
| `GET` | `/health/live` | Process liveness response |
| `GET` | `/health/ready` | Ready only after successful bundle initialization |

### Invoke request

```json
{
  "input": "Hello",
  "session_id": null,
  "user_id": "user-123",
  "metadata": {
    "channel": "web",
    "tenant_id": "tenant-1"
  }
}
```

### Invoke response

```json
{
  "output": "Model response",
  "invocation_id": "generated-id",
  "session_id": "generated-or-existing-id",
  "error": null
}
```

### Sessions and errors

Session semantics (enforced by the OSA `SessionProvider`):

- Omitting `session_id` creates a new session; the response returns its
  server-issued stable ID.
- A caller-supplied unknown ID returns **404** (`session_not_found`) — unknown
  IDs are never silently created.
- A session is only accessible to its own `(agent_name, user_id, tenant_id)`
  owner; foreign access returns **403** (`session_access_denied`).
- Sessions carry a TTL and a bounded history when configured.

Model/timeout/limit failures are returned as HTTP 200 with `error` populated
(domain results); `invocation_timeout` and `iteration_limit_exceeded` carry
deterministic messages. Genuine upstream model failures map to **502** with
the `model_invocation_failed` code when raised to the HTTP layer.

### Current runtime behavior

- Invocation flows through the ADK `Runner`; tools execute through ADK-native
  function calling with declarations from `ToolDefinition.capabilities`.
- One agent is stored in module-level state, matching the bundle model.
- Session and memory data are in memory and not replica-safe (P1).
- The `fake` model provider requires explicit opt-in via
  `OSA_ALLOW_FAKE_PROVIDER=1` in service bootstraps.
- Capabilities are not yet an A2A Agent Card.
- Streaming and A2A endpoints are not implemented.
