import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, type AgentSummary, type TemplateSummary } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";
import { useLocale } from "../i18n/LocaleContext";
import { SearchField } from "../components/SearchField";

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
  const { t } = useLocale();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
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
        // F3: /agents pages with limit/offset; a single capped request made
        // agents beyond the first page unreachable. Bound the loop so a
        // huge catalog cannot pin the page.
        const collected: AgentSummary[] = [];
        const pageSize = 100;
        const maxPages = 10;
        let offset = 0;
        let totalCount = 0;
        for (let page = 0; page < maxPages; page += 1) {
          const response = await client.listAgents({
            q: nextQuery || undefined,
            status: nextStatus || undefined,
            limit: pageSize,
            offset,
          });
          collected.push(...response.agents);
          totalCount = response.total;
          offset += response.agents.length;
          if (response.agents.length < pageSize || collected.length >= response.total) break;
        }
        setAgents(collected);
        setTotal(totalCount);
      } catch (caught) {
        setError(errorMessage(caught, t("Unable to load agents")));
        setAgents([]);
        setTotal(0);
      } finally {
        setLoading(false);
      }
    },
    [client, query, status, t],
  );

  useEffect(() => {
    void loadAgents("", "");
  }, [client, t]);

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
        if (!cancelled) setCreateError(errorMessage(caught, t("Unable to load the agent to clone")));
      });
    return () => {
      cancelled = true;
    };
  }, [showCreate, cloneOf, client, t]);

  useEffect(() => {
    if (!showCreate || source !== "template" || templates.length > 0) return;
    let cancelled = false;
    client
      .listTemplates()
      .then((items) => {
        if (!cancelled) setTemplates(items);
      })
      .catch((caught) => {
        if (!cancelled) setCreateError(errorMessage(caught, t("Unable to load templates")));
      });
    return () => {
      cancelled = true;
    };
  }, [showCreate, source, templates.length, client, t]);

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
    // F5: stale ?create=1 / ?cloneOf would re-enter create/clone mode on the
    // next "Create agent" click in the same visit.
    if (searchParams.get("create") === "1" || searchParams.get("cloneOf")) {
      setSearchParams({}, { replace: true });
    }
  }

  function parseDefinition(): Record<string, unknown> | undefined {
    if (source !== "definition") return undefined;
    const trimmed = definitionText.trim();
    if (!trimmed) {
      setCreateError(t("A definition is required in definition mode"));
      return undefined;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      setCreateError(t("Definition is not valid JSON"));
      return undefined;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setCreateError(t("Definition must be a JSON object"));
      return undefined;
    }
    const metadata = (parsed as Record<string, unknown>).metadata;
    const definitionName =
      typeof metadata === "object" && metadata !== null && !Array.isArray(metadata)
        ? (metadata as Record<string, unknown>).name
        : undefined;
    if (definitionName !== name.trim()) {
      setCreateError(t('Definition metadata.name must match the agent name "{name}"', { name: name.trim() }));
      return undefined;
    }
    return parsed as Record<string, unknown>;
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setCreateError(t("Agent name is required"));
      return;
    }
    if (source === "template" && !templateName) {
      setCreateError(t("Select a template or switch the configuration source to draft"));
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
      setCreateError(errorMessage(caught, t("Unable to create agent")));
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
          <span className="eyebrow">{t("Managed agents")}</span>
          <h2 id="agents-title">{t("Agents")}</h2>
          <p>{t("Browse Control Plane records without routing invocation traffic through the Control Plane.")}</p>
        </div>
        <span className="count-badge" aria-label={t("{count} matching agents", { count: total })}>{total}</span>
      </div>

      <form className="filter-bar" onSubmit={submitFilters} noValidate>
        <label>
          <SearchField id="agent-search" label={t("Search")} value={query} onChange={setQuery} placeholder={t("Name or description")} clearLabel={t("Clear search")} />
        </label>
        <label>
          {t("Status")}
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            {statuses.map((value) => (
              <option key={value || "all"} value={value}>{t(value || "All statuses")}</option>
            ))}
          </select>
        </label>
        <button type="submit">{t("Apply filters")}</button>
        {showCreate ? (
          <button type="button" className="secondary-button" onClick={closeCreate}>{t("Close create")}</button>
        ) : (
          <button type="button" onClick={openCreate}>{t("Create agent")}</button>
        )}
      </form>

      {showCreate ? (
        <form className="detail-card create-panel" onSubmit={(event) => void submitCreate(event)} aria-labelledby="create-agent-title" noValidate>
          <div className="card-heading">
            <div>
              <span className="eyebrow">{cloneSource ? t("Cloning {name}", { name: cloneSource.name }) : t("New agent")}</span>
              <h3 id="create-agent-title">{t("Create agent")}</h3>
            </div>
          </div>
          {cloneSource ? (
            <p className="muted-text">
              {t("Metadata is copied. Agent definitions are write-only in the Control Plane, so choose a template or paste a definition for the copy.")}
            </p>
          ) : null}
          <div className="filter-bar">
            <label htmlFor="create-name">{t("Name")}
              <input id="create-name" value={name} onChange={(event) => setName(event.target.value)} placeholder={t("e.g. support-agent")} disabled={creating} />
            </label>
            <label htmlFor="create-description">{t("Description")}
              <input id="create-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder={t("What does this agent do?")} disabled={creating} />
            </label>
            <label htmlFor="create-source">{t("Configuration source")}
              <select id="create-source" value={source} onChange={(event) => setSource(event.target.value as CreateSource)} disabled={creating}>
                <option value="draft">{t("Empty draft (no definition)")}</option>
                <option value="template">{t("Built-in template")}</option>
                <option value="definition">{t("JSON definition")}</option>
              </select>
            </label>
            {source === "template" ? (
              <label htmlFor="create-template">{t("Template")}
                <select id="create-template" value={templateName} onChange={(event) => setTemplateName(event.target.value)} disabled={creating}>
                  <option value="">{t("Select a template…")}</option>
                  {templates.map((template) => (
                    <option key={template.name} value={template.name}>{template.name}</option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
          {source === "definition" ? (
            <label htmlFor="create-definition">{t("Definition (JSON)")}
              <textarea
                id="create-definition"
                className="logs-view definition-editor resize-none"
                value={definitionText}
                onChange={(event) => setDefinitionText(event.target.value)}
                rows={10}
                placeholder={DEFINITION_TEMPLATE}
                disabled={creating}
                spellCheck={false}
              />
            </label>
          ) : null}
          {createError ? <div className="state-card error-card inline-state" role="alert"><strong>{t("Create failed")}</strong><span>{createError}</span></div> : null}
          <div className="action-row">
            <button type="submit" disabled={creating}>{creating ? t("Creating…") : t("Create agent")}</button>
            <button type="button" className="secondary-button" onClick={closeCreate} disabled={creating}>{t("Cancel")}</button>
          </div>
        </form>
      ) : null}

      {loading ? <div className="state-card" role="status">{t("Loading agents…")}</div> : null}
      {!loading && error ? (
        <div className="state-card error-card" role="alert">
          <strong>{t("Agent list unavailable")}</strong>
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={() => void loadAgents()}>{t("Retry")}</button>
        </div>
      ) : null}
      {!loading && !error && agents.length === 0 ? (
        <div className="state-card">
          <strong>{t("No agents found")}</strong>
          <span>{t("Create an agent through the API or adjust the current filters.")}</span>
        </div>
      ) : null}
      {!loading && !error && agents.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">{t("Managed agents")}</caption>
            <thead>
              <tr><th scope="col">{t("Name")}</th><th scope="col">{t("Status")}</th><th scope="col">{t("Version")}</th><th scope="col">{t("Runtime")}</th><th scope="col">{t("Skills")}</th></tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.agent_id}>
                  <td><Link className="agent-link" to={`/agents/${encodeURIComponent(agent.agent_id)}`}><strong>{agent.name}</strong></Link><small>{agent.description || t("No description")}</small></td>
                  <td><span className={`status status-${agent.status}`}>{t(agent.status)}</span></td>
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
