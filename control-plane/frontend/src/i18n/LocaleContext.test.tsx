import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { AppShell } from "../components/AppShell";
import { LocaleProvider } from "./LocaleContext";

function renderLocalizedShell() {
  return render(
    <MemoryRouter initialEntries={["/agents"]}>
      <AuthProvider>
        <LocaleProvider>
          <AppShell>
            <div>localized page body</div>
          </AppShell>
        </LocaleProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  document.documentElement.lang = "en";
  document.documentElement.dir = "ltr";
});

afterEach(() => {
  cleanup();
});

describe("LocaleProvider", () => {
  it("switches the shared shell to Arabic and applies RTL direction", () => {
    renderLocalizedShell();

    fireEvent.change(screen.getByRole("combobox", { name: "Language" }), { target: { value: "ar" } });

    expect(document.documentElement).toHaveAttribute("lang", "ar");
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(document.title).toBe("الوكلاء — Open Simple Agent");
    expect(screen.getByRole("link", { name: "الوكلاء" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "التدقيق" })).toBeInTheDocument();
    expect(sessionStorage.getItem("osa-control-panel-locale")).toBe("ar");
  });

  it("restores the selected locale from the current browser session", () => {
    sessionStorage.setItem("osa-control-panel-locale", "ar");

    renderLocalizedShell();

    expect(document.documentElement).toHaveAttribute("lang", "ar");
    expect(document.title).toBe("الوكلاء — Open Simple Agent");
    expect(screen.getByRole("heading", { name: "لوحة التحكم" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "الموارد" })).toBeInTheDocument();
  });
});
