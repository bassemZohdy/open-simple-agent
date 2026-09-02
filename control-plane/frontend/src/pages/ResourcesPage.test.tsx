import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { ResourcesPage } from "./ResourcesPage";

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ kind: "Model", total: 1, resources: [{ apiVersion: "osa/v1alpha1", kind: "Model", spec: { name: "primary-model", description: "Primary inference model", provider: "litellm", model: "openai/gpt-5" } }] }), { status: 200, headers: { "Content-Type": "application/json" } })));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ResourcesPage", () => {
  it("renders resource envelopes returned by the Control Plane", async () => {
    render(<MemoryRouter><AuthProvider><ResourcesPage /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "primary-model" })).toBeInTheDocument();
    expect(screen.getByText("Primary inference model")).toBeInTheDocument();
    expect(screen.getByText("litellm")).toBeInTheDocument();
    expect(screen.getByText("osa/v1alpha1")).toBeInTheDocument();
  });
});
