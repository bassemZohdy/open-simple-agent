# Memory

## Project Overview
See @README.md for project overview. This is a Python project — see @CONTRIBUTING.md for setup and check commands (there is no package.json).

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

## Common Workflows
```bash
uv sync --all-packages   # setup
uv run pytest            # tests
uv run ruff format . && uv run ruff check .   # format + lint
uv run mypy .            # strict type check across all members
```
