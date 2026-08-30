FROM python:3.12-slim AS base

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY generic-agent/ generic-agent/
COPY runtimes/ runtimes/
COPY control-plane/ control-plane/

RUN uv sync --frozen --no-dev

# Development base image only. It intentionally has no ENTRYPOINT/CMD because
# the runtime API still lacks external configuration/lifespan bootstrap. Images
# built from this Dockerfile are not standalone agent services.
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=base /app/.venv /app/.venv
COPY --from=base /app /app

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --shell /bin/bash appuser
USER appuser
