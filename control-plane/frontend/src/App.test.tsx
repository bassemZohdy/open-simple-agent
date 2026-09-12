import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AuthProvider } from "./auth/AuthContext";
import { App } from "./App";

describe("App routes", () => {
  it("renders a recoverable not-found route", () => {
    render(
      <MemoryRouter initialEntries={["/missing"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to agents" })).toHaveAttribute("href", "/agents");
  });
});
