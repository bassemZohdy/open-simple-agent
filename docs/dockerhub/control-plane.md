# Open Simple Agent Control Plane

The Control Plane image provides the agent and resource API and manages local demo deployments. The full app also needs the Control Panel image; the seeded child runtime uses the matching runtime image.

The current full-app release is **v0.1.5**. Download the [matching Docker Compose bundle](https://github.com/bassemZohdy/open-simple-agent/releases/tag/v0.1.5), then follow the [numbered Docker walkthrough](https://github.com/bassemZohdy/open-simple-agent/blob/main/docs/guides/docker-demo.md) and [real screenshot gallery](https://github.com/bassemZohdy/open-simple-agent/blob/main/docs/assets/screenshots/README.md).

The demo serves the API at `http://localhost:8000` and binds all published ports to loopback. It uses an explicitly enabled fake provider, whose `DEMO RESPONSE` is deterministic.

See the [deployment documentation](https://github.com/bassemZohdy/open-simple-agent/blob/main/docs/guides/deployment.md) for persistence and production setup.
