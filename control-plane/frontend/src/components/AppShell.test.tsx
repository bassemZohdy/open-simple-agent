import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { AppShell } from "./AppShell";

function renderShell(initialEntries?: string[]) {
  return render(
    <MemoryRouter initialEntries={initialEntries ?? ["/agents"]}>
      <AuthProvider>
        <AppShell>
          <Routes>
            <Route path="/agents" element={<div>agents page body</div>} />
            <Route path="/health" element={<div>health page body</div>} />
          </Routes>
        </AppShell>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ready" }), { status: 200 })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AppShell", () => {
  it("offers a skip link that targets the main content region", () => {
    renderShell();
    const skipLink = screen.getByRole("link", { name: "Skip to content" });
    expect(skipLink).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("moves focus to the main content region on route change", () => {
    renderShell();
    const main = screen.getByRole("main");
    (main as HTMLElement).blur();
    expect(document.activeElement).not.toBe(main);
    fireEvent.click(screen.getByRole("link", { name: "Health" }));
    expect(screen.getByText("health page body")).toBeInTheDocument();
    expect(document.activeElement).toBe(main);
  });

  it("keeps the main content region out of the tab order stop list semantics", () => {
    renderShell();
    const main = screen.getByRole("main");
    expect(main).toHaveAttribute("tabindex", "-1");
  });
});
