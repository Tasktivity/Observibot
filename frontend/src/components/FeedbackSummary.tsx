import { useEffect, useState } from 'react';
import { api, type FeedbackSummary as FeedbackSummaryData } from '../api/client';

const OUTCOME_COLORS: Record<string, string> = {
  noise: 'bg-rose-500/20 text-rose-300',
  actionable: 'bg-emerald-500/20 text-emerald-300',
  investigating: 'bg-amber-500/20 text-amber-300',
  resolved: 'bg-sky-500/20 text-sky-300',
};

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

export function FeedbackSummary() {
  const [data, setData] = useState<FeedbackSummaryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    api.knowledge
      .feedbackSummary(30, 20)
      .then(setData)
      .catch((err) => setError(err.message ?? 'Failed to load feedback'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-slate-500 text-sm">Loading feedback...</div>;
  if (error) return <div className="text-rose-400 text-sm">Error: {error}</div>;
  if (!data) return null;

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full px-3 py-2 flex items-center justify-between hover:bg-slate-700/40"
      >
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-slate-200">
            Feedback (last {data.since_days} days)
          </span>
          <div className="flex gap-2">
            {Object.entries(data.by_outcome).map(([outcome, n]) => (
              <span
                key={outcome}
                className={`text-xs px-2 py-0.5 rounded ${OUTCOME_COLORS[outcome] ?? 'bg-slate-700 text-slate-300'}`}
              >
                {n} {outcome}
              </span>
            ))}
            {data.total === 0 && (
              <span className="text-xs text-slate-500">No feedback yet.</span>
            )}
          </div>
        </div>
        <span className="text-xs text-slate-500">{expanded ? '▼' : '▶'}</span>
      </button>
      {expanded && data.recent.length > 0 && (
        <div className="border-t border-slate-700 divide-y divide-slate-700/50">
          {data.recent.map((entry) => (
            <div key={entry.id} className="px-3 py-2 flex items-start gap-3">
              <span
                className={`text-xs px-2 py-0.5 rounded flex-shrink-0 ${OUTCOME_COLORS[entry.outcome] ?? 'bg-slate-700 text-slate-300'}`}
              >
                {entry.outcome}
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-slate-200 truncate">{entry.insight_title}</div>
                {entry.note && (
                  <div className="text-xs text-slate-400 mt-0.5">{entry.note}</div>
                )}
              </div>
              <span className="text-xs text-slate-500 flex-shrink-0">
                {formatTime(entry.created_at)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
