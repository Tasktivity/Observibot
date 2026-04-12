import { useState, useEffect, useCallback } from 'react';
import { api, type Insight } from '../api/client';
import { InsightCard } from '../components/InsightCard';

interface DiscoveryFeedProps {
  onPromote?: (insight: Insight) => void;
  onInvestigate?: (insight: Insight) => void;
}

interface SystemSummary {
  tables: number;
  relationships: number;
  services: number;
  app_description: string;
  app_type: string;
}

function BootstrapCard({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-4">
      <h3 className="text-sm font-semibold text-sky-400 mb-1">{title}</h3>
      <p className="text-sm text-slate-300">{text}</p>
    </div>
  );
}

export function DiscoveryFeed({ onPromote, onInvestigate }: DiscoveryFeedProps) {
  const [insights, setInsights] = useState<Insight[]>([]);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<SystemSummary | null>(null);
  const [acknowledgedIds, setAcknowledgedIds] = useState<Set<string>>(new Set());
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    api.insights.list(50).then((data) => {
      setInsights(data);
      setLoading(false);
    }).catch(() => setLoading(false));

    api.discovery.model().then((data) => {
      setSummary(data as unknown as SystemSummary);
    }).catch(() => {});

    const interval = setInterval(() => {
      api.insights.list(10).then((fresh) => {
        setInsights((prev) => {
          const existingIds = new Set(prev.map((i) => i.id));
          const newOnes = fresh.filter((i) => !existingIds.has(i.id));
          return newOnes.length > 0 ? [...newOnes, ...prev] : prev;
        });
      }).catch(() => {});
    }, 5000);

    return () => clearInterval(interval);
  }, []);

  const handleAcknowledge = useCallback((insight: Insight) => {
    api.insights.ack(insight.id).catch(() => {});
    setAcknowledgedIds((prev) => new Set(prev).add(insight.id));
  }, []);

  const handlePinToFeed = useCallback((insight: Insight) => {
    setPinnedIds((prev) => {
      const next = new Set(prev);
      if (next.has(insight.id)) {
        next.delete(insight.id);
      } else {
        next.add(insight.id);
      }
      return next;
    });
  }, []);

  const visibleInsights = insights
    .filter((i) => !acknowledgedIds.has(i.id))
    .sort((a, b) => {
      const aPinned = pinnedIds.has(a.id);
      const bPinned = pinnedIds.has(b.id);
      if (aPinned && !bPinned) return -1;
      if (!aPinned && bPinned) return 1;
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
        Dynamic Discovery Feed
      </h2>
      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {loading && <p className="text-slate-500 text-sm">Loading insights...</p>}

        {!loading && visibleInsights.length === 0 && summary && (
          <div className="space-y-3">
            {(summary.tables > 0 || summary.services > 0) && (
              <BootstrapCard
                title="System Connected"
                text={`Monitoring ${summary.tables} tables, ${summary.relationships} relationships, and ${summary.services} services.`}
              />
            )}
            {summary.app_description && (
              <BootstrapCard
                title="Application Identified"
                text={summary.app_description}
              />
            )}
            <BootstrapCard
              title="Getting Started"
              text="Try asking a question in the Chat panel. The monitor will generate insights as it collects data."
            />
          </div>
        )}

        {!loading && visibleInsights.length === 0 && !summary && (
          <p className="text-slate-500 text-sm">
            No insights yet. The monitor will generate them as it collects data.
          </p>
        )}

        {visibleInsights.map((insight) => (
          <InsightCard
            key={insight.id}
            insight={insight}
            isPinned={pinnedIds.has(insight.id)}
            onAcknowledge={handleAcknowledge}
            onPinToFeed={handlePinToFeed}
            onPromote={onPromote}
            onInvestigate={onInvestigate}
          />
        ))}
      </div>
    </div>
  );
}
