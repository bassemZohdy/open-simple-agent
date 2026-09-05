import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, type AgentSummary, type AgentVersionSummary } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";
import { formatTimestamp } from "../lib/format";

type LifecycleAction = "activate" | "disable" | "archive";

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? `${caught.code}: ${caught.message}` : fallback;
}

function actionsForStatus(status: AgentSummary["status"]): LifecycleAction[] {
  if (status === "draft") return ["activate", "archive"];
  if (status === "active") return ["disable", "archive"];
  if (status === "disabled") return ["activate", "archive"];
  return [];
}

function actionLabel(action: LifecycleAction): string {
  return action.charAt(0).toUpperCase() + action.slice(1);
}

export function AgentDetailPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const client = useControlPlaneClient();
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [versions, setVersions] = useState<AgentVersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<LifecycleAction | null>(null);
  const [confirmingArchive, setConfirmingArchive] = useState(false);
  const [version, setVersion] = useState("");
  const [changeSummary, setChangeSummary] = useState("");
  const [versionError, setVersionError] = useState<string | null>(null);
  const [creatingVersion, setCreatingVersion] = useState(false);

  const loadAgent = useCallback(async () => {
    if (!agentId) {
      setError("Agent identifier is missing");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [nextAgent, nextVersions] = await Promise.all([
        client.getAgent(agentId),
        client.listAgentVersions(agentId),
      ]);
      setAgent(nextAgent);
      setVersions(nextVersions);
    } catch (caught) {
      setError(errorMessage(caught, "Unable to load agent details"));
      setAgent(null);
      setVersions([]);
    } finally {
      setLoading(false);
    }
  }, [agentId, client]);

  useEffect(() => {
    void loadAgent();
  }, [loadAgent]);

  async function applyLifecycle(action: LifecycleAction) {
    if (!agentId) return;
    setBusyAction(action);
    setError(null);
    setMessage(null);
    try {
      let updated: AgentSummary;
      if (action === "activate") updated = await client.activateAgent(agentId);
      else if (action === "disable") updated = await client.disableAgent(agentId);
      else updated = await client.archiveAgent(agentId);
      setAgent(updated);
      setConfirmingArchive(false);
      setMessage(`Agent ${action}d successfully.`);
    } catch (caught) {
      setError(errorMessage(caught, `Unable to ${action} agent`));
    } finally {
      setBusyAction(null);
    }
  }

  async function submitVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!agentId) return;
    const normalizedVersion = version.trim();
    if (!normalizedVersion) {
      setVersionError("Version is required");
      return;
    }
    setCreatingVersion(true);
    setVersionError(null);
    setError(null);
    setMessage(null);
    try {
      const updated = await client.createAgentVersion(agentId, {
        version: normalizedVersion,
        change_summary: changeSummary.trim(),
      });
      const nextVersions = await client.listAgentVersions(agentId);
      setAgent(updated);
      setVersions(nextVersions);
      setVersion("");
      setChangeSummary("");
      setMessage(`Version ${normalizedVersion} created successfully.`);
    } catch (caught) {
      setVersionError(errorMessage(caught, "Unable to create version"));
    } finally {
      setCreatingVersion(false);
    }
  }

  const lifecycleActions = agent ? actionsForStatus(agent.status) : [];

  return (
    <section aria-labelledby="agent-detail-title">
      <Link className="back-link" to="/agents">← Back to agents</Link>

      {loading ? <div className="state-card" role="status">Loading agent details…</div> : null}
      {!loading && error && !agent ? (
        <div className="state-card error-card" role="alert">
          <strong>Agent details unavailable</strong>
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={() => void loadAgent()}>Retry</button>
        </div>
      ) : null}

      {!loading && agent ? (
        <>
          <div className="page-heading detail-heading">
            <div>
              <span className="eyebrow">Agent detail</span>
              <h2 id="agent-detail-title">{agent.name}</h2>
              <p>{agent.description || "No description"}</p>
            </div>
            <span className={`status status-${agent.status}`}>{agent.status}</span>
          </div>

          {error ? <div className="state-card error-card inline-state" role="alert"><strong>Action failed</strong><span>{error}</span></div> : null}
          {message ? <div className="state-card success-card inline-state" role="status">{message}</div> : null}

          <div className="detail-grid">
            <article className="detail-card">
              <div className="card-heading"><div><span className="eyebrow">Lifecycle</span><h3>Manage agent</h3></div></div>
              <p>Transitions are validated by the Control Plane and only allowed for the current agent state.</p>
              <div className="action-row">
                {lifecycleActions.filter((action) => action !== "archive").map((action) => (
                  <button key={action} type="button" disabled={busyAction !== null || creatingVersion} onClick={() => void applyLifecycle(action)}>
                    {busyAction === action ? `${actionLabel(action)}…` : actionLabel(action)}
                  </button>
                ))}
                <Link className="agent-link" to={`/agents?create=1&cloneOf=${encodeURIComponent(agent.agent_id)}`}>Clone agent</Link>
                {lifecycleActions.includes("archive") && !confirmingArchive ? (
                  <button type="button" className="danger-button" disabled={busyAction !== null || creatingVersion} onClick={() => setConfirmingArchive(true)}>Archive</button>
                ) : null}
              </div>
              {confirmingArchive ? (
                <div className="confirmation" role="group" aria-label="Confirm archive">
                  <span>Archive is terminal. Confirm this action?</span>
                  <button type="button" className="danger-button" disabled={busyAction !== null} onClick={() => void applyLifecycle("archive")}>Confirm archive</button>
                  <button type="button" className="secondary-button" disabled={busyAction !== null} onClick={() => setConfirmingArchive(false)}>Cancel</button>
                </div>
              ) : null}
              {lifecycleActions.length === 0 ? <span className="muted-text">No further lifecycle actions are available.</span> : null}
            </article>

            <article className="detail-card">
              <div className="card-heading"><div><span className="eyebrow">Configuration</span><h3>Agent metadata</h3></div></div>
              <dl className="metadata-list detail-metadata">
                <div><dt>Agent ID</dt><dd>{agent.agent_id}</dd></div>
                <div><dt>Current version</dt><dd>{agent.current_version || "—"}</dd></div>
                <div><dt>Runtime</dt><dd>{agent.runtime || "—"}</dd></div>
                <div><dt>Tenant</dt><dd>{agent.tenant_id ?? "Shared scope"}</dd></div>
                <div><dt>Skills</dt><dd>{agent.skills.length > 0 ? agent.skills.join(", ") : "—"}</dd></div>
                <div><dt>Labels</dt><dd>{Object.entries(agent.labels).map(([key, value]) => `${key}=${value}`).join(", ") || "—"}</dd></div>
                <div><dt>Deployments</dt><dd><Link className="agent-link" to={`/deployments?agent=${encodeURIComponent(agent.agent_id)}`}>View deployment history</Link></dd></div>
              </dl>
            </article>
          </div>

          <section className="detail-section" aria-labelledby="versions-title">
            <div className="section-heading"><div><span className="eyebrow">Immutable snapshots</span><h3 id="versions-title">Version history</h3></div><span className="count-badge" aria-label={`${versions.length} versions`}>{versions.length}</span></div>
            <form className="filter-bar version-form" onSubmit={(event) => void submitVersion(event)}>
              <label htmlFor="agent-version">Version
                <input id="agent-version" value={version} onChange={(event) => setVersion(event.target.value)} placeholder="e.g. 2.0.0" disabled={creatingVersion} />
              </label>
              <label htmlFor="version-summary">Change summary
                <input id="version-summary" value={changeSummary} onChange={(event) => setChangeSummary(event.target.value)} placeholder="What changed?" disabled={creatingVersion} />
              </label>
              <button type="submit" disabled={creatingVersion}>{creatingVersion ? "Creating…" : "Create version"}</button>
            </form>
            {versionError ? <div className="state-card error-card inline-state" role="alert"><strong>Version creation failed</strong><span>{versionError}</span></div> : null}
            {versions.length === 0 ? <div className="state-card"><strong>No version snapshots</strong><span>Create the first immutable snapshot from the current agent definition.</span></div> : null}
            {versions.length > 0 ? (
              <div className="version-list">
                {versions.map((entry) => (
                  <article className="version-card" key={entry.version_id}>
                    <div className="version-card-heading"><div><span className="eyebrow">Version</span><h4>{entry.version}</h4></div>{entry.version === agent.current_version ? <span className="status status-active">Current</span> : null}</div>
                    <p>{entry.change_summary || "No change summary"}</p>
                    <dl className="metadata-list version-metadata">
                      <div><dt>Created</dt><dd><time dateTime={entry.created_at}>{formatTimestamp(entry.created_at)}</time></dd></div>
                      <div><dt>Created by</dt><dd>{entry.created_by || "Control Plane"}</dd></div>
                      <div><dt>Snapshot</dt><dd>{entry.has_definition ? "Definition available" : "No definition"}</dd></div>
                    </dl>
                  </article>
                ))}
              </div>
            ) : null}
          </section>
        </>
      ) : null}
    </section>
  );
}
