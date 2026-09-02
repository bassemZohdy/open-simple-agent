import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { InvocationPage } from "./InvocationPage";

interface FakeRoute {
  pattern: RegExp;
  method?: string;
  respond: () => unknown;
  status?: number;
}

const externalAgent = {
  external_id: "ext-1",
  name: "weather-agent",
  url: "https://weather.example.com/a2a",
  card_name: "weather",
  card_version: "2.0.0",
  skills: [{ name: "forecast" }],
  status: "healthy",
  detail: "",
  agent_type: "external",
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
      return new Response(JSON.stringify(route.respond()), {
        status: route.status ?? 200,
        headers: { "Content-Type": "application/json" },
      });
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
      pattern: /\/external-agents\/ext-1\/invoke/,
      method: "POST",
      respond: () => ({ output: "Sunny, 22°C." }),
    },
    { pattern: /\/external-agents$/, respond: () => [externalAgent] },
  ];
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/console"]}>
      <AuthProvider>
        <InvocationPage />
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

describe("InvocationPage", () => {
  it("renders registered external agents with status badges", async () => {
    renderPage();
    expect((await screen.findAllByText("weather-agent")).length).toBeGreaterThan(0);
    expect(screen.getByText("healthy", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("forecast")).toBeInTheDocument();
  });

  it("invokes the selected agent and renders the response", async () => {
    renderPage();
    await screen.findAllByText("weather-agent");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "What is the weather?" } });
    fireEvent.click(screen.getByRole("button", { name: "Invoke agent" }));
    expect(await screen.findByText("Sunny, 22°C.")).toBeInTheDocument();
    const invokeCall = calls.find((call) => call.url.includes("/external-agents/ext-1/invoke"));
    expect(invokeCall?.method).toBe("POST");
    expect(invokeCall?.url).toContain("message=What+is+the+weather%3F");
    expect(invokeCall?.url).toContain("timeout_seconds=30");
  });

  it("surfaces remote invocation failures", async () => {
    routes = [
      {
        pattern: /\/invoke/,
        method: "POST",
        respond: () => ({ error: { code: "a2a_remote_error", message: "remote agent unreachable" } }),
        status: 502,
      },
      { pattern: /\/external-agents$/, respond: () => [externalAgent] },
    ];
    renderPage();
    await screen.findAllByText("weather-agent");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Invoke agent" }));
    expect(await screen.findByText(/a2a_remote_error/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent response")).not.toBeInTheDocument();
  });

  it("blocks invocation without a message", async () => {
    renderPage();
    await screen.findAllByText("weather-agent");
    fireEvent.click(screen.getByRole("button", { name: "Invoke agent" }));
    expect(await screen.findByText("A message is required")).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("/invoke"))).toBe(false);
  });
});
