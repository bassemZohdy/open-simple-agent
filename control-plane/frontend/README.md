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
- loading, empty, error, 401/403-safe presentation;
- Vitest/Testing Library coverage for API auth/error behavior and the implemented management views.

Agent details/versioning/lifecycle, deployment lifecycle/logs, audit/metrics, authoring flows, and the invocation console remain P3.1 follow-up work.
