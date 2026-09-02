export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export interface AgentSummary {
  agent_id: string;
  name: string;
  description: string;
  status: "draft" | "active" | "disabled" | "archived" | string;
  current_version: string;
  runtime: string;
  tenant_id: string | null;
  skills: string[];
  labels: Record<string, string>;
}

export interface AgentListResponse {
  agents: AgentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface AgentVersionSummary {
  version_id: string;
  version: string;
  created_at: string;
  created_by: string;
  change_summary: string;
  has_definition: boolean;
}

export interface CreateAgentVersionRequest {
  version: string;
  change_summary?: string;
}

export type DeploymentStatus = "starting" | "running" | "stopped" | "failed" | string;

export interface DeploymentSummary {
  deployment_id: string;
  agent_id: string;
  agent_name: string;
  tenant_id: string | null;
  version: string;
  status: DeploymentStatus;
  detail: string;
}

export interface DeploymentLogsResponse {
  deployment_id: string;
  lines: string[];
}

export interface HealthResponse {
  status: string;
  [key: string]: unknown;
}

export interface AgentListFilters {
  q?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export type ResourceKind = "Model" | "Tool" | "Skill" | "Mcp" | "MemoryPolicy";

export interface ResourceEnvelope {
  apiVersion: string;
  kind: string;
  spec: Record<string, unknown>;
}

export interface ResourceListResponse {
  kind: string;
  total: number;
  resources: ResourceEnvelope[];
}

export interface TemplateSummary {
  name: string;
  description: string;
  skills: string[];
  memory_enabled: boolean;
  memory_policy: string | null;
}

export interface AuditEvent {
  event_id: string;
  actor: string;
  action: string;
  target: string;
  occurred_at: string;
  tenant_id: string | null;
  detail: Record<string, unknown>;
}

function normalizedBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/$/, "");
}

export class ControlPlaneClient {
  private readonly baseUrl: string;
  private readonly getToken: () => string | null;

  constructor(baseUrl: string, getToken: () => string | null) {
    this.baseUrl = normalizedBaseUrl(baseUrl);
    this.getToken = getToken;
  }

  async listAgents(filters: AgentListFilters = {}): Promise<AgentListResponse> {
    const params = new URLSearchParams();
    if (filters.q) params.set("q", filters.q);
    if (filters.status) params.set("status", filters.status);
    if (filters.limit !== undefined) params.set("limit", String(filters.limit));
    if (filters.offset !== undefined) params.set("offset", String(filters.offset));
    const query = params.size > 0 ? `?${params.toString()}` : "";
    return this.request<AgentListResponse>(`/agents${query}`);
  }

  async getAgent(agentId: string): Promise<AgentSummary> {
    return this.request<AgentSummary>(this.agentPath(agentId));
  }

  async listAgentVersions(agentId: string): Promise<AgentVersionSummary[]> {
    return this.request<AgentVersionSummary[]>(`${this.agentPath(agentId)}/versions`);
  }

  async createAgentVersion(agentId: string, request: CreateAgentVersionRequest): Promise<AgentSummary> {
    return this.request<AgentSummary>(`${this.agentPath(agentId)}/versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  }

  async activateAgent(agentId: string): Promise<AgentSummary> {
    return this.transitionAgent(agentId, "activate");
  }

  async disableAgent(agentId: string): Promise<AgentSummary> {
    return this.transitionAgent(agentId, "disable");
  }

  async archiveAgent(agentId: string): Promise<AgentSummary> {
    return this.transitionAgent(agentId, "archive");
  }

  async deployAgent(agentId: string): Promise<DeploymentSummary> {
    return this.request<DeploymentSummary>(`${this.agentPath(agentId)}/deploy`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  async listAgentDeployments(agentId: string): Promise<DeploymentSummary[]> {
    return this.request<DeploymentSummary[]>(`${this.agentPath(agentId)}/deployments`);
  }

  async getDeployment(deploymentId: string): Promise<DeploymentSummary> {
    return this.request<DeploymentSummary>(this.deploymentPath(deploymentId));
  }

  async stopDeployment(deploymentId: string): Promise<DeploymentSummary> {
    return this.request<DeploymentSummary>(`${this.deploymentPath(deploymentId)}/stop`, { method: "POST" });
  }

  async restartDeployment(deploymentId: string): Promise<DeploymentSummary> {
    return this.request<DeploymentSummary>(`${this.deploymentPath(deploymentId)}/restart`, { method: "POST" });
  }

  async rollbackDeployment(deploymentId: string, version?: string): Promise<DeploymentSummary> {
    const query = version ? `?version=${encodeURIComponent(version)}` : "";
    return this.request<DeploymentSummary>(`${this.deploymentPath(deploymentId)}/rollback${query}`, {
      method: "POST",
    });
  }

  async getDeploymentLogs(deploymentId: string, tail = 200): Promise<DeploymentLogsResponse> {
    const query = `?tail=${encodeURIComponent(String(tail))}`;
    return this.request<DeploymentLogsResponse>(`${this.deploymentPath(deploymentId)}/logs${query}`);
  }

  async listAuditEvents(limit = 100): Promise<AuditEvent[]> {
    const query = `?limit=${encodeURIComponent(String(limit))}`;
    return this.request<AuditEvent[]>(`/audit-events${query}`);
  }

  async getMetrics(): Promise<string> {
    return this.requestText("/metrics");
  }

  async listTemplates(): Promise<TemplateSummary[]> {
    return this.request<TemplateSummary[]>("/templates");
  }

  async listResources(kind: ResourceKind, q?: string): Promise<ResourceListResponse> {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    const query = params.size > 0 ? `?${params.toString()}` : "";
    return this.request<ResourceListResponse>(`/resources/${encodeURIComponent(kind)}${query}`);
  }

  async healthReady(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health/ready");
  }

  private agentPath(agentId: string): string {
    return `/agents/${encodeURIComponent(agentId)}`;
  }

  private deploymentPath(deploymentId: string): string {
    return `/deployments/${encodeURIComponent(deploymentId)}`;
  }

  private async transitionAgent(agentId: string, action: "activate" | "disable" | "archive"): Promise<AgentSummary> {
    return this.request<AgentSummary>(`${this.agentPath(agentId)}/${action}`, { method: "POST" });
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.send(path, init);
    return (await response.json()) as T;
  }

  private async requestText(path: string, init: RequestInit = {}): Promise<string> {
    const response = await this.send(path, init);
    return response.text();
  }

  private async send(path: string, init: RequestInit): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined && init.body !== null) {
      headers.set("Content-Type", "application/json");
    }
    const token = this.getToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    if (!response.ok) {
      let body: ApiErrorBody = {};
      try {
        body = (await response.json()) as ApiErrorBody;
      } catch {
        // Preserve a stable client error even if a proxy returns a non-JSON body.
      }
      const code = body.error?.code ?? `http_${response.status}`;
      const message = body.error?.message ?? `Control Plane request failed with HTTP ${response.status}`;
      throw new ApiError(response.status, code, message);
    }
    return response;
  }
}

export const defaultApiBaseUrl = import.meta.env.VITE_OSA_API_BASE_URL ?? "http://localhost:8000";
