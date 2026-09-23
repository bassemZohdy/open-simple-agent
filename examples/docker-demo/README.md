# Docker demo

For the complete numbered walkthrough and recovery guidance, see the [Docker end-user walkthrough](../../docs/guides/docker-demo.md). The [capture script](../../docs/guides/docker-demo-capture.md) defines the screenshot and optional video sequence.

This bundle runs the Open Simple Agent Control Plane, Control Panel, and one
deterministic local runtime using Docker Compose. It is intended for screenshots
and first-use exploration. The fake provider is enabled explicitly and every
sample response is prefixed with `DEMO RESPONSE`; no paid model or API key is
used.

## Prerequisites

- Docker Desktop or Docker Engine with the Compose v2 plugin.
- A browser.
- Ports 8000, 8080, and 8081 available on loopback (or values changed in `.env`).

No Python, Node.js, or source checkout is required after downloading this
directory. The `osa-docker-demo-<VERSION>.tar.gz` GitHub Release asset includes
an `.env.example` pinned to that release's image tags. The repository copy
defaults to `latest` for development; use images from one release when recording
a demo.

## Start

```bash
cp .env.example .env
docker compose pull
docker compose up -d control-plane control-panel
docker compose --profile seed run --rm seed
```

Wait until the seed command prints a deployment response, then open
[http://localhost:8080](http://localhost:8080). The Control Plane API is
available at [http://localhost:8000](http://localhost:8000), and the seeded
runtime is exposed at [http://localhost:8081](http://localhost:8081).

In the Control Panel, open **Agents**, select `docker-demo-agent`, inspect its
model, deployment status, logs, and invoke it with:

```
What is running inside this Docker demo?
```

The response is deterministic and starts with `DEMO RESPONSE`. The runtime
port is fixed only for this single-agent demo so the browser can reach the
child runtime through Docker's published port. Do not deploy a second local
agent
until a separate runtime port is configured.

## Stop and reset

```bash
docker compose down --remove-orphans
```

The demo keeps no named volume: Control Plane state and the sample deployment
are process-local and disappear when the containers are removed. Start and
seed again for a clean run. Use `docker compose logs control-plane` when
diagnosing readiness or deployment errors.

If a port is occupied, change the three port values in `.env`; the runtime
port must be the same host and container value because it is selected by the
Control Plane. The generated browser URL always uses
`http://localhost:<OSA_RUNTIME_PORT>`.

## Live model

For a real model, create a separate environment file with a provider-specific
resource and secret configuration, set `OSA_ALLOW_FAKE_PROVIDER=0`, and use a
versioned runtime image. Never commit API keys or put credentials in the agent
definition. The deterministic seed is intentionally kept separate from live
model setup.
