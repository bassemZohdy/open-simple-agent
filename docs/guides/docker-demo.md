# Docker end-user walkthrough

This guide uses the checked-in [Docker demo bundle](../../examples/docker-demo).
It runs the released Control Plane and Control Panel images plus one local
runtime. The demo enables OSA's explicit fake provider, so responses begin with
`DEMO RESPONSE` and do not represent a live model.

Use one immutable image version for all three services when recording or
capturing screenshots. The `latest` tag is convenient for exploration but is
mutable.

## 1. Check prerequisites

Install Docker Desktop or Docker Engine with the Compose v2 plugin. No Python,
Node.js, or OSA source checkout is needed.

From the repository or downloaded demo directory:

```bash
cd examples/docker-demo
docker compose version
cp .env.example .env
docker compose config >/dev/null
```

The demo binds only to loopback and uses host ports 8000 (Control Plane), 8080
(Control Panel), and 8081 (child runtime). Change those values in `.env` if
they are occupied. Keep the runtime port equal on both sides of the mapping.

## 2. Pull and start the services

```bash
docker compose pull
docker compose up -d control-plane control-panel
docker compose ps
```

Expected result: both services become `running (healthy)`. Verify readiness:

```bash
curl -fsS http://localhost:8000/health/ready
curl -fsS http://localhost:8080/health/live
```

The first command returns a JSON readiness response and the second returns
`ok`.

## 3. Seed the sample agent

```bash
docker compose --profile seed run --rm seed
```

The seed command creates the `default` fake model resource, creates or reuses
`docker-demo-agent`, activates it, deploys it, and prints the deployment JSON.
It is safe to run again after a transient failure.

Expected result: the deployment has status `running` and an invoke URL based on
`http://localhost:8081`.

## 4. Open the Control Panel

Open [http://localhost:8080](http://localhost:8080). Choose **Agents** and
select `docker-demo-agent`.

Inspect:

- the agent status and current version;
- the model resource named `default`;
- the deployment status and advertised invoke URL;
- the deployment logs.

The browser calls the Control Plane at the host URL in `OSA_API_BASE_URL`.
Do not use the Compose service name `control-plane` as that value: it is
reachable only inside the Docker network, while the browser runs on the host.

## 5. Invoke the agent

From the agent's deployment view, invoke the runtime with:

```
What is running inside this Docker demo?
```

Expected result: the runtime returns a short response beginning with
`DEMO RESPONSE`. This proves the browser reached the deployed child runtime
through the published 8081 port. The response is deterministic and does not
use an API key.

For a direct smoke check, use the runtime endpoint shown by the deployment:

```bash
curl -fsS -X POST http://localhost:8081/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input":"What is running inside this Docker demo?"}'
```

## 6. Observe status and logs

Refresh the deployment status after an invocation. Use the Control Panel logs
view, or:

```bash
docker compose logs --tail=100 control-plane
```

A failed invocation must show an error state or error response. Do not use a
successful health check as evidence that an invocation succeeded.

## 7. Restart and stop

Use the deployment actions in the Control Panel to restart and then stop the
sample deployment. Verify the status after each action. The Control Plane and
Control Panel remain available while the child runtime is stopped.

To stop the complete demo:

```bash
docker compose down --remove-orphans
```

The demo has no named database volume. Control Plane records and the
process-local deployment disappear when containers are removed. Run the start
and seed commands again for a clean demonstration.

## Recovery

- **Port is occupied:** edit `.env`, run `docker compose down`, then start
  again. The Control Panel API URL follows `OSA_CONTROL_PLANE_PORT`; the
  runtime invoke URL follows `OSA_RUNTIME_PORT`.
- **Control Plane is not healthy:** run
  `docker compose logs control-plane`. Check that the image is available and
  that the three host ports are free.
- **Seed fails to find the model or agent:** remove the containers with
  `docker compose down --remove-orphans`, start them again, and rerun the
  seed command. The seed script is deterministic and idempotent.
- **Control Panel is blank or reports a network error:** inspect
  `OSA_API_BASE_URL` in `.env`. It must be a URL reachable from the browser,
  normally `http://localhost:<OSA_CONTROL_PLANE_PORT>`.
- **Runtime invocation reports a CORS error:** ensure the Control Panel port
  matches `OSA_CONTROL_PANEL_PORT` and that the runtime port mapping matches
  `OSA_RUNTIME_PORT`. Restart and reseed after changing `.env`.
- **401 or 403:** the demo runs with authentication disabled. A protected
  deployment requires the deployment's bearer-token/OIDC configuration and
  permissions; do not put a token in a URL or commit it.
- **Live provider credentials are missing:** the deterministic demo does not
  need credentials. A live provider requires a separate resource/secret setup;
  never add a key to `agent.yaml`, Compose files, screenshots, or video.

## Switching to a live model

Keep the deterministic demo for first-use documentation. To test a live model,
prepare a separate environment and model resource using the deployment and
security guides, configure the provider credential through an environment or
secret reference, set `OSA_ALLOW_FAKE_PROVIDER=0`, and use a versioned runtime
image. Repeat the readiness, deploy, invoke, and log checks before capturing
live-provider media. Label live output separately from the deterministic demo.
