import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, type AgentSummary, type TemplateSummary } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";

const statuses = ["", "draft", "active", "disabled", "archived"] as const;

type CreateSource = "draft" | "template" | "definition";

const DEFINITION_TEMPLATE = `{
  "apiVersion": "osa/v1alpha1",
  "kind": "Agent",
  "metadata": { "name": "AGENT_NAME" },
  "spec": { "instruction": "You are a helpful assistant." }
}`;

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? `${caught.code}: ${caught.message}` : fallback;
}

export function AgentsPage() {
  const client = useControlPlaneClient();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(() => searchParams.get("create") === "1");
  const cloneOf = searchParams.get("cloneOf");
  const [cloneSource, setCloneSource] = useState<AgentSummary | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [source, setSource] = useState<CreateSource>("draft");
  const [templateName, setTemplateName] = useState("");
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [definitionText, setDefinitionText] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const loadAgents = useCallback(
    async (nextQuery = query, nextStatus = status) => {
      setLoading(true);
      setError(null);
      try {
        const response = await client.listAgents({ q: nextQuery || undefined, status: nextStatus || undefined, limit: 100 });
        setAgents(response.agents);
        setTotal(response.total);
      } catch (caught) {
        setError(errorMessage(caught, "Unable to load agents"));
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

  useEffect(() => {
    if (!showCreate || !cloneOf) return;
    let cancelled = false;
    client
      .getAgent(cloneOf)
      .then((agent) => {
        if (cancelled) return;
        setCloneSource(agent);
        setName(`${agent.name}-copy`);
        setDescription(agent.description);
      })
      .catch((caught) => {
        if (!cancelled) setCreateError(errorMessage(caught, "Unable to load the agent to clone"));
      });
    return () => {
      cancelled = true;
    };
  }, [showCreate, cloneOf, client]);

  useEffect(() => {
    if (!showCreate || source !== "template" || templates.length > 0) return;
    let cancelled = false;
    client
      .listTemplates()
      .then((items) => {
        if (!cancelled) setTemplates(items);
      })
      .catch((caught) => {
        if (!cancelled) setCreateError(errorMessage(caught, "Unable to load templates"));
      });
    return () => {
      cancelled = true;
    };
  }, [showCreate, source, templates.length, client]);

  function openCreate() {
    setShowCreate(true);
    setCreateError(null);
  }

  function closeCreate() {
    setShowCreate(false);
    setCreateError(null);
    setCloneSource(null);
    setName("");
    setDescription("");
    setSource("draft");
    setTemplateName("");
    setDefinitionText("");
  }

  function parseDefinition(): Record<string, unknown> | undefined {
    if (source !== "definition") return undefined;
    const trimmed = definitionText.trim();
    if (!trimmed) {
      setCreateError("A definition is required in definition mode");
      return undefined;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      setCreateError("Definition is not valid JSON");
      return undefined;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setCreateError("Definition must be a JSON object");
      return undefined;
    }
    const metadata = (parsed as Record<string, unknown>).metadata;
    const definitionName =
      typeof metadata === "object" && metadata !== null && !Array.isArray(metadata)
        ? (metadata as Record<string, unknown>).name
        : undefined;
    if (definitionName !== name.trim()) {
      setCreateError(`Definition metadata.name must match the agent name "${name.trim()}"`);
      return undefined;
    }
    return parsed as Record<string, unknown>;
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setCreateError("Agent name is required");
      return;
    }
    const definition = parseDefinition();
    if (source === "definition" && definition === undefined) return;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await client.createAgent({
        name: trimmedName,
        description: description.trim(),
        ...(source === "template" && templateName ? { template: templateName } : {}),
        ...(source === "definition" && definition ? { definition } : {}),
      });
      closeCreate();
      navigate(`/agents/${encodeURIComponent(created.agent_id)}`);
    } catch (caught) {
      setCreateError(errorMessage(caught, "Unable to create agent"));
    } finally {
      setCreating(false);
    }
  }

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
        {showCreate ? (
          <button type="button" className="secondary-button" onClick={closeCreate}>Close create</button>
        ) : (
          <button type="button" onClick={openCreate}>Create agent</button>
        )}
      </form>

      {showCreate ? (
        <form className="detail-card create-panel" onSubmit={(event) => void submitCreate(event)} aria-labelledby="create-agent-title">
          <div className="card-heading">
            <div>
              <span className="eyebrow">{cloneSource ? `Cloning ${cloneSource.name}` : "New agent"}</span>
              <h3 id="create-agent-title">Create agent</h3>
            </div>
          </div>
          {cloneSource ? (
            <p className="muted-text">
              Metadata is copied. Agent definitions are write-only in the Control Plane, so choose a template or
              paste a definition for the copy.
            </p>
          ) : null}
          <div className="filter-bar">
            <label htmlFor="create-name">Name
              <input id="create-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. support-agent" disabled={creating} />
            </label>
            <label htmlFor="create-description">Description
              <input id="create-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What does this agent do?" disabled={creating} />
            </label>
            <label htmlFor="create-source">Configuration source
              <select id="create-source" value={source} onChange={(event) => setSource(event.target.value as CreateSource)} disabled={creating}>
                <option value="draft">Empty draft (no definition)</option>
                <option value="template">Built-in template</option>
                <option value="definition">JSON definition</option>
              </select>
            </label>
            {source === "template" ? (
              <label htmlFor="create-template">Template
                <select id="create-template" value={templateName} onChange={(event) => setTemplateName(event.target.value)} disabled={creating}>
                  <option value="">Select a template…</option>
                  {templates.map((template) => (
                    <option key={template.name} value={template.name}>{template.name}</option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
          {source === "definition" ? (
            <label htmlFor="create-definition">Definition (JSON)
              <textarea
                id="create-definition"
                className="logs-view definition-editor"
                value={definitionText}
                onChange={(event) => setDefinitionText(event.target.value)}
                rows={10}
                placeholder={DEFINITION_TEMPLATE}
                disabled={creating}
                spellCheck={false}
              />
            </label>
          ) : null}
          {createError ? <div className="state-card error-card inline-state" role="alert"><strong>Create failed</strong><span>{createError}</span></div> : null}
          <div className="action-row">
            <button type="submit" disabled={creating}>{creating ? "Creating…" : "Create agent"}</button>
            <button type="button" className="secondary-button" onClick={closeCreate} disabled={creating}>Cancel</button>
          </div>
        </form>
      ) : null}

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
                  <td><Link className="agent-link" to={`/agents/${encodeURIComponent(agent.agent_id)}`}><strong>{agent.name}</strong></Link><small>{agent.description || "No description"}</small></td>
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
