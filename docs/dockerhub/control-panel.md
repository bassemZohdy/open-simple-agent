# Open Simple Agent Control Panel

The Control Panel image serves the browser interface for agents, templates, resources, deployments, invocation, and logs. It needs a reachable Control Plane API; the full Docker demo configures that browser URL at startup.

The current full-app release is **v0.1.5**. Download the [matching Docker Compose bundle](https://github.com/bassemZohdy/open-simple-agent/releases/tag/v0.1.5), then follow the [numbered Docker walkthrough](https://github.com/bassemZohdy/open-simple-agent/blob/main/docs/guides/docker-demo.md) and [real screenshot gallery](https://github.com/bassemZohdy/open-simple-agent/blob/main/docs/assets/screenshots/README.md).

The demo opens the panel at `http://localhost:8080`, uses loopback ports, and invokes the seeded runtime at `http://localhost:8081`. The labeled `DEMO RESPONSE` is deterministic fake-provider output.
