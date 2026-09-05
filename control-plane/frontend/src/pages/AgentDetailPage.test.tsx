import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { AgentDetailPage } from "./AgentDetailPage";

const agent = {
  agent_id: "a-1",
  name: "support",
  description: "Support agent",
  status: "draft",
  current_version: "1.0.0",
  runtime: "adk",
  tenant_id: null,
  skills: ["support"],
  labels: { team: "service" },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/agents/a-1"]}>
      <AuthProvider>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentDetailPage", () => {
  it("renders agent metadata, redacted version history, and valid lifecycle actions", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith("/versions")
        ? [{ version_id: "v-1", version: "1.0.0", created_at: "2026-09-02T10:00:00Z", created_by: "", change_summary: "Initial release", has_definition: true }]
        : agent;
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));

    renderPage();

    expect(await screen.findByRole("heading", { name: "support" })).toBeInTheDocument();
    expect(screen.getByText("Initial release")).toBeInTheDocument();
    expect(screen.getByText("Definition available")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
  });

  it("loads and renders a safe immutable snapshot on demand", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/versions")) {
        return Promise.resolve(new Response(JSON.stringify([
          { version_id: "v-1", version: "1.0.0", created_at: "2026-09-02T10:00:00Z", created_by: "", change_summary: "Initial release", has_definition: true },
        ]), { status: 200 }));
      }
      if (url.endsWith("/versions/v-1")) {
        return Promise.resolve(new Response(JSON.stringify({
          version_id: "v-1",
          version: "1.0.0",
          created_at: "2026-09-02T10:00:00Z",
          created_by: "",
          change_summary: "Initial release",
          has_definition: true,
          definition: { metadata: { name: "support" }, spec: { instruction: "Help safely." } },
          redacted_fields: [],
        }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(agent), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByRole("heading", { name: "support" });
    fireEvent.click(screen.getByRole("button", { name: "View safe snapshot" }));

    expect(await screen.findByLabelText("Safe definition for version 1.0.0")).toHaveTextContent("Help safely.");
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/agents/a-1/versions/v-1"), expect.anything());
  });

  it("creates a version and applies a lifecycle transition", async () => {
    let created = false;
    let activated = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/versions") && init?.method === "POST") {
        created = true;
        return Promise.resolve(new Response(JSON.stringify({ ...agent, current_version: "2.0.0" }), { status: 201 }));
      }
      if (url.endsWith("/activate")) {
        activated = true;
        return Promise.resolve(new Response(JSON.stringify({ ...agent, status: "active" }), { status: 200 }));
      }
      if (url.endsWith("/versions")) {
        const versions = created
          ? [{ version_id: "v-2", version: "2.0.0", created_at: "2026-09-02T10:00:00Z", created_by: "", change_summary: "Added escalation", has_definition: true }]
          : [];
        return Promise.resolve(new Response(JSON.stringify(versions), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(activated ? { ...agent, status: "active" } : agent), { status: 200 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByRole("heading", { name: "support" });
    fireEvent.change(screen.getByLabelText("Version"), { target: { value: "2.0.0" } });
    fireEvent.change(screen.getByLabelText("Change summary"), { target: { value: "Added escalation" } });
    fireEvent.click(screen.getByRole("button", { name: "Create version" }));

    expect(await screen.findByText("Version 2.0.0 created successfully.")).toBeInTheDocument();
    expect(screen.getByText("Added escalation")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Activate" }));

    expect(await screen.findByText("Agent activated successfully.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/agents/a-1/activate"), expect.objectContaining({ method: "POST" }));
  });
});
