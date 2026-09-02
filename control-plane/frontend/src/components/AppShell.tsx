import { type FormEvent, type ReactNode, useState } from "react";
import { NavLink } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const navigation = [
  ["Agents", "/agents"],
  ["Templates", "/templates"],
  ["Resources", "/resources"],
  ["Deployments", "/deployments"],
  ["Health", "/health"],
  ["Audit", "/audit"],
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const { token, setToken, clearToken } = useAuth();
  const [draftToken, setDraftToken] = useState("");
  const [showTokenForm, setShowTokenForm] = useState(false);

  function submitToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draftToken.trim()) return;
    setToken(draftToken);
    setDraftToken("");
    setShowTokenForm(false);
  }

  return (
    <div className="app-shell">
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
          <form className="token-form" onSubmit={submitToken}>
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
        <main className="content" id="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}
