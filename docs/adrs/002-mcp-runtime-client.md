# ADR-002: MCP runtime client — official Python SDK, stdio + Streamable HTTP

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
- `google-adk` itself pins `mcp>=1.24,<2` for its MCP support, so aligning
  avoids version conflicts.
- `McpDefinition` (transports, timeouts, retries, TLS, response caps,
  credential references) must remain the single configuration surface.

## Considered options

1. **Official MCP Python SDK (`mcp`)** — maintained alongside the
   specification; ships stdio and Streamable HTTP clients plus a server
   implementation usable for deterministic tests.
2. **ADK's `MCPToolset`** — would offload bridging, but hides connection
   lifecycle, limits, and credential resolution that OSA must own, and
   couples OSA tests to ADK's MCP layer.
3. **Hand-rolled JSON-RPC transports** — full control, unacceptable
   maintenance cost.

## Decision

- Use the **official `mcp` Python SDK**, pinned `mcp>=1.24,<2` (matching
  google-adk's own extra) as a core dependency of `osa-adk-runtime`.
- **Protocol-version policy:** OSA follows the SDK's negotiated protocol
  versions; the SDK major (1.x) is the compatibility boundary. Upgrades
  within the pin are covered by CI; a protocol bump requires a major pin
  change and an ADR revision.
- **Transports:** `stdio` (subprocess) and `streamable_http` (the current
  MCP standard). **Legacy `sse` is not supported at runtime** — definitions
  remain schema-valid, but the client rejects them with a deterministic
  error directing users to `streamable_http`.
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

## Consequences

### Positive

- Protocol handling is upstream-maintained; OSA only owns policy
  (filters, limits, retries, credentials).
- Deterministic offline protocol tests are easy: an in-repo stdio server
  subprocess and a localhost Streamable HTTP server.
- Agents on the same runtime share one connection per MCP server.

### Negative or trade-offs

- One more dependency surface (`mcp` SDK majors may introduce protocol
  changes) — mitigated by the pin matching google-adk's extra.
- Legacy SSE deployments need migration to Streamable HTTP.

## Validation

- Protocol-level integration tests run a deterministic stdio MCP server
  (`tests/mcp_fixtures/echo_server.py`) covering discovery, filtering,
  invocation, timeouts, oversized responses, and connection failures; a
  localhost Streamable HTTP server covers HTTP transport and 401 auth
  failures.
- Acceptance (TODO P1.3): a configured agent discovers and invokes a
  filtered MCP tool through the ADK Runner; timeout/auth/oversize/disconnect
  failures are deterministic errors surfaced to the model or caller.

## Follow-up

- [ ] Track upstream SDK majors; revisit the pin when MCP 2.x lands.
- [ ] Consider resource/prompt exposure (list_resources, prompts) once a
      concrete requirement exists.
