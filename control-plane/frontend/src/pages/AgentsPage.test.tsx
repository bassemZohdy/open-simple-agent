import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { AgentsPage } from "./AgentsPage";

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

let calls: Array<{ url: string; method: string; body: string | null }>;
let routes: FakeRoute[];

async function routedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input);
  const method = (init?.method ?? "GET").toUpperCase();
  calls.push({ url, method, body: typeof init?.body === "string" ? init.body : null });
  for (const route of routes) {
    if (route.method && route.method !== method) continue;
    if (route.pattern.test(url)) {
      return new Response(JSON.stringify(route.respond()), { status: 200, headers: { "Content-Type": "application/json" } });
    }
  }
  return new Response(JSON.stringify({ error: { code: "route_not_mocked", message: `${method} ${url}` } }), {
    status: 404,
    headers: { "Content-Type": "application/json" },
  });
}

function defaultRoutes(): FakeRoute[] {
  return [
    {
      pattern: /\/agents\/a-1$/,
      respond: () => agent,
    },
    {
      pattern: /\/agents$/,
      method: "POST",
      respond: () => ({ ...agent, agent_id: "new-1", name: "support-copy", status: "draft", current_version: "" }),
    },
    {
      pattern: /\/templates$/,
      respond: () => [{ name: "generic", description: "A generic agent template.", skills: [], memory_enabled: false, memory_policy: null }],
    },
    { pattern: /\/agents\?/, respond: () => ({ agents: [agent], total: 1, limit: 100, offset: 0 }) },
  ];
}

function renderPage(initialEntries?: string[]) {
  return render(
    <MemoryRouter initialEntries={initialEntries ?? ["/agents"]}>
      <AuthProvider>
        <Routes>
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/:agentId" element={<div>navigated to detail</div>} />
        </Routes>
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

describe("AgentsPage", () => {
  it("renders agents returned by the Control Plane", async () => {
    renderPage();
    expect(await screen.findAllByText("support")).toHaveLength(2);
    expect(screen.getByText("1.0.0")).toBeInTheDocument();
    expect(await screen.findByText("active", { selector: "span" })).toBeInTheDocument();
  });

  it("creates an agent from a template and navigates to the detail page", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create agent" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "support-copy" } });
    fireEvent.change(screen.getByLabelText(/Configuration source/), { target: { value: "template" } });
    const templateOption = (await screen.findByRole("option", { name: "generic" })) as HTMLOptionElement;
    fireEvent.change(screen.getByLabelText("Template"), { target: { value: templateOption.value } });
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByText("navigated to detail")).toBeInTheDocument();
    const createCall = calls.find((call) => call.method === "POST" && call.url.endsWith("/agents"));
    expect(createCall?.body && JSON.parse(createCall.body)).toEqual({
      name: "support-copy",
      description: "",
      template: "generic",
    });
  });

  it("prefills the create panel when cloning an agent", async () => {
    renderPage(["/agents?create=1&cloneOf=a-1"]);
    const nameInput = await screen.findByLabelText("Name");
    expect((nameInput as HTMLInputElement).value).toBe("support-copy");
    expect(screen.getByText(/Cloning support/)).toBeInTheDocument();
    expect(screen.getByText(/write-only/)).toBeInTheDocument();
  });

  it("rejects a definition whose metadata.name does not match without calling the API", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create agent" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "my-agent" } });
    fireEvent.change(screen.getByLabelText(/Configuration source/), { target: { value: "definition" } });
    fireEvent.change(
      screen.getByLabelText(/Definition \(JSON\)/),
      { target: { value: JSON.stringify({ apiVersion: "osa/v1alpha1", kind: "Agent", metadata: { name: "other" }, spec: {} }) } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByText(/must match the agent name/)).toBeInTheDocument();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });
});
