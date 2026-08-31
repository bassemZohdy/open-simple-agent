# syntax=docker/dockerfile:1

# Build stage: resolve the workspace with uv into a self-contained venv.
# No compiler or build tooling is copied into the runtime image.
FROM python:3.12-slim AS build

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY generic-agent/ generic-agent/
COPY runtimes/ runtimes/
COPY control-plane/ control-plane/

# The runtime service only needs the ADK runtime member (plus its workspace
# dependency); the litellm extra provides the production model adapter.
# --no-editable installs real wheels so no source tree is needed at runtime.
RUN uv sync --frozen --no-dev --no-editable --package osa-adk-runtime --extra litellm

# Runtime stage: non-root, arbitrary-UID friendly, externally configured.
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    # Default bundle location; override --config or this env var to change it.
    OSA_BUNDLE=/app/config \
    PYTHONDONTWRITEBYTECODE=1

# Fixed non-root UID with no home directory or login shell so the image runs
# under an arbitrary UID at runtime (mount the bundle readable by that UID).
RUN useradd --no-create-home --shell /usr/sbin/nologin --uid 10001 osa
USER 10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["osa-runtime"]
CMD ["--config", "/app/config", "--host", "0.0.0.0", "--port", "8080"]
