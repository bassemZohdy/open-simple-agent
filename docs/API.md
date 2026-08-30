# HTTP API Reference

This document describes routes implemented on `main` as reviewed on 2026-08-30.
Both applications are development-only: they have no authentication,
persistence, rate limits, or production error envelope.

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
| `POST` | `/agents` | Create a draft agent record, optionally from a built-in template or definition |
| `GET` | `/agents` | List records; supports one effective filter among `q`, `status`, `skill`, `runtime` |
| `GET` | `/agents/{agent_id}` | Get one record |
| `PATCH` | `/agents/{agent_id}` | Replace selected record fields |
| `POST` | `/agents/{agent_id}/versions` | Append a version using query parameters `version` and `change_summary` |
| `POST` | `/agents/{agent_id}/disable` | Set status to `disabled` |
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

`template` and `definition` are not declared mutually exclusive. If both are
provided, the template path wins. If neither is provided, the API creates a
record with no definition. These behaviors are tracked for correction.

The built-in templates are `generic`, `support`, and `research`. Template skill
names are not automatically registered in the Skill Catalog.

### Agent response

```json
{
  "agent_id": "generated-id",
  "name": "support-agent",
  "description": "Support agent",
  "status": "draft",
  "current_version": "1.0.0",
  "runtime": "adk",
  "skills": [],
  "labels": {
    "team": "service"
  }
}
```

The record's `skills` field is not currently derived from its definition, so it
may be empty even when the definition references skills.

### Current error limitations

Missing agent IDs produce 404 responses. Several other domain errors currently
escape as 500 responses, including unknown templates, duplicate names, and some
invalid filter values. API-hardening work must define 400/404/409/422 mappings
and a stable error body.

Resource catalog operations and deployment providers exist as Python classes
but are not exposed as routes.

## Runtime API

Application: `osa.runtimes.adk.api:runtime_app`

The app has no configuration bootstrap or lifespan initializer. Starting it
directly leaves the agent uninitialized, so invocation/readiness return 503.
Tests and embedding applications call `initialize_runtime(definition)` first.

### Routes

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/v1/invoke` | Invoke the single initialized agent |
| `GET` | `/v1/capabilities` | Return agent name, version, session flag, tools, and skills |
| `GET` | `/health/live` | Process liveness response |
| `GET` | `/health/ready` | Ready only when an agent has been initialized |

### Invoke request

```json
{
  "input": "Hello",
  "session_id": null,
  "user_id": "user-123",
  "metadata": {
    "channel": "web"
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

Runtime execution failures are currently returned inside a successful HTTP 200
response with `error` populated. A stable protocol must decide which failures
are domain results and which become HTTP errors.

### Current runtime behavior

- Programmatic initialization registers a fake default model and fake provider.
- One agent is stored in module-level state.
- Session data is in memory and not replica-safe.
- Request metadata is accepted but not used by model/tool policy.
- Capabilities are not yet an A2A Agent Card.
- Streaming and A2A endpoints are not implemented.
