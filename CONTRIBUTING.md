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
│   └── backend/      # Control Plane API (FastAPI)
├── generic-agent/    # Domain model and contracts
├── runtimes/
│   └── adk/          # Google ADK runtime
├── tests/            # Shared tests
└── docs/             # Documentation
```

The React Control Panel is planned but is not present in the repository.

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

- Update `README.md` when user-facing behavior or current status changes.
- Update `PROJECT_DEFINITION.md` only for product scope, principles, or target
  architecture changes.
- Update `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, and `docs/API.md` when
  implementation behavior changes.
- Update `TODO.md` when work is completed, added, reprioritized, or deferred.
- Update `CHANGELOG.md` for notable changes.
- Add an ADR for significant decisions, including protocol/provider/version
  choices and irreversible data or deployment boundaries.

Target capabilities must be labelled as planned. Do not describe a domain type
or catalog entry as a working integration without runtime behavior and tests.

`tests/unit/test_docs_examples.py` validates every `kind: Agent` YAML example in
`README.md` and `PROJECT_DEFINITION.md`.

## Release Process

All three packages are released together as one versioned release — packages
are not published independently. The release version is the `version` field,
kept identical across the workspace root and the three member manifests; it is
the single authoritative version source. `tests/unit/test_versioning.py`
fails when the manifests drift or when the installed distributions / FastAPI
application metadata report a different version.

Before releasing:

1. Bump `version` in `pyproject.toml` and the three member manifests to the
   same value and refresh `uv.lock`.
2. Move relevant changelog entries out of `Unreleased` into a dated
   `## [X.Y.Z] - YYYY-MM-DD` section.
3. Run the full local suite and merge only after CI is green on `main`.

Releases are automated by `.github/workflows/release.yml`. A maintainer can
either push the exact tag `vX.Y.Z` or dispatch the workflow from `main` with
`X.Y.Z`; manual dispatch creates the annotated tag after the build gates pass.
`scripts/release_validation.py` rejects a tag when the four manifests are not
lockstep or the dated changelog section is missing.

The workflow publishes:

- `osa-generic-agent`, `osa-adk-runtime`, and `osa-control-plane` wheel/sdist
  artifacts on the GitHub Release, with SHA-256 checksums and GitHub build
  provenance attestations.
- `ghcr.io/<owner>/<repo>-runtime:X.Y.Z` and
  `ghcr.io/<owner>/<repo>-control-plane:X.Y.Z`, plus the corresponding
  `latest` tags. Image names are normalized to lowercase for OCI compatibility.
- OCI provenance/SBOM metadata and GitHub artifact attestations for both
  images, followed by keyless Cosign signing using GitHub OIDC.

Release tags are immutable. To roll back a deployment, select a previously
published immutable version/digest rather than rebuilding or replacing an old
release. Automation for moving deployment/channel pointers back to a prior
release remains backlog work.
