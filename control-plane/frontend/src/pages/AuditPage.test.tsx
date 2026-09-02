import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { AuditPage } from "./AuditPage";

interface FakeRoute {
  pattern: RegExp;
  method?: string;
  respond: () => string;
}

const event = {
  event_id: "evt-1",
  actor: "anonymous",
  action: "deployment.deploy",
  target: "dep-1",
  occurred_at: "2026-09-03T10:00:00+00:00",
  tenant_id: null,
  detail: { agent_id: "a-1", version: "1.1.0" },
};

let routes: FakeRoute[];

async function routedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  const method = (init?.method ?? "GET").toUpperCase();
  for (const route of routes) {
    if (route.method && route.method !== method) continue;
    if (route.pattern.test(url)) {
      return new Response(route.respond(), { status: 200, headers: { "Content-Type": "text/plain" } });
    }
  }
  return new Response(JSON.stringify({ error: { code: "route_not_mocked", message: `${method} ${url}` } }), {
    status: 404,
    headers: { "Content-Type": "application/json" },
  });
}

function defaultRoutes(): FakeRoute[] {
  return [
    { pattern: /\/audit-events/, respond: () => JSON.stringify([event]) },
    {
      pattern: /\/metrics$/,
      respond: () =>
        "osa_http_requests_total{route=\"/agents\",method=\"GET\"} 12\nosa_invoke_seconds_count{agent=\"support\"} 3\n",
    },
  ];
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/audit"]}>
      <AuthProvider>
        <AuditPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  routes = defaultRoutes();
  vi.stubGlobal("fetch", vi.fn(routedFetch));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AuditPage", () => {
  it("renders audit events returned by the Control Plane", async () => {
    renderPage();
    expect(await screen.findByText("deployment.deploy")).toBeInTheDocument();
    expect(screen.getByText("dep-1")).toBeInTheDocument();
    expect(screen.getByText(/agent_id/)).toHaveAttribute("title", '{"agent_id":"a-1","version":"1.1.0"}');
  });

  it("parses Prometheus metrics into a sample table with a raw view", async () => {
    renderPage();
    expect(await screen.findByText("osa_http_requests_total")).toBeInTheDocument();
    expect(screen.getByText('route="/agents",method="GET"')).toBeInTheDocument();
    expect(screen.getByText("osa_invoke_seconds_count")).toBeInTheDocument();
    const raw = screen.getByText(/osa_http_requests_total\{/);
    expect(raw).toBeInTheDocument();
  });

  it("filters events by action substring on the client", async () => {
    renderPage();
    await screen.findByText("deployment.deploy");
    fireEvent.change(screen.getByLabelText("Action"), { target: { value: "agent" } });
    expect(screen.queryByText("deployment.deploy")).not.toBeInTheDocument();
    const badge = screen.getByLabelText("0 matching audit events");
    expect(badge).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Action"), { target: { value: "deployment" } });
    expect(await screen.findByText("deployment.deploy")).toBeInTheDocument();
  });

  it("surfaces backend failures with a retry path", async () => {
    routes = [{ pattern: /\/nomatch/, respond: () => "{}" }];
    renderPage();
    expect(await screen.findByText("Audit events unavailable")).toBeInTheDocument();
    expect(screen.getByText("Metrics unavailable")).toBeInTheDocument();
  });
});
