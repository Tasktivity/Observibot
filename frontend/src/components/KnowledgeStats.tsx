import { useEffect, useState } from 'react';
import { api, type KnowledgeStats as KnowledgeStatsData } from '../api/client';

const STATUS_COLORS: Record<string, string> = {
  current: 'text-emerald-400',
  stale: 'text-amber-400',
  unavailable: 'text-slate-500',
  error: 'text-rose-400',
};

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 min-w-[140px]">
      <div className="text-xs text-slate-400 uppercase tracking-wider">{label}</div>
      <div className="text-xl font-semibold text-slate-100">{value}</div>
      {hint && <div className="text-xs text-slate-500 mt-0.5">{hint}</div>}
    </div>
  );
}

export function KnowledgeStats() {
  const [stats, setStats] = useState<KnowledgeStatsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.knowledge
      .stats()
      .then((data) => setStats(data))
      .catch((err) => setError(err.message ?? 'Failed to load stats'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-slate-500 text-sm">Loading stats...</div>;
  if (error) return <div className="text-rose-400 text-sm">Error: {error}</div>;
  if (!stats) return null;

  const bySource = stats.facts_by_source ?? {};
  const sourceSummary = [
    bySource.code_extraction ? `${bySource.code_extraction} from code` : null,
    bySource.schema_analysis ? `${bySource.schema_analysis} from schema` : null,
    bySource.user_correction ? `${bySource.user_correction} corrections` : null,
    bySource.semantic_modeler ? `${bySource.semantic_modeler} from modeler` : null,
  ]
    .filter(Boolean)
    .join(', ');

  const byOutcome = stats.feedback_by_outcome ?? {};
  const feedbackSummary = Object.entries(byOutcome)
    .map(([k, v]) => `${v} ${k}`)
    .join(', ');

  const statusColor = STATUS_COLORS[stats.code_intelligence_status] ?? 'text-slate-500';
  const commit = stats.last_indexed_commit ? stats.last_indexed_commit.slice(0, 8) : 'n/a';

  return (
    <div className="flex flex-wrap gap-3 mb-4">
      <Stat
        label="Facts"
        value={`${stats.active_facts}${stats.inactive_facts ? ` (+${stats.inactive_facts} inactive)` : ''}`}
        hint={sourceSummary || '—'}
      />
      <Stat
        label="Feedback"
        value={stats.total_feedback}
        hint={feedbackSummary || '—'}
      />
      <Stat label="Events" value={stats.total_events} hint="all observations" />
      <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 min-w-[200px]">
        <div className="text-xs text-slate-400 uppercase tracking-wider">Code Intelligence</div>
        <div className={`text-sm font-semibold ${statusColor}`}>{stats.code_intelligence_status}</div>
        <div className="text-xs text-slate-500 mt-0.5">commit {commit}</div>
      </div>
    </div>
  );
}
