# OSA Control Panel

The Control Panel is the TypeScript/React administrative UI for the Open Simple Agent Control Plane.

## Development

```bash
cd control-plane/frontend
npm install
npm run dev
```

Set `VITE_OSA_API_BASE_URL` to the Control Plane origin when it is not `http://localhost:8000`.

The shell supports an optional Bearer token for Control Plane instances using `OSA_AUTH_MODE=optional|required`. Tokens are stored only in `sessionStorage`; they are never written to source, configuration, URLs, or logs. OIDC login/refresh orchestration is intentionally not invented here because issuer/client/redirect semantics are deployment-specific and are not yet a stable Control Plane contract.

## Current implementation

Implemented:

- responsive React shell and navigation;
- optional session-scoped Bearer token handling;
- typed Control Plane API client with stable OSA error-envelope handling;
- Agents list/search/status filtering backed by `GET /agents`;
- built-in template cards backed by `GET /templates`;
- tenant-scoped Model, Tool, Skill, MCP, and MemoryPolicy catalog browsing backed by `GET /resources/{kind}`;
- resource name filtering and safe inspection of the already-redacted resource definition returned by the Control Plane;
- readiness view backed by `GET /health/ready`;
- agent detail pages with redacted version history, version snapshots, safe
  on-demand inspection of immutable snapshot content, and guarded
  activate/disable/archive lifecycle actions;
- per-agent deployment history with intent-only deploy, stop/restart/rollback
  lifecycle actions, observed-status refresh, and bounded captured-log
  inspection backed by the Control Plane deployment APIs;
- audit event list with action filtering backed by `GET /audit-events` and
  operational metrics backed by `GET /metrics` (parsed samples plus the raw
  Prometheus exposition);
- validated create/clone agent flows: empty draft, built-in template, or
  pasted JSON definition with client and server validation, plus clone
  deep-links that pre-fill agent metadata;
- A2A invocation test console: external agents with health status, message +
  timeout invocation, and inline response/error rendering backed by
  `POST /external-agents/{id}/invoke`;
- skip-to-content link, route-change focus management, visible focus states,
  semantic status/error regions, and narrow-viewport layout coverage for
  keyboard users;
- loading, empty, error, 401/403-safe presentation;
- Vitest/Testing Library coverage for API auth/error behavior and the implemented management views.

Managed-agent invocation (sessions, streaming, tool traces) is available from
the Deployments page when a deployment publishes a runtime invoke URL. The
Control Plane never proxies that traffic; configure the runtime's own auth and
CORS posture as described in ADR-008 and the security guide.
