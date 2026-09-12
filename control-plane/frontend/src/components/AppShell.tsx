import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { supportedLocales, useLocale } from "../i18n/LocaleContext";
import { ErrorBoundary } from "./ErrorBoundary";

const navigation = [
  ["Agents", "/agents"],
  ["Templates", "/templates"],
  ["Resources", "/resources"],
  ["Deployments", "/deployments"],
  ["Console", "/console"],
  ["Health", "/health"],
  ["Audit", "/audit"],
] as const;

function titleKeyForPath(pathname: string): string {
  if (pathname.startsWith("/agents/")) return "Agent detail";
  if (pathname === "/agents" || pathname === "/") return "Agents";
  if (pathname === "/templates") return "Templates";
  if (pathname === "/resources") return "Resources";
  if (pathname === "/deployments") return "Deployments";
  if (pathname === "/console") return "Console";
  if (pathname === "/health") return "Health";
  if (pathname === "/audit") return "Audit";
  return "Page not found";
}

export function AppShell({ children }: { children: ReactNode }) {
  const { token, setToken, clearToken } = useAuth();
  const { locale, setLocale, t } = useLocale();
  const [draftToken, setDraftToken] = useState("");
  const [showTokenForm, setShowTokenForm] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const { pathname } = useLocation();

  useEffect(() => {
    // F2: a route change must start at the top, even when navigation came
    // from the bottom of a long list.
    window.scrollTo({ top: 0 });
    mainRef.current?.focus({ preventScroll: true });
  }, [pathname]);

  useEffect(() => {
    document.title = `${t(titleKeyForPath(pathname))} — Open Simple Agent`;
  }, [pathname, t]);

  function submitToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draftToken.trim()) return;
    setToken(draftToken);
    setDraftToken("");
    setShowToken(false);
    setShowTokenForm(false);
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">{t("Skip to content")}</a>
      <header className="topbar">
        <div>
          <span className="eyebrow">Open Simple Agent</span>
          <h1>{t("Control Panel")}</h1>
        </div>
        <div className="auth-controls">
          <span className={`connection-pill ${token ? "is-authenticated" : ""}`}>
            {token ? t("Bearer token active") : t("Anonymous API mode")}
          </span>
          {token ? (
            <button className="secondary-button" type="button" onClick={clearToken}>
              {t("Disconnect")}
            </button>
          ) : (
            <button className="secondary-button" type="button" onClick={() => setShowTokenForm((value) => !value)}>
              {t("Connect token")}
            </button>
          )}
          <label className="locale-picker" htmlFor="locale-select">
            <span className="sr-only">{t("Language")}</span>
            <select
              id="locale-select"
              value={locale}
              onChange={(event) => setLocale(event.target.value as (typeof supportedLocales)[number])}
              aria-label={t("Language")}
            >
              <option value="en">English</option>
              <option value="ar">العربية</option>
            </select>
          </label>
        </div>
        {showTokenForm ? (
          <form className="token-form" onSubmit={submitToken} noValidate>
            <label htmlFor="access-token">{t("Bearer access token")}</label>
            <div className="token-row">
              <input
                id="access-token"
                type={showToken ? "text" : "password"}
                autoComplete="off"
                value={draftToken}
                onChange={(event) => setDraftToken(event.target.value)}
                placeholder={t("Paste a short-lived token")}
              />
              <button
                type="button"
                className="secondary-button"
                aria-pressed={showToken}
                aria-label={showToken ? t("Hide token") : t("Show token")}
                onClick={() => setShowToken((value) => !value)}
              >
                {showToken ? t("Hide token") : t("Show token")}
              </button>
              <button type="submit">{t("Use token")}</button>
            </div>
            <small>{t("Stored only in this browser tab/session; never written to OSA configuration.")}</small>
          </form>
        ) : null}
      </header>
      <div className="shell-body">
        <nav className="sidebar" aria-label={t("Control Panel sections")}>
          {navigation.map(([label, href]) => (
            <NavLink key={href} to={href} className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
              {t(label)}
            </NavLink>
          ))}
        </nav>
        <main ref={mainRef} className="content" id="main-content" tabIndex={-1}>
          <ErrorBoundary>{children}</ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
