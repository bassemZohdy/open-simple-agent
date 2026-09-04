import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { DeploymentsPage } from "./DeploymentsPage";

interface FakeRoute {
  pattern: RegExp;
  method?: string;
  respond: () => unknown;
}

const agent = {
  agent_id: "a-1",
  name: "support",
  description: "Support",
  status: "active",
  current_version: "1.0.0",
  runtime: "adk",
  tenant_id: null,
  skills: ["support"],
  labels: {},
};

const deployment = {
  deployment_id: "dep-1",
  agent_id: "a-1",
  agent_name: "support",
  tenant_id: null,
  version: "1.0.0",
  status: "running",
  detail: "pid 4242",
  invoke_url: "https://agents.example.test/a-1/dep-1",
};

let calls: Array<{ url: string; method: string; body: string | null }>;
let routes: FakeRoute[];

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

async function routedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  const method = (init?.method ?? "GET").toUpperCase();
  calls.push({ url, method, body: typeof init?.body === "string" ? init.body : null });
  for (const route of routes) {
    if (route.method && route.method !== method) continue;
    if (route.pattern.test(url)) return jsonResponse(route.respond());
  }
  return jsonResponse({ error: { code: "route_not_mocked", message: `${method} ${url}` } }, 404);
}

function defaultRoutes(): FakeRoute[] {
  return [
    { pattern: /\/agents\/a-1\/deploy$/, method: "POST", respond: () => ({ ...deployment, deployment_id: "dep-2", version: "2.0.0", status: "starting", detail: "" }) },
    { pattern: /\/agents\/a-1\/deployments$/, respond: () => [deployment] },
    { pattern: /\/deployments\/dep-1\/logs/, respond: () => ({ deployment_id: "dep-1", lines: ["2026-09-03 boot ok", "osa.runtime listening"] }) },
    { pattern: /\/deployments\/dep-1\/stop$/, method: "POST", respond: () => ({ ...deployment, status: "stopped", detail: "" }) },
    { pattern: /\/agents\?/, respond: () => ({ agents: [agent], total: 1, limit: 100, offset: 0 }) },
  ];
}

function renderPage(initialEntries?: string[]) {
  return render(
    <MemoryRouter initialEntries={initialEntries ?? ["/deployments"]}>
      <AuthProvider>
        <DeploymentsPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  calls = [];
  routes = defaultRoutes();
  vi.stubGlobal("fetch", vi.fn(routedFetch));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function selectAgent() {
  await screen.findByRole("option", { name: "support (active)" });
  fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "a-1" } });
  await screen.findByText("dep-1");
}

describe("DeploymentsPage", () => {
  it("renders deployment history after selecting an agent", async () => {
    renderPage();
    await selectAgent();
    expect(screen.getByText("1.0.0")).toBeInTheDocument();
    expect(screen.getAllByText("running").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Deploy current version" })).toBeInTheDocument();
  });

  it("loads history directly from the agent deep link", async () => {
    renderPage(["/deployments?agent=a-1"]);
    await screen.findByText("dep-1");
    expect((screen.getByLabelText("Agent") as HTMLSelectElement).value).toBe("a-1");
  });

  it("deploys the current version with an intent-only request", async () => {
    renderPage();
    await selectAgent();
    fireEvent.click(screen.getByRole("button", { name: "Deploy current version" }));
    expect((await screen.findAllByText("dep-2")).length).toBeGreaterThan(0);
    expect(await screen.findByText("Deployment dep-2 started for version 2.0.0.")).toBeInTheDocument();
    const deployCall = calls.find((call) => call.url.endsWith("/agents/a-1/deploy"));
    expect(deployCall?.method).toBe("POST");
    expect(deployCall?.body).toBe("{}");
  });

  it("stops a running deployment from the detail card", async () => {
    renderPage();
    await selectAgent();
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(await screen.findAllByText("stopped").then((elements) => elements.length)).toBeGreaterThan(0);
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/deployments/dep-1/stop"))).toBe(true);
  });

  it("shows the runtime endpoint link when the deployment publishes one", async () => {
    renderPage();
    await selectAgent();
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    const link = await screen.findByRole("link", { name: "https://agents.example.test/a-1/dep-1" });
    expect(link).toHaveAttribute("href", "https://agents.example.test/a-1/dep-1");
  });

  it("loads captured logs into the output panel", async () => {
    renderPage();
    await selectAgent();
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    fireEvent.click(await screen.findByRole("button", { name: "Load logs" }));
    expect(await screen.findByText(/osa\.runtime listening/)).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("/deployments/dep-1/logs?tail=200"))).toBe(true);
  });
});
