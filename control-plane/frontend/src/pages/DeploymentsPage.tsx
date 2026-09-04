import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  ApiError,
  type AgentSummary,
  type DeploymentStatus,
  type DeploymentSummary,
} from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";

const logTailOptions = [100, 200, 500, 1000] as const;

type LifecycleAction = "deploy" | "stop" | "restart" | "rollback" | "status" | "logs";

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? `${caught.code}: ${caught.message}` : fallback;
}

function statusClass(status: DeploymentStatus): string {
  if (status === "running") return "status status-active";
  if (status === "starting") return "status status-draft";
  if (status === "failed") return "status status-failed";
  return "status status-disabled";
}

function stoppable(status: DeploymentStatus): boolean {
  return status === "running" || status === "starting";
}

export function DeploymentsPage() {
  const client = useControlPlaneClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedAgentId = searchParams.get("agent") ?? "";

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [agentsError, setAgentsError] = useState<string | null>(null);

  const [deployments, setDeployments] = useState<DeploymentSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const [selected, setSelected] = useState<DeploymentSummary | null>(null);
  const [logs, setLogs] = useState<string[] | null>(null);
  const [logTail, setLogTail] = useState<number>(200);
  const [rollbackVersion, setRollbackVersion] = useState("");
  const [busyAction, setBusyAction] = useState<LifecycleAction | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadAgents = useCallback(async () => {
    setAgentsLoading(true);
    setAgentsError(null);
    try {
      const response = await client.listAgents({ limit: 100 });
      setAgents(response.agents);
    } catch (caught) {
      setAgentsError(errorMessage(caught, "Unable to load agents"));
      setAgents([]);
    } finally {
      setAgentsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  const loadHistory = useCallback(
    async (agentId: string) => {
      setHistoryLoading(true);
      setHistoryError(null);
      try {
        setDeployments(await client.listAgentDeployments(agentId));
      } catch (caught) {
        setHistoryError(errorMessage(caught, "Unable to load deployment history"));
        setDeployments([]);
      } finally {
        setHistoryLoading(false);
      }
    },
    [client],
  );

  useEffect(() => {
    setSelected(null);
    setLogs(null);
    setMessage(null);
    setActionError(null);
    if (selectedAgentId) {
      void loadHistory(selectedAgentId);
    } else {
      setDeployments([]);
      setHistoryError(null);
      setHistoryLoading(false);
    }
  }, [selectedAgentId, loadHistory]);

  function selectAgent(agentId: string) {
    setSearchParams(agentId ? { agent: agentId } : {});
  }

  function replaceDeployment(updated: DeploymentSummary) {
    setDeployments((current) => current.map((entry) => (entry.deployment_id === updated.deployment_id ? updated : entry)));
    setSelected((current) => (current && current.deployment_id === updated.deployment_id ? updated : current));
  }

  async function runLifecycle(action: Exclude<LifecycleAction, "logs">, deploymentId?: string) {
    setBusyAction(action);
    setMessage(null);
    setActionError(null);
    try {
      let updated: DeploymentSummary;
      if (action === "deploy") {
        updated = await client.deployAgent(selectedAgentId);
        setDeployments((current) => [updated, ...current]);
        setSelected(updated);
        setLogs(null);
        setMessage(`Deployment ${updated.deployment_id} started for version ${updated.version}.`);
      } else if (deploymentId) {
        if (action === "stop") updated = await client.stopDeployment(deploymentId);
        else if (action === "restart") updated = await client.restartDeployment(deploymentId);
        else if (action === "rollback") updated = await client.rollbackDeployment(deploymentId, rollbackVersion.trim() || undefined);
        else updated = await client.getDeployment(deploymentId);
        replaceDeployment(updated);
        if (action === "rollback") {
          setRollbackVersion("");
          setMessage(`Deployment rolled back to version ${updated.version}.`);
        }
      }
    } catch (caught) {
      setActionError(errorMessage(caught, `Unable to ${action} deployment`));
    } finally {
      setBusyAction(null);
    }
  }

  async function loadLogs(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!selected) return;
    setBusyAction("logs");
    setActionError(null);
    try {
      const response = await client.getDeploymentLogs(selected.deployment_id, logTail);
      setLogs(response.lines);
    } catch (caught) {
      setActionError(errorMessage(caught, "Unable to load deployment logs"));
      setLogs(null);
    } finally {
      setBusyAction(null);
    }
  }

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedAgentId) void loadHistory(selectedAgentId);
    else void loadAgents();
  }

  const selectedAgent = agents.find((agent) => agent.agent_id === selectedAgentId) ?? null;

  return (
    <section aria-labelledby="deployments-title">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Runtime operations</span>
          <h2 id="deployments-title">Deployments</h2>
          <p>Launch versioned agents through the Control Plane and manage their lifecycle, status, and logs.</p>
        </div>
        {selectedAgentId ? (
          <span className="count-badge" aria-label={`${deployments.length} deployments`}>{deployments.length}</span>
        ) : null}
      </div>

      <form className="filter-bar" onSubmit={submitFilters}>
        <label htmlFor="deployment-agent">Agent
          <select id="deployment-agent" value={selectedAgentId} onChange={(event) => selectAgent(event.target.value)}>
            <option value="">Select an agent…</option>
            {agents.map((agent) => (
              <option key={agent.agent_id} value={agent.agent_id}>{agent.name} ({agent.status})</option>
            ))}
          </select>
        </label>
        {selectedAgentId ? (
          <button type="submit" disabled={busyAction !== null}>Refresh history</button>
        ) : null}
      </form>

      {agentsLoading ? <div className="state-card" role="status">Loading agents…</div> : null}
      {!agentsLoading && agentsError ? (
        <div className="state-card error-card" role="alert">
          <strong>Agent list unavailable</strong>
          <span>{agentsError}</span>
          <button type="button" className="secondary-button" onClick={() => void loadAgents()}>Retry</button>
        </div>
      ) : null}
      {!agentsLoading && !agentsError && agents.length === 0 ? (
        <div className="state-card">
          <strong>No agents found</strong>
          <span>Create an agent through the API to deploy it.</span>
        </div>
      ) : null}

      {!agentsLoading && !agentsError && !selectedAgentId && agents.length > 0 ? (
        <div className="state-card">
          <strong>Select an agent</strong>
          <span>Deployment history is scoped to one agent; choose it above to view and manage its deployments.</span>
        </div>
      ) : null}

      {selectedAgentId && !agentsLoading && !agentsError ? (
        <>
          {message ? <div className="state-card success-card inline-state" role="status">{message}</div> : null}
          {actionError ? <div className="state-card error-card inline-state" role="alert"><strong>Action failed</strong><span>{actionError}</span></div> : null}
          {historyError ? (
            <div className="state-card error-card" role="alert">
              <strong>Deployment history unavailable</strong>
              <span>{historyError}</span>
              <button type="button" className="secondary-button" onClick={() => void loadHistory(selectedAgentId)}>Retry</button>
            </div>
          ) : null}

          <section className="detail-section" aria-labelledby="deployment-history-title">
            <div className="section-heading">
              <div>
                <span className="eyebrow">{selectedAgent ? selectedAgent.name : "Agent"}</span>
                <h3 id="deployment-history-title">Deployment history</h3>
              </div>
              <button type="button" disabled={busyAction !== null} onClick={() => void runLifecycle("deploy")}>
                {busyAction === "deploy" ? "Deploying…" : "Deploy current version"}
              </button>
            </div>
            {historyLoading ? <div className="state-card" role="status">Loading deployments…</div> : null}
            {!historyLoading && !historyError && deployments.length === 0 ? (
              <div className="state-card">
                <strong>No deployments yet</strong>
                <span>Deploy the current agent version to start its runtime through the configured provider.</span>
              </div>
            ) : null}
            {deployments.length > 0 ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Deployment</th><th>Version</th><th>Status</th><th>Detail</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {deployments.map((entry) => (
                      <tr key={entry.deployment_id}>
                        <td><code>{entry.deployment_id}</code></td>
                        <td>{entry.version}</td>
                        <td><span className={statusClass(entry.status)}>{entry.status}</span></td>
                        <td>{entry.detail || "—"}</td>
                        <td>
                          <button
                            type="button"
                            className="secondary-button"
                            disabled={busyAction !== null}
                            onClick={() => {
                              setSelected(entry);
                              setLogs(null);
                              setMessage(null);
                              setActionError(null);
                            }}
                          >
                            Manage
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </section>

          {selected ? (
            <section className="detail-section" aria-labelledby="deployment-detail-title">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">Deployment detail</span>
                  <h3 id="deployment-detail-title"><code>{selected.deployment_id}</code></h3>
                </div>
                <span className={statusClass(selected.status)}>{selected.status}</span>
              </div>
              <article className="detail-card">
                <dl className="metadata-list detail-metadata">
                  <div><dt>Agent</dt><dd><Link className="agent-link" to={`/agents/${encodeURIComponent(selected.agent_id)}`}>{selected.agent_name}</Link></dd></div>
                  <div><dt>Version</dt><dd>{selected.version}</dd></div>
                  <div><dt>Tenant</dt><dd>{selected.tenant_id ?? "Shared scope"}</dd></div>
                  <div><dt>Detail</dt><dd>{selected.detail || "—"}</dd></div>
                  <div><dt>Runtime endpoint</dt><dd>{selected.invoke_url ? <a className="agent-link" href={selected.invoke_url}>{selected.invoke_url}</a> : "Not configured"}</dd></div>
                </dl>
                <div className="action-row">
                  <button type="button" disabled={busyAction !== null} onClick={() => void runLifecycle("status", selected.deployment_id)}>
                    {busyAction === "status" ? "Refreshing…" : "Refresh status"}
                  </button>
                  {stoppable(selected.status) ? (
                    <button type="button" disabled={busyAction !== null} onClick={() => void runLifecycle("stop", selected.deployment_id)}>
                      {busyAction === "stop" ? "Stopping…" : "Stop"}
                    </button>
                  ) : null}
                  <button type="button" disabled={busyAction !== null} onClick={() => void runLifecycle("restart", selected.deployment_id)}>
                    {busyAction === "restart" ? "Restarting…" : "Restart"}
                  </button>
                  <button type="button" className="danger-button" disabled={busyAction !== null} onClick={() => void runLifecycle("rollback", selected.deployment_id)}>
                    {busyAction === "rollback" ? "Rolling back…" : "Rollback"}
                  </button>
                </div>
                <form className="filter-bar version-form" onSubmit={(event) => event.preventDefault()}>
                  <label htmlFor="rollback-version">Rollback version
                    <input
                      id="rollback-version"
                      value={rollbackVersion}
                      onChange={(event) => setRollbackVersion(event.target.value)}
                      placeholder="Leave empty for the previous version"
                      disabled={busyAction !== null}
                    />
                  </label>
                </form>
                <p className="muted-text">Rollback relaunches this deployment from an earlier immutable version snapshot.</p>
              </article>
            </section>
          ) : null}

          {selected ? (
            <section className="detail-section" aria-labelledby="deployment-logs-title">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">Captured output</span>
                  <h3 id="deployment-logs-title">Logs</h3>
                </div>
              </div>
              <form className="filter-bar version-form" onSubmit={(event) => void loadLogs(event)}>
                <label htmlFor="log-tail">Tail lines
                  <select
                    id="log-tail"
                    value={logTail}
                    onChange={(event) => setLogTail(Number(event.target.value))}
                    disabled={busyAction !== null}
                  >
                    {logTailOptions.map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                  </select>
                </label>
                <button type="submit" disabled={busyAction !== null}>
                  {busyAction === "logs" ? "Loading…" : "Load logs"}
                </button>
              </form>
              {logs === null ? (
                <div className="state-card">
                  <strong>No logs loaded</strong>
                  <span>Load the bounded captured output for this deployment.</span>
                </div>
              ) : logs.length === 0 ? (
                <div className="state-card">
                  <strong>No captured output</strong>
                  <span>The deployment has not produced any captured log lines yet.</span>
                </div>
              ) : (
                <pre className="logs-view" aria-label="Deployment log output">{logs.join("\n")}</pre>
              )}
            </section>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
