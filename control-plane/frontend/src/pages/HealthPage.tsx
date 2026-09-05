import { useCallback, useEffect, useState } from "react";

import { ApiError, type HealthResponse } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";

export function HealthPage() {
  const client = useControlPlaneClient();
  const [payload, setPayload] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void client.healthReady().then(
      (response) => {
        if (!active) return;
        setPayload(response);
        setLoading(false);
      },
      (caught: unknown) => {
        if (!active) return;
        setError(caught instanceof ApiError ? caught.message : "Unable to read Control Plane health");
        setPayload(null);
        setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, [client, reloadTick]);

  const entries = payload
    ? Object.entries(payload).filter(([, value]) => typeof value !== "object" && typeof value !== "function")
    : [];
  const status = typeof payload?.status === "string" ? payload.status : null;

  return (
    <section aria-labelledby="health-title">
      <div className="page-heading">
        <div><span className="eyebrow">Operations</span><h2 id="health-title">Health</h2></div>
        <button
          type="button"
          className="secondary-button"
          disabled={loading}
          onClick={() => setReloadTick((tick) => tick + 1)}
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {loading && !payload ? <div className="state-card" role="status">Checking readiness…</div> : null}
      {error ? (
        <div className="state-card error-card" role="alert">
          <strong>Control Plane unavailable</strong>
          <span>{error}</span>
        </div>
      ) : null}
      {!error && payload ? (
        <div className="state-card" role="status">
          <strong>Readiness: {status ?? "ready"}</strong>
          <span>Source: GET /health/ready</span>
          {entries.length > 0 ? (
            <dl className="metadata-list">
              {entries.map(([key, value]) => (
                <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>
              ))}
            </dl>
          ) : null}
          <details className="definition-details">
            <summary>Raw readiness payload</summary>
            <pre>{JSON.stringify(payload, null, 2)}</pre>
          </details>
        </div>
      ) : null}
    </section>
  );
}
