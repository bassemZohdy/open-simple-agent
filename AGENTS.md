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
- `GenericAdkAgent` resolves `spec.tools`/`spec.skills` at construction (fails fast on missing refs) and executes tools via a transitional `TOOL_CALL <name> {json}` model protocol with `ToolDefinition.timeout_seconds` enforcement. ADK `LlmAgent`/`Runner` are built at construction (`osa.runtimes.adk.llm_agent`, exposed as `agent.llm_agent`/`agent.runner`); live invocation still flows through the injected `ModelProvider` until a real model is configured.
- `GenericAdkAgent` injects memory context only when `spec.memory.enabled` + a `MemoryProvider` are configured; explicit `remember()` writes memory, raw interactions are never auto-persisted.
- `DeploymentProvider` / `LocalDeploymentProvider` (`osa.control_plane.backend.deployment`) manage process lifecycle and are deliberately separate from `AgentRuntime`.
- FastAPI services: Control Plane API at `osa.control_plane.backend.api`, agent Runtime API at `osa.runtimes.adk.api` (both in-memory backed; no persistence yet).
- The runtime API requires programmatic `initialize_runtime()`; there is no
  external configuration bootstrap, lifespan initializer, or runnable image
  command yet.
- ADK `LlmAgent`/`Runner` objects are constructed, but current invocation still
  flows through `ModelProvider` and the transitional `TOOL_CALL` protocol.
- Current OSA session history is recorded but is not fed into later prompts;
  persistence, TTL, and ownership isolation are backlog items.
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
