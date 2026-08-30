FROM python:3.12-slim AS base

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY generic-agent/ generic-agent/
COPY runtimes/ runtimes/
COPY control-plane/ control-plane/

RUN uv sync --frozen --no-dev

# Base runtime image — intentionally has no ENTRYPOINT/CMD yet. The agent
# runtime HTTP service (Milestone 14, "Agent Runtime HTTP API") adds the
# entrypoint when there is a service to start; images built from this stage
# are not runnable on their own until then.
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=base /app/.venv /app/.venv
COPY --from=base /app /app

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --shell /bin/bash appuser
USER appuser
