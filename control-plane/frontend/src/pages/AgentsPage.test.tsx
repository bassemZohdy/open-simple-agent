import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { AgentsPage } from "./AgentsPage";

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ agents: [{ agent_id: "a-1", name: "support", description: "Support", status: "active", current_version: "1.0.0", runtime: "adk", tenant_id: null, skills: ["support"], labels: {} }], total: 1, limit: 100, offset: 0 }), { status: 200, headers: { "Content-Type": "application/json" } })));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AgentsPage", () => {
  it("renders agents returned by the Control Plane", async () => {
    render(<MemoryRouter><AuthProvider><AgentsPage /></AuthProvider></MemoryRouter>);
    expect(await screen.findAllByText("support")).toHaveLength(2);
    expect(screen.getByText("1.0.0")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
  });
});
