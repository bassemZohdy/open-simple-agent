# Open Simple Agent runtime

The runtime image executes versioned agent bundles with the ADK backend. Use it for a standalone runtime or as the child runtime in the full Docker demo.

The current full-app release is **v0.1.5**. Download the [matching Docker Compose bundle](https://github.com/bassemZohdy/open-simple-agent/releases/tag/v0.1.5), then follow the [numbered Docker walkthrough](https://github.com/bassemZohdy/open-simple-agent/blob/main/docs/guides/docker-demo.md) and [real screenshot gallery](https://github.com/bassemZohdy/open-simple-agent/blob/main/docs/assets/screenshots/README.md).

The demo uses an explicitly enabled fake provider. Its `DEMO RESPONSE` is deterministic, not live model output. The bundle pins the runtime, Control Plane, and Control Panel to one release and publishes the runtime at `http://localhost:8081`.

See the [runtime and deployment documentation](https://github.com/bassemZohdy/open-simple-agent/blob/main/docs/guides/deployment.md) for non-demo configuration.
