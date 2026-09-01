# Memory

## Project Overview
See @README.md for current capabilities and @PROJECT_DEFINITION.md for target
scope. This is a Python project — see @CONTRIBUTING.md for setup and check
commands (there is no package.json or Control Panel UI yet).

## Code Style Guidelines
- Use descriptive variable names
- Follow existing patterns in the codebase
- Extract complex conditions into meaningful boolean variables

## Architecture Notes
- uv workspace with three members (`generic-agent`, `runtimes/adk`, `control-plane/backend`) sharing one PEP 420 namespace package `osa`. Never add `__init__.py` at namespace levels (`src/osa/`, `src/osa/runtimes/`, `src/osa/control_plane/`) — it breaks cross-member imports.
- Use `uv sync --all-packages`; a bare `uv sync` installs none of the members' dependencies.
- mypy runs strict with **no** exemptions for `osa.*`; `mypy_path` + `explicit_package_bases` in the root `pyproject.toml` make `uv run mypy .` resolve all members. If a third-party dep lacks stubs, scope `ignore_missing_imports` to that module only.
- Agent definition YAML accepts bare strings for catalog refs (`- calculator` ≡ `- ref: calculator`) for `model`, `mcps`, `tools`, `skills`.
- Docs are contract: `tests/unit/test_docs_examples.py` validates every `kind: Agent` YAML block in README.md / PROJECT_DEFINITION.md — update code and docs together.
- `MemoryScope` lives in `generic_agent/config.py` (`memory.py` re-exports it); don't re-define domain enums.
- Stable error types live in `generic_agent/errors.py` (`OsaError` hierarchy + `error_payload` wire schema); HTTP layers map them, never let them escape as 500s.
- Deployment bundles: `osa.generic_agent.bundle` (`load_bundle`, `build_catalogs`) loads an `AgentBundle` document or `agent.yaml` + resource directories; all validation (duplicates, unknown kinds/refs, secrets) fails fast before readiness. `osa.generic_agent.secret` holds the `SecretResolver` contract; resolved values must never appear in models, responses, logs, or exceptions.
- Sessions: `osa.generic_agent.session.SessionProvider` enforces ownership (`agent_name`, `user_id`, `tenant_id`), TTL, and bounded history; unknown caller-supplied session IDs are rejected. `osa.runtimes.adk.session_service.OsaAdkSessionService` maps ADK sessions onto the provider so bounded history reaches the model.
- `GenericAdkAgent` resolves `spec.tools`/`spec.skills`/`spec.model` at construction (missing refs fail fast — no silent model fallback) and invokes through the ADK `Runner` with ADK-native function calling. Tool declarations come from `ToolDefinition.capabilities`; `runtime.timeout_seconds`/`max_iterations` are enforced around the run. Models are built via `osa.runtimes.adk.model_adapter` (litellm = production, fake = explicit test-only opt-in via `OSA_ALLOW_FAKE_PROVIDER=1`).
- `GenericAdkAgent` injects memory context only when `spec.memory.enabled` + a `MemoryProvider` are configured; explicit `remember()` writes memory, raw interactions are never auto-persisted (`auto_extract` is reserved). A referenced `MemoryPolicy` is authoritative for scope/limits/retention; a disabled policy blocks writes. Scope IDs come from `memory_scope_id()`; limits/retention enforce via `MemoryProvider.enforce()` after writes and before reads. Persistence: `OSA_MEMORY_DATABASE_URL` selects `osa.runtimes.adk.postgres_memory.PostgresMemoryProvider` (ADR-003); tests need `OSA_TEST_DATABASE_URL` (CI has a Postgres service).
- MCP runtime: `osa.runtimes.adk.mcp_client` (pool + keeper-task connections; stdio + streamable_http only — SSE rejected) and `osa.runtimes.adk.mcp_toolset.OsaMcpToolset` (ADK toolset, namespaced `<server>_<tool>` tools, filters intersect server + agent levels). ADK resolves toolsets fail-open, so `GenericAdkAgent.invoke` pre-flights MCP connections to fail deterministically. anyio CMs must enter/exit in one task — never move MCP session contexts across tasks.
- `DeploymentProvider` / `LocalDeploymentProvider` (`osa.control_plane.backend.deployment`) manage process lifecycle and are deliberately separate from `AgentRuntime`. The local provider captures bounded logs (thread-drained ring buffers), probes `health_check_url` during startup, and keeps restarts on the same deployment id. `DeploymentService` (`deployment_service.py`) exports agent bundles and synthesizes launch commands from `OSA_DEPLOY_COMMAND_TEMPLATE` — never accept process commands from API input. Deployment intent persists via `DeploymentRecordRepository` (PG impl + migration 0002).
- FastAPI services: Control Plane API at `osa.control_plane.backend.api` (in-memory module app; `osa.control_plane.backend.service.create_control_plane_app` selects PG via `OSA_CONTROL_PLANE_DATABASE_URL`), agent Runtime API at `osa.runtimes.adk.api` with the `osa-runtime` CLI / `create_runtime_app` (bundle-driven lifespan; SIGTERM shuts down cleanly).
- Resource APIs (P1.2): `osa.control_plane.backend.resources_api` exposes `/resources/{kind}` CRUD + import/export with write-through to `ResourceDefinitionRepository`. Domain catalogs have public `delete()` methods and `ResourceCatalogs` uses `register_*`/`has_*`/`delete_*` — never mutate catalog internals. Deletion runs a reference-usage check against agent definitions (409 when referenced). `credential_ref` responses are redacted to source/key/env_var only.
- A2A (ADR-005): `osa.runtimes.adk.a2a` attaches card + JSON-RPC routes when `spec.a2a.enabled` (`maybe_attach_a2a`; env `OSA_A2A_URL`); client utilities (`resolve_agent_card`, `invoke_remote_agent`, `RemoteA2aError`) live in `osa.generic_agent.a2a_client` so the Control Plane can use them without importing the runtime. The A2A executor enqueues the initial Task event itself (1.x consumer requirement) and maps context ids to OSA sessions. External agents (`external_agents.py`) are never deployable.
- Control Plane persistence (ADR-004): routes go through `AgentRepository`; in-memory (`InMemoryAgentRepository` over `AgentCatalog`) is the default, `PostgresAgentRepository` when a DSN is set. Both raise the same typed errors (`DuplicateAgentError`, `DuplicateVersionError`, `InvalidTransitionError`, `ConcurrentUpdateError`). Schema is Alembic-owned: `osa-cp-migrate` applies it; NEVER auto-migrate at startup and never add columns without a migration (`control-plane/backend/src/osa/control_plane/backend/migrations/`). Resource definitions persist as kind+JSONB via `ResourceDefinitionRepository` and materialize into `ResourceCatalogs` at startup.
- The runtime container image (`Dockerfile`) has an `ENTRYPOINT`/`CMD`, runs non-root, and is smoke-tested in CI (`examples/smoke-bundle`).
- Package versions are one lockstep release (root + three manifests), enforced by `tests/unit/test_versioning.py`; bump all four together and refresh `uv.lock`.
- Don't write `x or Default()` for catalogs/providers/managers — they define `__len__`, so an empty instance is falsy; use `x if x is not None else Default()`.
- `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, and `docs/API.md` document
  current behavior. Keep planned behavior in `PROJECT_DEFINITION.md`/`TODO.md`.
- Open Simple Agent is independent from the Micro-Agents project. Do not infer
  shared architecture, dependencies, compatibility, or interoperability.

## Common Workflows
```bash
uv sync --all-packages   # setup
uv run pytest            # tests
uv run ruff format . && uv run ruff check .   # format + lint
uv run mypy .            # strict type check across all members
```
