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

export interface RecurrenceContext {
  count: number;
  first_seen: string;
  last_seen: string;
  common_hours: number[];
}

export interface DiagnosticEvidence {
  hypothesis: string;
  sql: string;
  row_count: number;
  rows: Array<Record<string, unknown>>;
  explanation: string;
  executed_at: string;
  error: string | null;
}

export interface CorrelationEvidence {
  metric_name: string;
  change_event_id: string;
  change_type: string;
  change_summary: string;
  time_delta_seconds: number;
  severity_score: number;
}

export interface EvidenceBundle {
  recurrence?: Record<string, RecurrenceContext & { metric_name?: string }>;
  correlations?: CorrelationEvidence[];
  diagnostics?: DiagnosticEvidence[];
}

export interface DiagnosticActivity {
  last_24h: {
    runs: number;
    skipped_cooldown: number;
    timed_out: number;
    queries_issued: number;
    queries_succeeded: number;
    queries_rejected: number;
  };
  recent_runs: Array<{
    run_id: string | null;
    occurred_at: string;
    event_type: string;
    summary: string | null;
  }>;
}

export interface SeasonalCoverage {
  total_buckets: number;
  trusted_buckets: number;
  pct_trusted: number;
  oldest_bucket_age_days: number | null;
  min_weeks_observed: number;
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
  recurrence_context?: RecurrenceContext | null;
  evidence?: EvidenceBundle | null;
}

export interface ObservationEvent {
  id: string;
  event_type: string;
  occurred_at: string;
  severity: string | null;
  source: string;
  agent: string;
  subject: string;
  summary: string | null;
  ref_table: string;
  ref_id: string;
  run_id: string | null;
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
  session_id: string;
  fallback: boolean;
}

export interface InsightFeedback {
  id: number;
  insight_id: string;
  user_id: string | null;
  outcome: string;
  note: string | null;
  created_at: string;
}

export interface MonitorIntervals {
  collection_interval_seconds: number;
  analysis_interval_seconds: number;
}

export interface SemanticFact {
  id: string;
  fact_type: string;
  concept: string;
  claim: string;
  tables: string[];
  columns: string[];
  sql_condition: string | null;
  source: string;
  confidence: number;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface FactUpdate {
  is_active?: boolean;
  claim?: string;
  confidence?: number;
}

export interface BusinessContextEntry {
  key: string;
  value: string;
}

export interface FeedbackSummary {
  total: number;
  since_days: number;
  by_outcome: Record<string, number>;
  recent: {
    id: number;
    insight_id: string;
    insight_title: string;
    outcome: string;
    note: string | null;
    created_at: string;
  }[];
}

export interface KnowledgeStats {
  total_facts: number;
  active_facts: number;
  inactive_facts: number;
  facts_by_source: Record<string, number>;
  facts_by_type: Record<string, number>;
  total_feedback: number;
  feedback_by_outcome: Record<string, number>;
  total_events: number;
  code_intelligence_status: string;
  last_indexed_commit: string | null;
  last_index_time: string | null;
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
    feedback: (id: string, outcome: string, note?: string) =>
      request<InsightFeedback>(`/insights/${id}/feedback`, {
        method: 'POST',
        body: JSON.stringify({ outcome, ...(note ? { note } : {}) }),
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
    query: (question: string, sessionId?: string) =>
      request<ChatResponse>('/chat/query', {
        method: 'POST',
        body: JSON.stringify({ question, ...(sessionId ? { session_id: sessionId } : {}) }),
      }),
  },
  system: {
    health: () => request<{ status: string; version: string }>('/system/health'),
    status: () => request<Record<string, unknown>>('/system/status'),
    cost: () => request<{ calls: number; total_tokens: number; cost_usd: number }>('/system/cost'),
    intervals: () => request<MonitorIntervals>('/system/intervals'),
    updateIntervals: (data: Partial<MonitorIntervals>) =>
      request<MonitorIntervals>('/system/intervals', {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
  },
  health: {
    seasonalCoverage: () =>
      request<SeasonalCoverage>('/health/seasonal-coverage'),
  },
  diagnostics: {
    recent: () => request<DiagnosticActivity>('/diagnostics/recent'),
  },
  events: {
    list: (params?: { event_type?: string; subject?: string; limit?: number }) => {
      const q = new URLSearchParams();
      if (params?.event_type) q.set('event_type', params.event_type);
      if (params?.subject) q.set('subject', params.subject);
      if (params?.limit) q.set('limit', String(params.limit));
      const qs = q.toString();
      return request<ObservationEvent[]>(`/events${qs ? `?${qs}` : ''}`);
    },
    forSubject: (subject: string, limit = 20) =>
      request<ObservationEvent[]>(`/events/subject/${encodeURIComponent(subject)}?limit=${limit}`),
    recurrence: (subject: string, eventType = 'anomaly', days = 30) =>
      request<RecurrenceContext>(`/events/subject/${encodeURIComponent(subject)}/recurrence?event_type=${eventType}&days=${days}`),
    search: (q: string, limit = 10) =>
      request<ObservationEvent[]>(`/events/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  },
  discovery: {
    model: () => request<Record<string, unknown>>('/discovery/model'),
  },
  knowledge: {
    facts: (params?: {
      source?: string;
      fact_type?: string;
      active_only?: boolean;
      search?: string;
      limit?: number;
      offset?: number;
    }) => {
      const q = new URLSearchParams();
      if (params?.source) q.set('source', params.source);
      if (params?.fact_type) q.set('fact_type', params.fact_type);
      if (params?.active_only !== undefined) q.set('active_only', String(params.active_only));
      if (params?.search) q.set('search', params.search);
      if (params?.limit) q.set('limit', String(params.limit));
      if (params?.offset) q.set('offset', String(params.offset));
      const qs = q.toString();
      return request<SemanticFact[]>(`/knowledge/facts${qs ? `?${qs}` : ''}`);
    },
    updateFact: (id: string, data: FactUpdate) =>
      request<SemanticFact>(`/knowledge/facts/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    deleteFact: (id: string) =>
      request<{ id: string; deleted: boolean }>(`/knowledge/facts/${id}`, {
        method: 'DELETE',
      }),
    stats: () => request<KnowledgeStats>('/knowledge/stats'),
    feedbackSummary: (days = 30, limit = 20) =>
      request<FeedbackSummary>(`/knowledge/feedback-summary?days=${days}&limit=${limit}`),
    context: () => request<BusinessContextEntry[]>('/knowledge/context'),
  },
};
