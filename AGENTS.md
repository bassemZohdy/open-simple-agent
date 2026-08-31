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
- `DeploymentProvider` / `LocalDeploymentProvider` (`osa.control_plane.backend.deployment`) manage process lifecycle and are deliberately separate from `AgentRuntime`.
- FastAPI services: Control Plane API at `osa.control_plane.backend.api`, agent Runtime API at `osa.runtimes.adk.api`. The runtime service path is the `osa-runtime` CLI / `osa.runtimes.adk.service.create_runtime_app` (bundle-driven lifespan; SIGTERM shuts down cleanly). Both apps are in-memory backed; no persistence yet.
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
