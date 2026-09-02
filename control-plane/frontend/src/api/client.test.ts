import { afterEach, describe, expect, it, vi } from "vitest";

import { ControlPlaneClient } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ControlPlaneClient", () => {
  it("adds a bearer token without exposing it in the URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ agents: [], total: 0, limit: 50, offset: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new ControlPlaneClient("https://control.example/", () => "secret-token");

    await client.listAgents({ q: "support" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://control.example/agents?q=support");
    expect(url).not.toContain("secret-token");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer secret-token");
  });

  it("builds resource catalog queries using the API kind", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ kind: "MemoryPolicy", total: 0, resources: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new ControlPlaneClient("https://control.example", () => null);

    await client.listResources("MemoryPolicy", "user memory");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("https://control.example/resources/MemoryPolicy?q=user+memory");
  });

  it("loads built-in templates from the Control Plane", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new ControlPlaneClient("https://control.example", () => null);

    await client.listTemplates();

    expect(fetchMock.mock.calls[0]?.[0]).toBe("https://control.example/templates");
  });

  it("loads redacted agent version history", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new ControlPlaneClient("https://control.example", () => null);

    await client.listAgentVersions("support/agent");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("https://control.example/agents/support%2Fagent/versions");
  });

  it("posts lifecycle actions without putting the agent id in a query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ agent_id: "a/1", status: "disabled" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new ControlPlaneClient("https://control.example", () => null);

    await client.disableAgent("a/1");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://control.example/agents/a%2F1/disable");
    expect(init.method).toBe("POST");
    expect(url).not.toContain("?");
  });

  it("maps the stable OSA error envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "authorization_denied", message: "missing permission" } }), { status: 403, headers: { "Content-Type": "application/json" } })));
    const client = new ControlPlaneClient("https://control.example", () => null);

    await expect(client.listAgents()).rejects.toEqual(expect.objectContaining({ status: 403, code: "authorization_denied", message: "missing permission" }));
  });
});
