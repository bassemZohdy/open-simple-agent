import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
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

export function AppShell({ children }: { children: ReactNode }) {
  const { token, setToken, clearToken } = useAuth();
  const [draftToken, setDraftToken] = useState("");
  const [showTokenForm, setShowTokenForm] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const { pathname } = useLocation();

  useEffect(() => {
    // F2: a route change must start at the top, even when navigation came
    // from the bottom of a long list.
    window.scrollTo({ top: 0 });
    mainRef.current?.focus({ preventScroll: true });
  }, [pathname]);

  function submitToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draftToken.trim()) return;
    setToken(draftToken);
    setDraftToken("");
    setShowTokenForm(false);
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="topbar">
        <div>
          <span className="eyebrow">Open Simple Agent</span>
          <h1>Control Panel</h1>
        </div>
        <div className="auth-controls">
          <span className={`connection-pill ${token ? "is-authenticated" : ""}`}>
            {token ? "Bearer token active" : "Anonymous API mode"}
          </span>
          {token ? (
            <button className="secondary-button" type="button" onClick={clearToken}>
              Disconnect
            </button>
          ) : (
            <button className="secondary-button" type="button" onClick={() => setShowTokenForm((value) => !value)}>
              Connect token
            </button>
          )}
        </div>
        {showTokenForm ? (
          <form className="token-form" onSubmit={submitToken} noValidate>
            <label htmlFor="access-token">Bearer access token</label>
            <div className="token-row">
              <input
                id="access-token"
                type="password"
                autoComplete="off"
                value={draftToken}
                onChange={(event) => setDraftToken(event.target.value)}
                placeholder="Paste a short-lived token"
              />
              <button type="submit">Use token</button>
            </div>
            <small>Stored only in this browser tab/session; never written to OSA configuration.</small>
          </form>
        ) : null}
      </header>
      <div className="shell-body">
        <nav className="sidebar" aria-label="Control Panel sections">
          {navigation.map(([label, href]) => (
            <NavLink key={href} to={href} className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
              {label}
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
