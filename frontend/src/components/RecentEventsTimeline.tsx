import { useEffect, useState } from 'react';
import { api, type ObservationEvent } from '../api/client';

const EVENT_ICONS: Record<string, string> = {
  anomaly: '⚠',
  insight: '💡',
  deploy: '🚀',
  drift: '🌊',
  investigation: '🔍',
  feedback: '📣',
  metric_collection: '📊',
};

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function RecentEventsTimeline() {
  const [events, setEvents] = useState<ObservationEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    api.events
      .list({ limit: 20 })
      .then(setEvents)
      .catch((err) => setError(err.message ?? 'Failed to load events'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-slate-500 text-sm">Loading events...</div>;
  if (error) return <div className="text-rose-400 text-sm">Error: {error}</div>;

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full px-3 py-2 flex items-center justify-between hover:bg-slate-700/40"
      >
        <span className="text-sm font-semibold text-slate-200">
          Recent events ({events.length})
        </span>
        <span className="text-xs text-slate-500">{expanded ? '▼' : '▶'}</span>
      </button>
      {expanded && events.length > 0 && (
        <div className="border-t border-slate-700 divide-y divide-slate-700/50 max-h-96 overflow-y-auto">
          {events.map((ev) => (
            <div key={ev.id} className="px-3 py-2 flex items-start gap-3 text-sm">
              <span className="text-lg flex-shrink-0 leading-none pt-0.5">
                {EVENT_ICONS[ev.event_type] ?? '•'}
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-slate-200 truncate">
                  <span className="text-xs text-slate-400 mr-2">{ev.event_type}</span>
                  {ev.summary ?? ev.subject}
                </div>
                <div className="text-xs text-slate-500 mt-0.5">
                  {ev.source} · {ev.subject}
                </div>
              </div>
              <span className="text-xs text-slate-500 flex-shrink-0">
                {formatTime(ev.occurred_at)}
              </span>
            </div>
          ))}
        </div>
      )}
      {expanded && events.length === 0 && (
        <div className="px-3 py-4 text-center text-xs text-slate-500 border-t border-slate-700">
          No events yet.
        </div>
      )}
    </div>
  );
}
