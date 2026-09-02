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

  async healthReady(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health/ready");
  }

  private async request<T>(path: string): Promise<T> {
    const headers = new Headers({ Accept: "application/json" });
    const token = this.getToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    const response = await fetch(`${this.baseUrl}${path}`, { headers });
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
    return (await response.json()) as T;
  }
}

export const defaultApiBaseUrl = import.meta.env.VITE_OSA_API_BASE_URL ?? "http://localhost:8000";
