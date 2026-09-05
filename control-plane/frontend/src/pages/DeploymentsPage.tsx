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

type LifecycleAction = "deploy" | "stop" | "restart" | "rollback" | "status" | "logs" | "invoke-managed";

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
  const [rollbackConfirm, setRollbackConfirm] = useState(false);
  const [managedMessage, setManagedMessage] = useState("");
  const [managedOutput, setManagedOutput] = useState<string | null>(null);
  const [busy, setBusy] = useState<{ action: LifecycleAction; deploymentId?: string } | null>(null);

  // I6: only the buttons tied to the running action's deployment lock; the
  // rest of the page stays interactive. A deploy rewrites the history table,
  // so it locks the whole section.
  function busyLocked(deploymentId?: string): boolean {
    if (busy === null) return false;
    if (busy.action === "deploy") return true;
    if (busy.deploymentId === undefined) return true;
    return busy.deploymentId === deploymentId;
  }
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadAgents = useCallback(async () => {
    setAgentsLoading(true);
    setAgentsError(null);
    try {
      const collected: AgentSummary[] = [];
      const pageSize = 100;
      let offset = 0;
      for (let page = 0; page < 10; page += 1) {
        const response = await client.listAgents({ limit: pageSize, offset });
        collected.push(...response.agents);
        offset += response.agents.length;
        if (response.agents.length < pageSize) break;
      }
      setAgents(collected);
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
    setManagedMessage("");
    setManagedOutput(null);
    setMessage(null);
    setActionError(null);
    setRollbackConfirm(false);
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

  // F14: rollback is destructive, so the first click/Enter arms an explicit
  // confirmation instead of relaunching an older version immediately.
  async function requestRollback() {
    if (!selected) return;
    if (!rollbackConfirm) {
      setRollbackConfirm(true);
      return;
    }
    setRollbackConfirm(false);
    await runLifecycle("rollback", selected.deployment_id);
  }

  async function runLifecycle(action: Exclude<LifecycleAction, "logs">, deploymentId?: string) {
    setBusy({ action, deploymentId });
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
      setBusy(null);
    }
  }

  async function loadLogs(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!selected) return;
    setBusy({ action: "logs", deploymentId: selected.deployment_id });
    setActionError(null);
    try {
      const response = await client.getDeploymentLogs(selected.deployment_id, logTail);
      setLogs(response.lines);
    } catch (caught) {
      setActionError(errorMessage(caught, "Unable to load deployment logs"));
      setLogs(null);
    } finally {
      setBusy(null);
    }
  }

  async function submitManagedInvoke(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected?.invoke_url) return;
    const message = managedMessage.trim();
    if (!message) {
      setActionError("A message is required");
      return;
    }
    setBusy({ action: "invoke-managed", deploymentId: selected.deployment_id });
    setActionError(null);
    try {
      const result = await client.invokeRuntimeEndpoint(selected.invoke_url, message);
      setManagedOutput(result.error ? `${result.output}\n${result.error}`.trim() : result.output);
    } catch (caught) {
      setActionError(errorMessage(caught, "Unable to reach the runtime endpoint"));
    } finally {
      setBusy(null);
    }
  }

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedAgentId) void loadHistory(selectedAgentId);
    else void loadAgents();
  }

  // I1: starting deployments converge without manual refresh clicks.
  const hasStarting = deployments.some((entry) => entry.status === "starting");
  useEffect(() => {
    if (!selectedAgentId || !hasStarting) return;
    const interval = window.setInterval(() => {
      deployments
        .filter((entry) => entry.status === "starting")
        .forEach((entry) => {
          void client
            .getDeployment(entry.deployment_id)
            .then((updated) => {
              setDeployments((current) =>
                current.map((item) => (item.deployment_id === updated.deployment_id ? updated : item)),
              );
              setSelected((current) => (current && current.deployment_id === updated.deployment_id ? updated : current));
            })
            .catch(() => undefined);
        });
    }, 3000);
    return () => window.clearInterval(interval);
  }, [client, deployments, hasStarting, selectedAgentId]);

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

      <form className="filter-bar" onSubmit={submitFilters} noValidate>
        <label htmlFor="deployment-agent">Agent
          <select id="deployment-agent" value={selectedAgentId} onChange={(event) => selectAgent(event.target.value)}>
            <option value="">Select an agent…</option>
            {agents.map((agent) => (
              <option key={agent.agent_id} value={agent.agent_id}>{agent.name} ({agent.status})</option>
            ))}
          </select>
        </label>
        {selectedAgentId ? (
          <button type="submit" disabled={busy !== null}>Refresh history</button>
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
              <button type="button" disabled={busy !== null} onClick={() => void runLifecycle("deploy")}>
                {busy?.action === "deploy" ? "Deploying…" : "Deploy current version"}
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
                  <caption className="sr-only">Deployment history</caption>
                  <thead>
                    <tr><th scope="col">Deployment</th><th scope="col">Version</th><th scope="col">Status</th><th scope="col">Detail</th><th scope="col">Actions</th></tr>
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
                            disabled={busyLocked(selected?.deployment_id)}
                            onClick={() => {
                              setSelected(entry);
                              setLogs(null);
                              setManagedMessage("");
                              setManagedOutput(null);
                              setMessage(null);
                              setActionError(null);
                              setRollbackConfirm(false);
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
                  <button type="button" disabled={busyLocked(selected?.deployment_id)} onClick={() => void runLifecycle("status", selected.deployment_id)}>
                    {busy?.action === "status" && busy.deploymentId === selected.deployment_id ? "Refreshing…" : "Refresh status"}
                  </button>
                  {stoppable(selected.status) ? (
                    <button type="button" disabled={busyLocked(selected?.deployment_id)} onClick={() => void runLifecycle("stop", selected.deployment_id)}>
                      {busy?.action === "stop" && busy.deploymentId === selected.deployment_id ? "Stopping…" : "Stop"}
                    </button>
                  ) : null}
                  <button type="button" disabled={busyLocked(selected?.deployment_id)} onClick={() => void runLifecycle("restart", selected.deployment_id)}>
                    {busy?.action === "restart" && busy.deploymentId === selected.deployment_id ? "Restarting…" : "Restart"}
                  </button>
                  <button type="button" className="danger-button" disabled={busyLocked(selected?.deployment_id)} onClick={() => void requestRollback()}>
                    {busy?.action === "rollback" && busy.deploymentId === selected.deployment_id ? "Rolling back…" : rollbackConfirm ? "Confirm rollback" : "Rollback"}
                  </button>
                </div>
                {rollbackConfirm ? (
                  <div className="confirmation" role="alert">
                    <span>
                      Roll back to version {rollbackVersion.trim() || "(previous version)"}? This relaunches the
                      deployment from an earlier immutable snapshot.
                    </span>
                    <button type="button" className="danger-button" disabled={busyLocked(selected?.deployment_id)} onClick={() => void requestRollback()}>
                      Confirm rollback
                    </button>
                    <button type="button" className="secondary-button" disabled={busyLocked(selected?.deployment_id)} onClick={() => setRollbackConfirm(false)}>
                      Cancel
                    </button>
                  </div>
                ) : null}
                <form
                  className="filter-bar version-form"
                  noValidate
                  onSubmit={(event) => {
                    event.preventDefault();
                    void requestRollback();
                  }}
                >
                  <label htmlFor="rollback-version">Rollback version
                    <input
                      id="rollback-version"
                      value={rollbackVersion}
                      onChange={(event) => {
                        setRollbackVersion(event.target.value);
                        setRollbackConfirm(false);
                      }}
                      placeholder="Leave empty for the previous version"
                      disabled={busyLocked(selected?.deployment_id)}
                    />
                  </label>
                  <button type="submit" className="secondary-button" disabled={busyLocked(selected?.deployment_id)}>
                    Roll back to this version
                  </button>
                </form>
                <p className="muted-text">Rollback relaunches this deployment from an earlier immutable version snapshot.</p>
                {selected.invoke_url ? (
                  <>
                    <div className="section-heading">
                      <div>
                        <span className="eyebrow">Managed invocation</span>
                        <h4>Test message</h4>
                      </div>
                    </div>
                    <form className="filter-bar version-form" onSubmit={(event) => void submitManagedInvoke(event)} noValidate>
                      <label htmlFor="managed-invoke-message">Message
                        <input
                          id="managed-invoke-message"
                          value={managedMessage}
                          onChange={(event) => setManagedMessage(event.target.value)}
                          placeholder="Ask the deployed agent something"
                          disabled={busyLocked(selected?.deployment_id)}
                        />
                      </label>
                      <button type="submit" disabled={busyLocked(selected?.deployment_id)}>
                        {busy?.action === "invoke-managed" ? "Invoking…" : "Send test message"}
                      </button>
                    </form>
                    {managedOutput !== null ? (
                      <pre className="logs-view" aria-label="Managed invocation output">{managedOutput}</pre>
                    ) : null}
                    <p className="muted-text">
                      Sent directly to the runtime endpoint using its own authentication; the Control Plane token is
                      never forwarded.
                    </p>
                  </>
                ) : null}
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
              <form className="filter-bar version-form" onSubmit={(event) => void loadLogs(event)} noValidate>
                <label htmlFor="log-tail">Tail lines
                  <select
                    id="log-tail"
                    value={logTail}
                    onChange={(event) => setLogTail(Number(event.target.value))}
                    disabled={busyLocked(selected?.deployment_id)}
                  >
                    {logTailOptions.map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                  </select>
                </label>
                <button type="submit" disabled={busyLocked(selected?.deployment_id)}>
                  {busy?.action === "logs" ? "Loading…" : "Load logs"}
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
