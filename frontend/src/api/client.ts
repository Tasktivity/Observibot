const BASE = '/api';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? 'Request failed');
  }
  const text = await res.text();
  if (!text) {
    return {} as T;
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(res.status, 'Invalid JSON response from server');
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export interface User {
  id: string;
  email: string;
  is_admin: boolean;
}

export interface Insight {
  id: string;
  severity: string;
  title: string;
  summary: string;
  details: string;
  recommended_actions: string[];
  related_metrics: string[];
  related_tables: string[];
  confidence: number;
  source: string;
  is_hypothesis: boolean;
  created_at: string;
}

export interface Metric {
  id: string;
  connector_name: string;
  metric_name: string;
  value: number;
  labels: Record<string, string>;
  collected_at: string;
}

export interface Widget {
  id: string;
  user_id: string | null;
  widget_type: string;
  title: string;
  config: Record<string, unknown> | null;
  layout: Record<string, unknown> | null;
  data_source: Record<string, unknown> | null;
  schema_version: number;
  pinned: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ChatResponse {
  answer: string;
  widget_plan: Record<string, unknown> | null;
  vega_lite_spec: Record<string, unknown> | null;
  sql_query: string | null;
  execution_ms: number | null;
  domains_hit: string[];
  warnings: string[];
}

export const api = {
  auth: {
    me: () => request<User>('/auth/me'),
    login: (email: string, password: string) =>
      request<User>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      }),
    register: (email: string, password: string) =>
      request<User>('/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      }),
    logout: () => request<void>('/auth/logout', { method: 'POST' }),
  },
  insights: {
    list: (limit = 20) => request<Insight[]>(`/insights?limit=${limit}`),
    ack: (id: string) =>
      request<{ id: string; acknowledged: boolean }>(`/insights/${id}/ack`, {
        method: 'PATCH',
      }),
  },
  metrics: {
    recent: (limit = 100) => request<Metric[]>(`/metrics/recent?limit=${limit}`),
    history: (name: string, hours = 24) =>
      request<Metric[]>(`/metrics/${name}/history?hours=${hours}`),
  },
  widgets: {
    list: () => request<Widget[]>('/widgets'),
    create: (data: { widget_type: string; title: string; config?: unknown }) =>
      request<Widget>('/widgets', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: string, data: Record<string, unknown>) =>
      request<Widget>(`/widgets/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    remove: (id: string) =>
      request<void>(`/widgets/${id}`, { method: 'DELETE' }),
    updateLayout: (items: { id: string; x: number; y: number; w: number; h: number }[]) =>
      request<{ updated: number }>('/widgets/layout', {
        method: 'PATCH',
        body: JSON.stringify({ items }),
      }),
  },
  chat: {
    query: (question: string) =>
      request<ChatResponse>('/chat/query', {
        method: 'POST',
        body: JSON.stringify({ question }),
      }),
  },
  system: {
    health: () => request<{ status: string; version: string }>('/system/health'),
    status: () => request<Record<string, unknown>>('/system/status'),
    cost: () => request<{ calls: number; total_tokens: number; cost_usd: number }>('/system/cost'),
  },
  discovery: {
    model: () => request<Record<string, unknown>>('/discovery/model'),
  },
};
