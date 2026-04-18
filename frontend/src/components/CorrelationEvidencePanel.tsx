import { useState } from 'react';
import type { CorrelationEvidence } from '../api/client';

interface CorrelationEvidencePanelProps {
  correlations: CorrelationEvidence[] | undefined;
}

/**
 * Renders the temporal change → anomaly correlations the deterministic
 * CorrelationDetector picked up this cycle. Stage 7 populates the data;
 * this panel surfaces it so operators can see "deploy X happened N min
 * before the anomaly" without reading the prompt logs.
 *
 * Empty-state: if correlations is empty / undefined the panel returns
 * null. "Correlation checked and found nothing" lives in the events
 * timeline (``correlation_run`` with a zero-count summary), not here.
 */
export function CorrelationEvidencePanel({
  correlations,
}: CorrelationEvidencePanelProps) {
  const [open, setOpen] = useState(false);
  if (!correlations || correlations.length === 0) return null;

  return (
    <div className="mt-3 border-t border-slate-700/50 pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 text-xs text-slate-400 hover:text-slate-200"
      >
        <span>{open ? '\u25BE' : '\u25B8'}</span>
        <span className="font-medium">Correlated changes</span>
        <span className="text-slate-500">
          {correlations.length} change
          {correlations.length === 1 ? '' : 's'} near the anomaly
        </span>
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {correlations.map((c, i) => (
            <CorrelationRow key={`${c.change_event_id}-${i}`} corr={c} />
          ))}
        </div>
      )}
    </div>
  );
}

function CorrelationRow({ corr }: { corr: CorrelationEvidence }) {
  const [expanded, setExpanded] = useState(false);
  const minutes = Math.round(corr.time_delta_seconds / 60);
  const summaryIsLong = corr.change_summary.length > 200;
  const shownSummary = expanded || !summaryIsLong
    ? corr.change_summary
    : `${corr.change_summary.slice(0, 200)}\u2026`;

  return (
    <div className="rounded border border-slate-700/60 bg-slate-900/40 p-2 text-xs">
      <div className="flex items-start gap-2">
        <span
          className="inline-flex items-center rounded bg-slate-700/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-200"
          title="Change event type"
        >
          {corr.change_type}
        </span>
        <div className="flex-1">
          <div className="font-medium text-slate-200">
            {corr.metric_name}
          </div>
          <div className="text-slate-400">
            Observed {minutes} min after {corr.change_type}
          </div>
        </div>
        <span
          className="shrink-0 inline-flex items-center rounded bg-slate-800/70 px-1.5 py-0.5 text-[10px] text-slate-300"
          title="Severity score: higher = change and anomaly more closely aligned in time and magnitude."
        >
          score {corr.severity_score.toFixed(2)}
        </span>
      </div>
      {corr.change_summary && (
        <div className="mt-1.5">
          <p className="text-slate-300 whitespace-pre-wrap break-words">
            {shownSummary}
          </p>
          {summaryIsLong && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="mt-0.5 text-[10px] text-slate-400 hover:text-slate-200"
            >
              {expanded ? 'show less' : 'show full summary'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
