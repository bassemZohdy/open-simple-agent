# ADR-002: MCP runtime client — official Python SDK and explicit transports

## Status

Accepted

## Date

2026-08-31

## Owners

Open Simple Agent maintainers

## Context

P1.3 requires a runtime MCP client: agents reference MCP servers in
`spec.mcps`, and the runtime must connect, discover tools, apply filters, and
invoke tools with bounded, observable failures. Until now MCP existed only as
catalog/schema types (`osa.generic_agent.mcp`).

## Decision drivers

- Protocol correctness (initialization handshake, tool listing, tool calls)
  without hand-rolling JSON-RPC transports.
- Test environments must stay offline and deterministic (a local stdio server
  is a plain subprocess).
- The tested `google-adk` 2.8.0 MCP extra still declares `mcp>=1.24,<2`, so OSA
  must keep its own bridge independent of that optional ADK extra while
  supporting the official SDK's 1.x and 2.x lines.
- `McpDefinition` (transports, timeouts, retries, TLS, response caps,
  credential references) must remain the single configuration surface.

## Considered options

1. **Official MCP Python SDK (`mcp`)** — maintained alongside the
   specification; ships stdio, Streamable HTTP, and legacy SSE clients plus a
   server implementation usable for deterministic tests.
2. **ADK's `MCPToolset`** — would offload bridging, but hides connection
   lifecycle, limits, and credential resolution that OSA must own, and
  couples OSA tests to ADK's MCP layer.
3. **Hand-rolled JSON-RPC transports** — full control, unacceptable
   maintenance cost.

## Decision

- Use the **official `mcp` Python SDK**, with the supported range
  `mcp>=1.24,<3` as a core dependency of `osa-adk-runtime`. OSA normalizes the
  SDK-major differences at its MCP client boundary.
- **Protocol-version policy:** OSA follows the SDK's negotiated protocol
  versions; SDK majors 1.x and 2.x are the current compatibility boundary.
  Both majors are exercised by CI, and a future major requires a compatibility
  port and an ADR revision.
- **Transports:** `stdio` (subprocess), `streamable_http` (the current MCP
  standard), and explicitly selected legacy `sse`. SSE remains available for
  compatibility, but new deployments should migrate to Streamable HTTP; all
  HTTP transports use OSA's endpoint validation and disabled-redirect policy.
- Connection lifecycle is owned by OSA (`osa.runtimes.adk.mcp_client`):
  lazy connection on first use, per-server connection pool shared across an
  agent runtime, bounded retries (`max_retries`, `retry_delay_seconds`),
  timeouts (`timeout_seconds`), TLS verification (`tls_verify`), response
  size caps (`max_response_bytes`), and credential resolution from
  `credential` via the shared outbound credential adapters and
  `SecretResolver` contract (values are resolved at connect time and never
  stored, logged, or included in errors). The legacy `credential_ref`
  bearer/stdio shorthand remains supported.
- Server tools are filtered by the server definition's `tools_filter`
  intersected with the agent reference's `tools_filter`, namespaced as
  `<server>_<tool>` (sanitized ADK identifiers), and bridged to ADK as
  function tools whose declarations come from the MCP `inputSchema`.
  Origin metadata (server name, original tool name) is preserved.
- Application callers can discover and retrieve server resources and prompts
  through the same pooled connection. OSA normalizes metadata, text/blob
  resource content, and prompt messages, applies server-level filters, and
  bounds discovery and response payloads. These operations are not silently
  injected into the model tool list.

## Consequences

### Positive

- Protocol handling is upstream-maintained; OSA only owns policy
  (filters, limits, retries, credentials).
- Deterministic offline protocol tests are easy: an in-repo stdio server
  subprocess and a localhost Streamable HTTP server.
- Agents on the same runtime share one connection per MCP server.

### Negative or trade-offs

- One more dependency surface (`mcp` SDK majors may introduce protocol
  changes) — mitigated by the normalized client boundary and dual-major CI.
- Legacy SSE remains a compatibility path; operators should prefer Streamable
  HTTP for new deployments and plan migration when the upstream server supports
  it.

## MCP 2.x compatibility assessment — 2026-09-12

The MCP 2.x port is complete. OSA now normalizes the v1/v2 timeout contract,
tool input-schema and error-field spellings, the v2 HTTP client module, and the
server fixture import change. The deterministic protocol suite and the ADK
Runner/toolset suite pass against both `mcp==1.29.1` and `mcp==2.2.0` with
`google-adk==2.8.0`. The runtime dependency range is widened to `mcp>=1.24,<3`
and the lock metadata is refreshed. OSA does not select ADK's optional MCP
extra, whose metadata remains v1-only.

## Validation

- Protocol-level integration tests run a deterministic stdio MCP server
  (`tests/mcp_fixtures/echo_server.py`) covering discovery, filtering,
  invocation, resources, prompts, timeouts, oversized responses, and
  connection failures; localhost Streamable HTTP and legacy SSE servers cover
  HTTP transports and 401/auth behavior.
- Acceptance (P1.3): a configured agent discovers and invokes a
  filtered MCP tool through the ADK Runner; timeout/auth/oversize/disconnect
  failures are deterministic errors surfaced to the model or caller. This is
  covered by `tests/integration/test_mcp_agent.py`.

## Follow-up

- [x] Review the MCP 2.x release and record the compatibility assessment above.
- [x] Port OSA to MCP 2.x, validate the supported google-adk combination, and
      widen the dependency pin with dual-major CI coverage.
- [x] Add application-controlled resource/prompt discovery and retrieval with
      bounded normalized payloads, and support legacy SSE through the same
      official-SDK connection boundary.
