import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { TemplatesPage } from "./TemplatesPage";

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([{ name: "support", description: "Customer support assistant.", skills: ["support", "case-resolution"], memory_enabled: true, memory_policy: "user-memory" }]), { status: 200, headers: { "Content-Type": "application/json" } })));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TemplatesPage", () => {
  it("renders templates returned by the Control Plane", async () => {
    render(<MemoryRouter><AuthProvider><TemplatesPage /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "support" })).toBeInTheDocument();
    expect(screen.getByText("Customer support assistant.")).toBeInTheDocument();
    expect(screen.getByText("user-memory")).toBeInTheDocument();
  });
});
