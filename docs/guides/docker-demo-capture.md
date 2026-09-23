# Docker demo capture script

This script keeps screenshots and the optional video on the same reproducible
flow. Capture only synthetic data from a versioned Docker image set.

## Capture metadata

Record these values in the asset directory:

- OSA image tags and immutable digests for Control Plane, Control Panel, and runtime;
- Git commit or release tag;
- operating system and Docker/Compose versions;
- browser, locale, viewport, and device-pixel ratio;
- values used for the three host ports;
- seed command and exact prompt;
- capture date in UTC.

Useful commands:

```bash
docker compose images
docker image inspect <image>:<tag> --format '{{index .RepoDigests 0}}'
docker compose config
```

## Screenshots

Use a clean browser profile and the same synthetic `docker-demo-agent` data.
Capture in this order:

1. Control Panel **Agents** list showing `docker-demo-agent` active.
2. Agent detail/version view showing the model and deployment.
3. Resources view showing the `default` fake model.
4. Deployment view showing **running** and the invoke URL.
5. Invocation view with the prompt and the `DEMO RESPONSE` result.
6. Logs/status view after the successful invocation.
7. The Arabic locale in RTL layout with the same agent.
8. A narrow viewport showing the responsive navigation and deployment result.

Save optimized images under `docs/assets/screenshots/` with names such as
`01-agents-en.png` and `07-agent-ar.png`. Each image needs a caption,
alt text, release/version metadata, and a link to the walkthrough step. Do not
capture tokens, credentials, personal data, or unrelated browser content.

The [v0.1.5 gallery](../assets/screenshots/README.md) and
[capture metadata](../assets/screenshots/metadata-v0.1.5.json) are the current
reference. Before a later release, repeat the clean bundle run and compare
every image with the current UI, prompt, and output label. Refresh stale
images and metadata together. If a video exists, verify its steps and links
against the new flow before keeping it in the release notes.

## Optional video

Use the same order and prompt for a 2–4 minute recording:

1. State that OSA runs agents from configuration and that this recording uses
   the deterministic Docker demo.
2. Pull/start the three services and show readiness.
3. Open the panel and select `docker-demo-agent`.
4. Show model/resources, deployment status, and logs.
5. Invoke the exact prompt and wait for the `DEMO RESPONSE`.
6. Restart, stop, and show the resulting status.
7. End by showing the reset command.

Add captions, a transcript, and chapter timestamps. Attach the compressed MP4
to a GitHub Release instead of committing large media to the repository.
