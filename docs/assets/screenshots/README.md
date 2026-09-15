# Docker demo screenshots

This directory is reserved for screenshots captured from the released Docker
demo described in [the walkthrough](../../guides/docker-demo.md). The images
must be captured from a running application with synthetic data; do not add
mockups or generated images.

Before committing media, record the exact release/tag and image digests in a
sidecar metadata file, and verify every image against the capture checklist:

| Filename | Walkthrough evidence |
|---|---|
| `01-agents-en.png` | Active `docker-demo-agent` in the Agents view |
| `02-agent-detail-en.png` | Agent version, model, and deployment details |
| `03-resources-en.png` | The `default` deterministic model resource |
| `04-deployment-running-en.png` | Running deployment and browser invoke URL |
| `05-invocation-demo-en.png` | Exact prompt and visible `DEMO RESPONSE` |
| `06-logs-status-en.png` | Successful status and bounded logs |
| `07-agent-ar.png` | Arabic RTL layout with the same synthetic agent |
| `08-deployment-narrow.png` | Narrow viewport responsive deployment result |

Each image needs a nearby caption and alt text in the documentation that links
to the relevant walkthrough step. Redact tokens, credentials, personal data,
and unrelated browser content. Optimize images for GitHub rendering; keep
source recordings outside git. The optional video is attached to a GitHub
Release and follows the same numbering and prompt.
