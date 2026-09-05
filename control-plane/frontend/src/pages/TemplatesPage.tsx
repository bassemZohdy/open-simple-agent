import { useEffect, useState } from "react";

import { ApiError, type TemplateSummary } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";

export function TemplatesPage() {
  const client = useControlPlaneClient();
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void client.listTemplates().then(
      (response) => {
        if (!active) return;
        setTemplates(response);
        setLoading(false);
      },
      (caught: unknown) => {
        if (!active) return;
        const message = caught instanceof ApiError ? `${caught.code}: ${caught.message}` : "Unable to load templates";
        setError(message);
        setTemplates([]);
        setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, [client, reloadTick]);

  return (
    <section aria-labelledby="templates-title">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Reusable definitions</span>
          <h2 id="templates-title">Templates</h2>
          <p>Built-in, read-only starting points exposed by the Control Plane.</p>
        </div>
        <span className="count-badge" aria-label={`${templates.length} templates`}>{templates.length}</span>
      </div>

      {loading ? <div className="state-card" role="status">Loading templates…</div> : null}
      {!loading && error ? (
        <div className="state-card error-card" role="alert">
          <strong>Templates unavailable</strong>
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={() => setReloadTick((tick) => tick + 1)}>Retry</button>
        </div>
      ) : null}
      {!loading && !error && templates.length === 0 ? <div className="state-card"><strong>No templates available</strong><span>The Control Plane returned an empty template catalog.</span></div> : null}
      {!loading && !error && templates.length > 0 ? (
        <div className="card-grid">
          {templates.map((template) => (
            <article className="catalog-card" key={template.name}>
              <div className="catalog-card-heading">
                <div><span className="eyebrow">Agent template</span><h3>{template.name}</h3></div>
                <span className={`status ${template.memory_enabled ? "status-active" : "status-disabled"}`}>{template.memory_enabled ? "memory" : "stateless"}</span>
              </div>
              <p>{template.description || "No description"}</p>
              <dl className="metadata-list">
                <div><dt>Skills</dt><dd>{template.skills.length > 0 ? template.skills.join(", ") : "—"}</dd></div>
                <div><dt>Memory policy</dt><dd>{template.memory_policy ?? "—"}</dd></div>
              </dl>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
