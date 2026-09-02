import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError, type ExternalAgentSummary } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? `${caught.code}: ${caught.message}` : fallback;
}

function statusClass(status: string): string {
  if (status === "healthy") return "status status-active";
  if (status === "unreachable") return "status status-failed";
  return "status status-disabled";
}

function skillNames(skills: ExternalAgentSummary["skills"]): string {
  const names = skills
    .map((skill) => (typeof skill.name === "string" ? skill.name : null))
    .filter((name): name is string => name !== null);
  return names.length > 0 ? names.join(", ") : `${skills.length} skill(s)`;
}

export function InvocationPage() {
  const client = useControlPlaneClient();
  const [agents, setAgents] = useState<ExternalAgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState("");
  const [message, setMessage] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [invoking, setInvoking] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await client.listExternalAgents();
      setAgents(next);
      setSelectedId((current) =>
        next.some((agent) => agent.external_id === current) ? current : (next[0]?.external_id ?? ""),
      );
    } catch (caught) {
      setError(errorMessage(caught, "Unable to load external agents"));
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  async function submitInvocation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedId) {
      setInvokeError("Select an external agent to invoke");
      return;
    }
    const trimmed = message.trim();
    if (!trimmed) {
      setInvokeError("A message is required");
      return;
    }
    setInvoking(true);
    setInvokeError(null);
    setOutput(null);
    try {
      const result = await client.invokeExternalAgent(selectedId, trimmed, timeoutSeconds);
      setOutput(result.output);
    } catch (caught) {
      setInvokeError(errorMessage(caught, "Unable to invoke the external agent"));
    } finally {
      setInvoking(false);
    }
  }

  return (
    <section aria-labelledby="console-title">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Invocation console</span>
          <h2 id="console-title">Console</h2>
          <p>Test external A2A agents registered with the Control Plane.</p>
        </div>
        <span className="count-badge" aria-label={`${agents.length} external agents`}>{agents.length}</span>
      </div>

      {loading ? <div className="state-card" role="status">Loading external agents…</div> : null}
      {!loading && error ? (
        <div className="state-card error-card" role="alert">
          <strong>External agents unavailable</strong>
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={() => void loadAgents()}>Retry</button>
        </div>
      ) : null}
      {!loading && !error && agents.length === 0 ? (
        <div className="state-card">
          <strong>No external agents registered</strong>
          <span>Register an external A2A agent through the Control Plane API to test it here.</span>
        </div>
      ) : null}
      {!loading && !error && agents.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Agent</th><th>URL</th><th>Status</th><th>Skills</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.external_id}>
                  <td>
                    <strong>{agent.name}</strong>
                    <small>{agent.card_name ? `${agent.card_name} ${agent.card_version}`.trim() : "No card name"}</small>
                  </td>
                  <td><code>{agent.url}</code></td>
                  <td>
                    <span className={statusClass(agent.status)}>{agent.status}</span>
                    {agent.detail ? <small>{agent.detail}</small> : null}
                  </td>
                  <td>{skillNames(agent.skills)}</td>
                  <td>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => setSelectedId(agent.external_id)}
                      disabled={selectedId === agent.external_id}
                    >
                      {selectedId === agent.external_id ? "Selected" : "Test"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <section className="detail-section" aria-labelledby="a2a-console-title">
        <div className="section-heading">
          <div>
            <span className="eyebrow">A2A test console</span>
            <h3 id="a2a-console-title">Send a message</h3>
          </div>
        </div>
        <form className="detail-card create-panel" onSubmit={(event) => void submitInvocation(event)}>
          <div className="filter-bar">
            <label htmlFor="invoke-agent">Agent
              <select
                id="invoke-agent"
                value={selectedId}
                onChange={(event) => setSelectedId(event.target.value)}
                disabled={invoking || agents.length === 0}
              >
                {agents.map((agent) => (
                  <option key={agent.external_id} value={agent.external_id}>{agent.name}</option>
                ))}
              </select>
            </label>
            <label htmlFor="invoke-timeout">Timeout (seconds)
              <input
                id="invoke-timeout"
                type="number"
                min={1}
                max={300}
                value={timeoutSeconds}
                onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
                disabled={invoking}
              />
            </label>
          </div>
          <label htmlFor="invoke-message">Message
            <textarea
              id="invoke-message"
              className="definition-editor"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              rows={4}
              placeholder="What would you like to ask the remote agent?"
              disabled={invoking}
            />
          </label>
          {invokeError ? <div className="state-card error-card inline-state" role="alert"><strong>Invocation failed</strong><span>{invokeError}</span></div> : null}
          <div className="action-row">
            <button type="submit" disabled={invoking || agents.length === 0}>
              {invoking ? "Invoking…" : "Invoke agent"}
            </button>
          </div>
          {output !== null ? (
            <div>
              <span className="eyebrow">Response</span>
              <pre className="logs-view" aria-label="Agent response">{output}</pre>
            </div>
          ) : null}
          <p className="muted-text">
            External agents are invoked through the A2A protocol via the Control Plane. Managed-agent invocations
            with sessions, streaming, and tool traces require runtime access design (pending).
          </p>
        </form>
      </section>
    </section>
  );
}
