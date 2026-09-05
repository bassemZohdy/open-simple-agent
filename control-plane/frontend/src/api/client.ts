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

export interface AgentVersionDetail extends AgentVersionSummary {
  definition: Record<string, unknown> | null;
  redacted_fields: string[];
}

export interface CreateAgentVersionRequest {
  version: string;
  change_summary?: string;
}

export interface CreateAgentRequest {
  name: string;
  description?: string;
  template?: string;
  definition?: Record<string, unknown>;
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
  invoke_url: string | null;
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

export interface ExternalAgentSummary {
  external_id: string;
  name: string;
  url: string;
  card_name: string;
  card_version: string;
  skills: Array<Record<string, unknown>>;
  status: string;
  detail: string;
  agent_type: string;
}

export interface ExternalAgentInvocation {
  output: string;
}

export interface RuntimeInvocation {
  output: string;
  invocation_id: string;
  session_id: string | null;
  error: string | null;
}

function normalizedBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/$/, "");
}

// F13: no fetch may hang forever; each call carries an abort deadline.
const DEFAULT_TIMEOUT_MS = 15_000;

interface RequestOptions {
  authenticated?: boolean;
  timeoutMs?: number;
}

function timeoutSignal(timeoutMs: number): AbortSignal | undefined {
  return typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(timeoutMs) : undefined;
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

  async getAgentVersion(agentId: string, versionId: string): Promise<AgentVersionDetail> {
    return this.request<AgentVersionDetail>(
      `${this.agentPath(agentId)}/versions/${encodeURIComponent(versionId)}`,
    );
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

  async createAgent(request: CreateAgentRequest): Promise<AgentSummary> {
    return this.request<AgentSummary>("/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
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

  async invokeRuntimeEndpoint(invokeUrl: string, input: string): Promise<RuntimeInvocation> {
    const url = `${normalizedBaseUrl(invokeUrl)}/v1/invoke`;
    // The runtime endpoint is deployment-specific: the Control Plane token is
    // never forwarded cross-origin. Runtimes behind OSA_AUTH_MODE=require
    // need their own credential story (see ADR-008).
    const response = await this.sendUrl(
      url,
      { method: "POST", body: JSON.stringify({ input }) },
      { authenticated: false, timeoutMs: 35_000 },
    );
    return (await response.json()) as RuntimeInvocation;
  }

  async listAuditEvents(limit = 100): Promise<AuditEvent[]> {
    const query = `?limit=${encodeURIComponent(String(limit))}`;
    return this.request<AuditEvent[]>(`/audit-events${query}`);
  }

  async getMetrics(): Promise<string> {
    return this.requestText("/metrics");
  }

  async listExternalAgents(): Promise<ExternalAgentSummary[]> {
    return this.request<ExternalAgentSummary[]>("/external-agents");
  }

  async invokeExternalAgent(
    externalId: string,
    message: string,
    timeoutSeconds = 30,
  ): Promise<ExternalAgentInvocation> {
    const params = new URLSearchParams({ message, timeout_seconds: String(timeoutSeconds) });
    return this.request<ExternalAgentInvocation>(
      `/external-agents/${encodeURIComponent(externalId)}/invoke?${params.toString()}`,
      { method: "POST" },
      // The server-side timeout is the caller's choice; give the fetch a
      // small margin beyond it.
      { timeoutMs: (timeoutSeconds + 5) * 1000 },
    );
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

  private async request<T>(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<T> {
    const response = await this.sendUrl(`${this.baseUrl}${path}`, init, options);
    return (await response.json()) as T;
  }

  private async requestText(path: string, init: RequestInit = {}, options: RequestOptions = {}): Promise<string> {
    const response = await this.sendUrl(`${this.baseUrl}${path}`, init, options);
    return response.text();
  }

  private async sendUrl(
    url: string,
    init: RequestInit,
    options: RequestOptions = {},
  ): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined && init.body !== null) {
      headers.set("Content-Type", "application/json");
    }
    if (options.authenticated !== false) {
      const token = this.getToken();
      if (token) {
        headers.set("Authorization", `Bearer ${token}`);
      }
    }

    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const response = await fetch(url, { ...init, headers, signal: timeoutSignal(timeoutMs) }).catch(
      (caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "TimeoutError") {
          throw new ApiError(0, "request_timeout", `Request timed out after ${Math.round(timeoutMs / 1000)}s`);
        }
        throw caught;
      },
    );
    if (response.status === 401) {
      // F11: a rejected token must not keep retrying; AuthContext clears it.
      window.dispatchEvent(new CustomEvent("osa:unauthorized"));
    }
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
