import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import { useControlPlaneClient } from "../api/useControlPlaneClient";

export function HealthPage() {
  const client = useControlPlaneClient();
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void client.healthReady().then(
      (response) => {
        if (active) setStatus(response.status ?? "ready");
      },
      (caught: unknown) => {
        if (!active) return;
        setError(caught instanceof ApiError ? caught.message : "Unable to read Control Plane health");
      },
    );
    return () => {
      active = false;
    };
  }, [client]);

  return (
    <section aria-labelledby="health-title">
      <div className="page-heading">
        <div><span className="eyebrow">Operations</span><h2 id="health-title">Health</h2></div>
      </div>
      <div className={`state-card ${error ? "error-card" : ""}`} role={error ? "alert" : "status"}>
        <strong>{error ? "Control Plane unavailable" : status ? `Readiness: ${status}` : "Checking readiness…"}</strong>
        {error ? <span>{error}</span> : <span>Source: GET /health/ready</span>}
      </div>
    </section>
  );
}
