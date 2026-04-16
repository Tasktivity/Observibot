import { useEffect, useState } from 'react';
import { api, type DiagnosticActivity } from '../api/client';

/**
 * Compact rollup of recent hypothesis-test-loop activity. Mirrors the
 * structure of ``SeasonalCoveragePanel`` so the Agent Memory tab has a
 * consistent visual for "what the agent has been doing lately."
 */
export function DiagnosticActivityPanel() {
  const [data, setData] = useState<DiagnosticActivity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    api.diagnostics
      .recent()
      .then(setData)
      .catch((err: Error) =>
        setError(err.message ?? 'Failed to load diagnostic activity'),
      )
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-500 mb-4">
        Loading diagnostic activity...
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-red-400 mb-4">
        {error ?? 'Diagnostic activity unavailable'}
      </div>
    );
  }

  const s = data.last_24h;
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 mb-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-xs uppercase tracking-wider text-slate-400">
            Diagnostic activity (24h)
          </div>
          <div className="text-sm text-slate-200 mt-0.5">
            {s.runs} runs &middot; {s.skipped_cooldown} skipped (cooldown)
            {s.timed_out > 0 && ` · ${s.timed_out} timed out`}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            {s.queries_issued} queries ({s.queries_succeeded} succeeded,{' '}
            {s.queries_rejected} rejected/errored)
          </div>
        </div>
        <button
          type="button"
          className="text-xs text-slate-400 hover:text-slate-200"
          onClick={() => setExpanded((v) => !v)}
          disabled={data.recent_runs.length === 0}
        >
          {data.recent_runs.length === 0
            ? 'no recent events'
            : expanded
              ? 'hide'
              : `show ${data.recent_runs.length} recent`}
        </button>
      </div>
      {expanded && data.recent_runs.length > 0 && (
        <ul className="mt-2 text-xs space-y-1">
          {data.recent_runs.map((r, i) => (
            <li key={i} className="text-slate-300">
              <span className="text-slate-500">
                {new Date(r.occurred_at).toLocaleString()}
              </span>{' '}
              <span className="font-mono">{r.event_type}</span>
              {r.summary ? `: ${r.summary}` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
