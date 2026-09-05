import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError, type AuditEvent } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";
import { formatTimestamp } from "../lib/format";

const limitOptions = [50, 100, 200, 500] as const;

interface MetricSample {
  name: string;
  labels: string;
  value: string;
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? `${caught.code}: ${caught.message}` : fallback;
}

function parseMetricSample(text: string): MetricSample[] {
  return text
    .split("\n")
    .filter((line) => line.trim() && !line.startsWith("#"))
    .map((line) => {
      const separator = line.lastIndexOf(" ");
      if (separator <= 0) return null;
      const sample = line.slice(0, separator);
      const openLabel = sample.indexOf("{");
      if (openLabel === -1) return { name: sample, labels: "", value: line.slice(separator + 1) };
      const closeLabel = sample.lastIndexOf("}");
      if (closeLabel === -1 || closeLabel < openLabel) return null;
      return {
        name: sample.slice(0, openLabel),
        labels: sample.slice(openLabel + 1, closeLabel),
        value: line.slice(separator + 1),
      };
    })
    .filter((sample): sample is MetricSample => sample !== null);
}

function formatDetail(detail: AuditEvent["detail"]): string {
  const entries = Object.keys(detail);
  if (entries.length === 0) return "—";
  const serialized = JSON.stringify(detail);
  return serialized.length > 80 ? `${serialized.slice(0, 77)}…` : serialized;
}

export function AuditPage() {
  const client = useControlPlaneClient();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [actionFilter, setActionFilter] = useState("");
  const [limit, setLimit] = useState<number>(100);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [metrics, setMetrics] = useState<string | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [metricsError, setMetricsError] = useState<string | null>(null);

  const loadEvents = useCallback(
    async (nextLimit = limit) => {
      setLoading(true);
      setError(null);
      try {
        setEvents(await client.listAuditEvents(nextLimit));
      } catch (caught) {
        setError(errorMessage(caught, "Unable to load audit events"));
        setEvents([]);
      } finally {
        setLoading(false);
      }
    },
    [client, limit],
  );

  const loadMetrics = useCallback(async () => {
    setMetricsLoading(true);
    setMetricsError(null);
    try {
      setMetrics(await client.getMetrics());
    } catch (caught) {
      setMetricsError(errorMessage(caught, "Unable to load metrics"));
      setMetrics(null);
    } finally {
      setMetricsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void loadEvents();
    void loadMetrics();
  }, [loadEvents, loadMetrics]);

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadEvents();
  }

  const needle = actionFilter.trim().toLowerCase();
  const visibleEvents = needle
    ? events.filter((entry) => entry.action.toLowerCase().includes(needle))
    : events;
  const samples = metrics === null ? [] : parseMetricSample(metrics);

  return (
    <section aria-labelledby="audit-title">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Operational oversight</span>
          <h2 id="audit-title">Audit &amp; metrics</h2>
          <p>Recent Control Plane audit events and bounded operational metrics.</p>
        </div>
        <span className="count-badge" aria-label={`${visibleEvents.length} shown of the ${events.length} most recent events loaded`}>{visibleEvents.length}<small>/{events.length} loaded</small></span>
      </div>

      <form className="filter-bar" onSubmit={submitFilters} noValidate>
        <label htmlFor="audit-action">Action
          <input
            id="audit-action"
            value={actionFilter}
            onChange={(event) => setActionFilter(event.target.value)}
            placeholder="e.g. deployment or agent"
          />
        </label>
        <label htmlFor="audit-limit">Limit
          <select
            id="audit-limit"
            value={limit}
            onChange={(event) => {
              const nextLimit = Number(event.target.value);
              setLimit(nextLimit);
              void loadEvents(nextLimit);
            }}
          >
            {limitOptions.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </label>
        <button type="submit">Refresh</button>
      </form>

      {loading ? <div className="state-card" role="status">Loading audit events…</div> : null}
      {!loading && error ? (
        <div className="state-card error-card" role="alert">
          <strong>Audit events unavailable</strong>
          <span>{error}</span>
          <button type="button" className="secondary-button" onClick={() => void loadEvents()}>Retry</button>
        </div>
      ) : null}
      {!loading && !error && visibleEvents.length === 0 ? (
        <div className="state-card">
          <strong>No audit events</strong>
          <span>Control Plane operations are recorded here as they happen.</span>
        </div>
      ) : null}
      {visibleEvents.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Recent audit events</caption>
            <thead>
              <tr><th scope="col">Time</th><th scope="col">Actor</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col">Tenant</th><th scope="col">Detail</th></tr>
            </thead>
            <tbody>
              {visibleEvents.map((entry) => (
                <tr key={entry.event_id}>
                  <td><time dateTime={entry.occurred_at}>{formatTimestamp(entry.occurred_at)}</time></td>
                  <td>{entry.actor}</td>
                  <td><code>{entry.action}</code></td>
                  <td><code>{entry.target}</code></td>
                  <td>{entry.tenant_id ?? "Shared scope"}</td>
                  <td title={JSON.stringify(entry.detail)}>{formatDetail(entry.detail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <section className="detail-section" aria-labelledby="metrics-title">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Operational metrics</span>
            <h3 id="metrics-title">Prometheus metrics</h3>
          </div>
          <button type="button" disabled={metricsLoading} onClick={() => void loadMetrics()}>
            {metricsLoading ? "Refreshing…" : "Refresh metrics"}
          </button>
        </div>
        {metricsLoading ? <div className="state-card" role="status">Loading metrics…</div> : null}
        {!metricsLoading && metricsError ? (
          <div className="state-card error-card" role="alert">
            <strong>Metrics unavailable</strong>
            <span>{metricsError}</span>
            <button type="button" className="secondary-button" onClick={() => void loadMetrics()}>Retry</button>
          </div>
        ) : null}
        {!metricsLoading && !metricsError && samples.length === 0 ? (
          <div className="state-card">
            <strong>No metrics recorded</strong>
            <span>Counters appear once the Control Plane handles traffic.</span>
          </div>
        ) : null}
        {samples.length > 0 ? (
          <>
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Prometheus metric samples</caption>
                <thead>
                  <tr><th scope="col">Metric</th><th scope="col">Labels</th><th scope="col">Value</th></tr>
                </thead>
                <tbody>
                  {samples.map((sample) => (
                    <tr key={`${sample.name}${sample.labels}`}>
                      <td><code>{sample.name}</code></td>
                      <td>{sample.labels || "—"}</td>
                      <td>{sample.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details>
              <summary>Raw Prometheus exposition</summary>
              <pre className="logs-view" aria-label="Raw metrics exposition">{metrics}</pre>
            </details>
          </>
        ) : null}
      </section>
    </section>
  );
}
