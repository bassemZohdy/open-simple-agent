import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, type AgentSummary, type AgentVersionDetail, type AgentVersionSummary } from "../api/client";
import { useLocale } from "../i18n/LocaleContext";
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
  const { t } = useLocale();
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
  const [snapshot, setSnapshot] = useState<AgentVersionDetail | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState<string | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  const loadAgent = useCallback(async () => {
    if (!agentId) {
      setError(t("Agent identifier is missing"));
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    setSnapshot(null);
    setSnapshotError(null);
    try {
      const [nextAgent, nextVersions] = await Promise.all([
        client.getAgent(agentId),
        client.listAgentVersions(agentId),
      ]);
      setAgent(nextAgent);
      setVersions(nextVersions);
    } catch (caught) {
      setError(errorMessage(caught, t("Unable to load agent details")));
      setAgent(null);
      setVersions([]);
    } finally {
      setLoading(false);
    }
  }, [agentId, client, t]);

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
      const pastAction = action === "activate" ? "activated" : action === "disable" ? "disabled" : "archived";
      setMessage(t("Agent {action} successfully.", { action: t(pastAction) }));
    } catch (caught) {
      setError(errorMessage(caught, t("Unable to {action} agent", { action: t(action) })));
    } finally {
      setBusyAction(null);
    }
  }

  async function submitVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!agentId) return;
    const normalizedVersion = version.trim();
    if (!normalizedVersion) {
      setVersionError(t("Version is required"));
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
      setMessage(t("Version {version} created successfully.", { version: normalizedVersion }));
    } catch (caught) {
      setVersionError(errorMessage(caught, t("Unable to create version")));
    } finally {
      setCreatingVersion(false);
    }
  }

  async function inspectSnapshot(entry: AgentVersionSummary) {
    if (!agentId || !entry.has_definition) return;
    if (snapshot?.version_id === entry.version_id) {
      setSnapshot(null);
      setSnapshotError(null);
      return;
    }
    setSnapshotLoading(entry.version_id);
    setSnapshotError(null);
    try {
      setSnapshot(await client.getAgentVersion(agentId, entry.version_id));
    } catch (caught) {
      setSnapshot(null);
      setSnapshotError(errorMessage(caught, t("Unable to load the version snapshot")));
    } finally {
      setSnapshotLoading(null);
    }
  }

  const lifecycleActions = agent ? actionsForStatus(agent.status) : [];

  return (
    <section aria-labelledby="agent-detail-title">
      <Link className="back-link" to="/agents">{t("← Back to agents")}</Link>

      {loading ? <div className="state-card" role="status">{t("Loading agent details…")}</div> : null}
      {!loading && error && !agent ? (
        <div className="state-card error-card" role="alert">
          <strong>{t("Agent details unavailable")}</strong>
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={() => void loadAgent()}>{t("Retry")}</button>
        </div>
      ) : null}

      {!loading && agent ? (
        <>
          <div className="page-heading detail-heading">
            <div>
              <span className="eyebrow">{t("Agent detail")}</span>
              <h2 id="agent-detail-title">{agent.name}</h2>
              <p>{agent.description || t("No description")}</p>
            </div>
            <span className={`status status-${agent.status}`}>{t(agent.status)}</span>
          </div>

          {error ? <div className="state-card error-card inline-state" role="alert"><strong>{t("Action failed")}</strong><span>{error}</span></div> : null}
          {message ? <div className="state-card success-card inline-state" role="status">{message}</div> : null}

          <div className="detail-grid">
            <article className="detail-card">
              <div className="card-heading"><div><span className="eyebrow">{t("Lifecycle")}</span><h3>{t("Manage agent")}</h3></div></div>
              <p>{t("Transitions are validated by the Control Plane and only allowed for the current agent state.")}</p>
              <div className="action-row">
                {lifecycleActions.filter((action) => action !== "archive").map((action) => (
                  <button key={action} type="button" disabled={busyAction !== null || creatingVersion} onClick={() => void applyLifecycle(action)}>
                    {busyAction === action ? `${t(actionLabel(action))}…` : t(actionLabel(action))}
                  </button>
                ))}
                <Link className="agent-link" to={`/agents?create=1&cloneOf=${encodeURIComponent(agent.agent_id)}`}>{t("Clone agent")}</Link>
                {lifecycleActions.includes("archive") && !confirmingArchive ? (
                  <button type="button" className="danger-button" disabled={busyAction !== null || creatingVersion} onClick={() => setConfirmingArchive(true)}>{t("Archive")}</button>
                ) : null}
              </div>
              {confirmingArchive ? (
                <div className="confirmation" role="group" aria-label={t("Confirm archive")}>
                  <span>{t("Archive is terminal. Confirm this action?")}</span>
                  <button type="button" className="danger-button" disabled={busyAction !== null} onClick={() => void applyLifecycle("archive")}>{t("Confirm archive")}</button>
                  <button type="button" className="secondary-button" disabled={busyAction !== null} onClick={() => setConfirmingArchive(false)}>{t("Cancel")}</button>
                </div>
              ) : null}
              {lifecycleActions.length === 0 ? <span className="muted-text">{t("No further lifecycle actions are available.")}</span> : null}
            </article>

            <article className="detail-card">
              <div className="card-heading"><div><span className="eyebrow">{t("Configuration")}</span><h3>{t("Agent metadata")}</h3></div></div>
              <dl className="metadata-list detail-metadata">
                <div><dt>{t("Agent ID")}</dt><dd>{agent.agent_id}</dd></div>
                <div><dt>{t("Current version")}</dt><dd>{agent.current_version || "—"}</dd></div>
                <div><dt>{t("Runtime")}</dt><dd>{agent.runtime || "—"}</dd></div>
                <div><dt>{t("Tenant")}</dt><dd>{agent.tenant_id ?? t("Shared scope")}</dd></div>
                <div><dt>{t("Skills")}</dt><dd>{agent.skills.length > 0 ? agent.skills.join(", ") : "—"}</dd></div>
                <div><dt>{t("Labels")}</dt><dd>{Object.entries(agent.labels).map(([key, value]) => `${key}=${value}`).join(", ") || "—"}</dd></div>
                <div><dt>{t("Deployments")}</dt><dd><Link className="agent-link" to={`/deployments?agent=${encodeURIComponent(agent.agent_id)}`}>{t("View deployment history")}</Link></dd></div>
              </dl>
            </article>
          </div>

          <section className="detail-section" aria-labelledby="versions-title">
            <div className="section-heading"><div><span className="eyebrow">{t("Immutable snapshots")}</span><h3 id="versions-title">{t("Version history")}</h3></div><span className="count-badge" aria-label={t("{count} versions", { count: versions.length })}>{versions.length}</span></div>
            <form className="filter-bar version-form" onSubmit={(event) => void submitVersion(event)} noValidate>
                <label htmlFor="agent-version">{t("Version")}
                <input id="agent-version" value={version} onChange={(event) => setVersion(event.target.value)} placeholder={t("e.g. 2.0.0")} disabled={creatingVersion} />
              </label>
              <label htmlFor="version-summary">{t("Change summary")}
                <input id="version-summary" value={changeSummary} onChange={(event) => setChangeSummary(event.target.value)} placeholder={t("What changed?")} disabled={creatingVersion} />
              </label>
              <button type="submit" disabled={creatingVersion}>{creatingVersion ? t("Creating…") : t("Create version")}</button>
            </form>
            {versionError ? <div className="state-card error-card inline-state" role="alert"><strong>{t("Version creation failed")}</strong><span>{versionError}</span></div> : null}
            {versions.length === 0 ? <div className="state-card"><strong>{t("No version snapshots")}</strong><span>{t("Create the first immutable snapshot from the current agent definition.")}</span></div> : null}
            {versions.length > 0 ? (
              <div className="version-list">
                {versions.map((entry) => (
                  <article className="version-card" key={entry.version_id}>
                    <div className="version-card-heading"><div><span className="eyebrow">{t("Version")}</span><h4>{entry.version}</h4></div>{entry.version === agent.current_version ? <span className="status status-active">{t("Current")}</span> : null}</div>
                    <p>{entry.change_summary || t("No change summary")}</p>
                    <dl className="metadata-list version-metadata">
                      <div><dt>{t("Created")}</dt><dd><time dateTime={entry.created_at}>{formatTimestamp(entry.created_at)}</time></dd></div>
                      <div><dt>{t("Created by")}</dt><dd>{entry.created_by || t("Control Plane")}</dd></div>
                      <div><dt>{t("Snapshot")}</dt><dd>{entry.has_definition ? t("Definition available") : t("No definition")}</dd></div>
                    </dl>
                    {entry.has_definition ? (
                      <button
                        type="button"
                        className="secondary-button snapshot-button"
                        aria-expanded={snapshot?.version_id === entry.version_id}
                        onClick={() => void inspectSnapshot(entry)}
                        disabled={snapshotLoading !== null}
                      >
                        {snapshotLoading === entry.version_id
                          ? t("Loading snapshot…")
                          : snapshot?.version_id === entry.version_id
                            ? t("Hide safe snapshot")
                            : t("View safe snapshot")}
                      </button>
                    ) : null}
                    {snapshot?.version_id === entry.version_id ? (
                      <div className="definition-details snapshot-details">
                        <p>
                          {t("This immutable definition is safe for inspection. Secret-like values are redacted before leaving the Control Plane.")}
                        </p>
                        {snapshot.redacted_fields.length > 0 ? (
                          <small>{t("Redacted fields: {fields}", { fields: snapshot.redacted_fields.join(", ") })}</small>
                        ) : null}
                        <pre aria-label={t("Safe definition for version {version}", { version: entry.version })}>
                          {JSON.stringify(snapshot.definition, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            ) : null}
            {snapshotError ? <div className="state-card error-card inline-state" role="alert"><strong>{t("Snapshot unavailable")}</strong><span>{snapshotError}</span></div> : null}
          </section>
        </>
      ) : null}
    </section>
  );
}
