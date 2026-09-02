import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, ControlPlaneClient } from "./client";

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

  it("maps the stable OSA error envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "authorization_denied", message: "missing permission" } }), { status: 403, headers: { "Content-Type": "application/json" } })));
    const client = new ControlPlaneClient("https://control.example", () => null);

    await expect(client.listAgents()).rejects.toEqual(expect.objectContaining<ApiError>({ status: 403, code: "authorization_denied", message: "missing permission" }));
  });
});
