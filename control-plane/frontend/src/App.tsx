import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { AgentsPage } from "./pages/AgentsPage";
import { AgentDetailPage } from "./pages/AgentDetailPage";
import { HealthPage } from "./pages/HealthPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ResourcesPage } from "./pages/ResourcesPage";
import { TemplatesPage } from "./pages/TemplatesPage";

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/agents" replace />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/agents/:agentId" element={<AgentDetailPage />} />
        <Route path="/health" element={<HealthPage />} />
        <Route path="/templates" element={<TemplatesPage />} />
        <Route path="/resources" element={<ResourcesPage />} />
        <Route path="/deployments" element={<PlaceholderPage title="Deployments" />} />
        <Route path="/audit" element={<PlaceholderPage title="Audit" />} />
        <Route path="*" element={<PlaceholderPage title="Page not found" />} />
      </Routes>
    </AppShell>
  );
}
