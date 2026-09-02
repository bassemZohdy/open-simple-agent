import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { AgentsPage } from "./pages/AgentsPage";
import { HealthPage } from "./pages/HealthPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/agents" replace />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/health" element={<HealthPage />} />
        <Route path="/templates" element={<PlaceholderPage title="Templates" />} />
        <Route path="/resources" element={<PlaceholderPage title="Resources" />} />
        <Route path="/deployments" element={<PlaceholderPage title="Deployments" />} />
        <Route path="/audit" element={<PlaceholderPage title="Audit" />} />
        <Route path="*" element={<PlaceholderPage title="Page not found" />} />
      </Routes>
    </AppShell>
  );
}
