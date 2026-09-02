import { type FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, type AgentSummary } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";

const statuses = ["", "draft", "active", "disabled", "archived"] as const;

export function AgentsPage() {
  const client = useControlPlaneClient();
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadAgents = useCallback(
    async (nextQuery = query, nextStatus = status) => {
      setLoading(true);
      setError(null);
      try {
        const response = await client.listAgents({ q: nextQuery || undefined, status: nextStatus || undefined, limit: 100 });
        setAgents(response.agents);
        setTotal(response.total);
      } catch (caught) {
        const message = caught instanceof ApiError ? `${caught.code}: ${caught.message}` : "Unable to load agents";
        setError(message);
        setAgents([]);
        setTotal(0);
      } finally {
        setLoading(false);
      }
    },
    [client, query, status],
  );

  useEffect(() => {
    void loadAgents("", "");
  }, [client]);

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadAgents();
  }

  return (
    <section aria-labelledby="agents-title">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Managed agents</span>
          <h2 id="agents-title">Agents</h2>
          <p>Browse Control Plane records without routing invocation traffic through the Control Plane.</p>
        </div>
        <span className="count-badge" aria-label={`${total} matching agents`}>{total}</span>
      </div>

      <form className="filter-bar" onSubmit={submitFilters}>
        <label>
          Search
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name or description" />
        </label>
        <label>
          Status
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            {statuses.map((value) => (
              <option key={value || "all"} value={value}>{value || "All statuses"}</option>
            ))}
          </select>
        </label>
        <button type="submit">Apply filters</button>
      </form>

      {loading ? <div className="state-card" role="status">Loading agents…</div> : null}
      {!loading && error ? (
        <div className="state-card error-card" role="alert">
          <strong>Agent list unavailable</strong>
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={() => void loadAgents()}>Retry</button>
        </div>
      ) : null}
      {!loading && !error && agents.length === 0 ? (
        <div className="state-card">
          <strong>No agents found</strong>
          <span>Create an agent through the API or adjust the current filters.</span>
        </div>
      ) : null}
      {!loading && !error && agents.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Name</th><th>Status</th><th>Version</th><th>Runtime</th><th>Skills</th></tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.agent_id}>
                  <td><strong>{agent.name}</strong><small>{agent.description || "No description"}</small></td>
                  <td><span className={`status status-${agent.status}`}>{agent.status}</span></td>
                  <td>{agent.current_version || "—"}</td>
                  <td>{agent.runtime || "—"}</td>
                  <td>{agent.skills.length > 0 ? agent.skills.join(", ") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
