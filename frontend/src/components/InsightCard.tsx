import type { Insight } from '../api/client';
import { formatTimestamp } from '../utils/format';

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
