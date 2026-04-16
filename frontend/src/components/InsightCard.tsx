import { useState } from 'react';
import type { Insight } from '../api/client';
import { api } from '../api/client';
import { formatTimestamp } from '../utils/format';
import { DiagnosticEvidencePanel } from './DiagnosticEvidencePanel';

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-500/20 text-red-400 border-red-500/30',
  warning: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  info: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  ok: 'bg-green-500/20 text-green-400 border-green-500/30',
  discovery: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
};

const BADGE_COLORS: Record<string, string> = {
  critical: 'bg-red-500',
  warning: 'bg-amber-500',
  info: 'bg-blue-500',
  ok: 'bg-green-500',
  discovery: 'bg-purple-500',
};

function formatLocalHour(utcHour: number): string {
  const d = new Date(Date.UTC(1970, 0, 1, utcHour, 0, 0));
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

const FEEDBACK_OPTIONS = [
  { outcome: 'noise', label: 'Noise', color: 'text-slate-400 hover:bg-slate-700' },
  { outcome: 'actionable', label: 'Actionable', color: 'text-green-400 hover:bg-green-900/30' },
  { outcome: 'investigating', label: 'Investigating', color: 'text-amber-400 hover:bg-amber-900/30' },
  { outcome: 'resolved', label: 'Resolved', color: 'text-blue-400 hover:bg-blue-900/30' },
] as const;

interface InsightCardProps {
  insight: Insight;
  isPinned?: boolean;
  onAcknowledge?: (insight: Insight) => void;
  onPinToFeed?: (insight: Insight) => void;
  onPromote?: (insight: Insight) => void;
  onInvestigate?: (insight: Insight) => void;
}

export function InsightCard({
  insight,
  isPinned,
  onAcknowledge,
  onPinToFeed,
  onPromote,
  onInvestigate,
}: InsightCardProps) {
  const colorClass = SEVERITY_COLORS[insight.severity] ?? SEVERITY_COLORS.info;
  const badgeColor = BADGE_COLORS[insight.severity] ?? BADGE_COLORS.info;
  const [feedbackSent, setFeedbackSent] = useState<string | null>(null);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);

  const handleFeedback = async (outcome: string) => {
    if (feedbackLoading) return; // double-click guard
    setFeedbackLoading(true);
    setFeedbackError(null);
    try {
      await api.insights.feedback(insight.id, outcome);
      setFeedbackSent(outcome);
    } catch {
      setFeedbackError('Failed to submit feedback');
    } finally {
      setFeedbackLoading(false);
    }
  };

  return (
    <div className={`rounded-lg border p-4 ${colorClass} transition hover:brightness-110 ${isPinned ? 'ring-1 ring-sky-400/40' : ''}`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-medium text-white ${badgeColor}`}>
            {insight.severity.toUpperCase()}
          </span>
          {isPinned && <span className="text-sky-400 text-xs" title="Pinned">&#x1F4CC;</span>}
          <h3 className="text-sm font-semibold text-slate-100">
            {insight.is_hypothesis ? `\u{1F7E1} Hypothesis: ${insight.title}` : insight.title}
          </h3>
        </div>
      </div>
      <p className="text-sm text-slate-300">{insight.summary}</p>
      {/* Recurrence annotation */}
      {insight.recurrence_context && insight.recurrence_context.count > 1 && (
        <div className="mt-1 flex items-center gap-1.5 text-xs text-slate-400">
          <span title="Recurring event">&#x1F504;</span>
          <span>
            Seen {insight.recurrence_context.count} times in last 30 days
            {insight.recurrence_context.common_hours?.length > 0 && (
              <> &middot; Usually around {insight.recurrence_context.common_hours.map(formatLocalHour).join(', ')} (local)</>
            )}
          </span>
        </div>
      )}
      {/* Diagnostic evidence — queries the agent ran to back this insight */}
      {insight.evidence?.diagnostics && insight.evidence.diagnostics.length > 0 && (
        <DiagnosticEvidencePanel diagnostics={insight.evidence.diagnostics} />
      )}
      {/* Feedback buttons */}
      <div className="mt-2 flex items-center gap-1 flex-wrap">
        {FEEDBACK_OPTIONS.map(({ outcome, label, color }) => (
          <button
            key={outcome}
            onClick={() => handleFeedback(outcome)}
            disabled={feedbackLoading || feedbackSent === outcome}
            className={`px-2 py-0.5 rounded-full text-xs border border-slate-600/50 transition ${
              feedbackSent === outcome
                ? 'bg-slate-700 text-slate-300 border-slate-500'
                : feedbackLoading
                  ? 'opacity-50 cursor-not-allowed'
                  : color
            }`}
          >
            {feedbackSent === outcome ? `${label} \u2713` : label}
          </button>
        ))}
        {feedbackError && (
          <span className="text-xs text-red-400 ml-1">{feedbackError}</span>
        )}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <div className="text-xs text-slate-500">
          {formatTimestamp(insight.created_at)}
          {insight.confidence < 1 && (
            <span className="ml-2">confidence: {(insight.confidence * 100).toFixed(0)}%</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {onAcknowledge && (
            <button
              onClick={() => onAcknowledge(insight)}
              title="Acknowledge"
              className="p-1 text-slate-500 hover:text-green-400 transition text-sm"
            >&#x2713;</button>
          )}
          {onPinToFeed && (
            <button
              onClick={() => onPinToFeed(insight)}
              title={isPinned ? 'Unpin' : 'Pin to feed'}
              className={`p-1 transition text-sm ${isPinned ? 'text-sky-400' : 'text-slate-500 hover:text-sky-400'}`}
            >&#x1F4CC;</button>
          )}
          {onPromote && (
            <button
              onClick={() => onPromote(insight)}
              title="Promote to Dashboard"
              className="p-1 text-slate-500 hover:text-amber-400 transition text-sm"
            >&#x25A6;</button>
          )}
          {onInvestigate && (
            <button
              onClick={() => onInvestigate(insight)}
              title="Investigate"
              className="p-1 text-slate-500 hover:text-purple-400 transition text-sm"
            >&#x1F50D;</button>
          )}
        </div>
      </div>
    </div>
  );
}
