import { type FormEvent, useEffect, useState } from "react";

import { ApiError, type ResourceEnvelope, type ResourceKind } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";

const resourceKinds: ResourceKind[] = ["Model", "Tool", "Skill", "Mcp", "MemoryPolicy"];
const summaryFields = ["provider", "model", "transport", "scope", "enabled", "retention_days"] as const;

function stringField(spec: Record<string, unknown>, field: string): string | null {
  const value = spec[field];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function resourceName(resource: ResourceEnvelope): string {
  return stringField(resource.spec, "name") ?? "Unnamed resource";
}

function displayValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  return "";
}

export function ResourcesPage() {
  const client = useControlPlaneClient();
  const [kind, setKind] = useState<ResourceKind>("Model");
  const [query, setQuery] = useState("");
  const [resources, setResources] = useState<ResourceEnvelope[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let active = true;
    setQuery("");
    setLoading(true);
    setError(null);
    void client.listResources(kind).then(
      (response) => {
        if (!active) return;
        setResources(response.resources);
        setTotal(response.total);
        setLoading(false);
      },
      (caught: unknown) => {
        if (!active) return;
        const message = caught instanceof ApiError ? `${caught.code}: ${caught.message}` : `Unable to load ${kind} resources`;
        setError(message);
        setResources([]);
        setTotal(0);
        setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, [client, kind, reloadTick]);

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await client.listResources(kind, query || undefined);
      setResources(response.resources);
      setTotal(response.total);
    } catch (caught) {
      const message = caught instanceof ApiError ? `${caught.code}: ${caught.message}` : `Unable to load ${kind} resources`;
      setError(message);
      setResources([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section aria-labelledby="resources-title">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Runtime catalogs</span>
          <h2 id="resources-title">Resources</h2>
          <p>Tenant-scoped model, tool, skill, MCP, and memory-policy definitions returned by the Control Plane.</p>
        </div>
        <span className="count-badge" aria-label={`${total} matching resources`}>{total}</span>
      </div>

      <div className="segmented-control" role="group" aria-label="Resource kind">
        {resourceKinds.map((resourceKind) => (
          <button key={resourceKind} type="button" className={kind === resourceKind ? "segment active" : "segment"} aria-pressed={kind === resourceKind} onClick={() => setKind(resourceKind)}>{resourceKind}</button>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => void submitSearch(event)}>
        <label>
          Search {kind} names
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Resource name" />
        </label>
        <button type="submit">Search</button>
      </form>

      {loading ? <div className="state-card" role="status">Loading {kind} resources…</div> : null}
      {!loading && error ? (
        <div className="state-card error-card" role="alert">
          <strong>{kind} resources unavailable</strong>
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={() => setReloadTick((tick) => tick + 1)}>Retry</button>
        </div>
      ) : null}
      {!loading && !error && resources.length === 0 ? <div className="state-card"><strong>No {kind} resources found</strong><span>Adjust the search or register resources through the Control Plane API.</span></div> : null}
      {!loading && !error && resources.length > 0 ? (
        <div className="card-grid">
          {resources.map((resource) => {
            const name = resourceName(resource);
            const description = stringField(resource.spec, "description");
            const highlights = summaryFields.flatMap((field) => {
              const value = displayValue(resource.spec[field]);
              return value ? [{ field, value }] : [];
            });
            return (
              <article className="catalog-card" key={`${resource.kind}:${name}`}>
                <div className="catalog-card-heading">
                  <div><span className="eyebrow">{resource.kind}</span><h3>{name}</h3></div>
                  <span className="api-version">{resource.apiVersion}</span>
                </div>
                <p>{description ?? "No description"}</p>
                {highlights.length > 0 ? <dl className="metadata-list">{highlights.map(({ field, value }) => <div key={field}><dt>{field.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}</dl> : null}
                <details className="definition-details"><summary>View safe definition</summary><pre>{JSON.stringify(resource.spec, null, 2)}</pre></details>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
