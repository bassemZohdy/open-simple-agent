# Contributing to Open Simple Agent

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync --all-packages
```

`--all-packages` is required: the workspace root has no runtime dependencies of
its own, so a bare `uv sync` installs none of the three members' dependencies.

## Running Checks

```bash
# Format
uv run ruff format .

# Lint
uv run ruff check .

# Type check
uv run mypy .

# Tests
uv run pytest
```

## Project Structure

```text
open-simple-agent/
├── control-plane/
│   ├── backend/      # Control Plane API (FastAPI)
│   └── ui/           # Control Panel UI (React)
├── generic-agent/    # Domain model and contracts
├── runtimes/
│   └── adk/          # Google ADK runtime
├── tests/            # Shared tests
└── docs/             # Documentation
```

## Architecture Decision Records

ADRs are stored in `docs/adrs/`. See [ADR template](docs/adrs/000-template.md).

## Branches and Pull Requests

Maintainers may commit directly to `main`; every push runs CI, and `main` is
expected to stay green (lint, typecheck, tests).

External contributions:

1. Fork the repository
2. Create a feature branch
3. Run all checks (`ruff format`, `ruff check`, `mypy`, `pytest`)
4. Submit a PR with a clear description
5. Link related issues

## Code Style

- Follow existing patterns
- Use type annotations
- Keep functions focused
- Write tests for new functionality
- Use `ruff format` for formatting

## Testing

Tests are organized by type:

- `tests/unit/` — Unit tests for individual components
- `tests/integration/` — Integration tests for APIs and runtime

Run specific test categories:

```bash
# Unit tests only
uv run pytest tests/unit/

# Integration tests only
uv run pytest tests/integration/

# Tests matching a pattern
uv run pytest -k "tool or skill"
```

## Type Checking

The project uses strict mypy checking with no exemptions for first-party code:

```bash
# Check entire project
uv run mypy .

# Check specific module
uv run mypy generic-agent/src/osa/generic_agent
```

## Documentation

- Update README.md for user-facing changes
- Update PROJECT_DEFINITION.md for architectural changes
- Update TODO.md when completing milestones
- Update CHANGELOG.md for all notable changes
- Add ADRs for significant architectural decisions

## Release Process

1. Update CHANGELOG.md with release notes
2. Update version in pyproject.toml files
3. Create a git tag
4. Push to main (CI will run)
