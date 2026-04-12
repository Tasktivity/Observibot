import type { Insight } from '../api/client';

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
  onPin?: (insight: Insight) => void;
}

export function InsightCard({ insight, onPin }: InsightCardProps) {
  const colorClass = SEVERITY_COLORS[insight.severity] ?? SEVERITY_COLORS.info;
  const badgeColor = BADGE_COLORS[insight.severity] ?? BADGE_COLORS.info;

  return (
    <div className={`rounded-lg border p-4 ${colorClass} transition hover:brightness-110`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-medium text-white ${badgeColor}`}>
            {insight.severity.toUpperCase()}
          </span>
          <h3 className="text-sm font-semibold text-slate-100">
            {insight.is_hypothesis ? `\u{1F7E1} Hypothesis: ${insight.title}` : insight.title}
          </h3>
        </div>
        {onPin && (
          <button
            onClick={() => onPin(insight)}
            className="text-xs text-slate-400 hover:text-sky-400 whitespace-nowrap"
          >
            Pin
          </button>
        )}
      </div>
      <p className="text-sm text-slate-300">{insight.summary}</p>
      <div className="mt-2 text-xs text-slate-500">
        {new Date(insight.created_at).toLocaleTimeString()}
        {insight.confidence < 1 && (
          <span className="ml-2">confidence: {(insight.confidence * 100).toFixed(0)}%</span>
        )}
      </div>
    </div>
  );
}
