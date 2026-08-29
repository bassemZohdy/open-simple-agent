# Contributing to Open Simple Agent

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
```

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

## Pull Requests

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
