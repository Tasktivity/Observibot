import { useState, useEffect } from 'react';
import { api, type Insight } from '../api/client';
import { InsightCard } from '../components/InsightCard';

interface DiscoveryFeedProps {
  onPin?: (insight: Insight) => void;
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

export function DiscoveryFeed({ onPin }: DiscoveryFeedProps) {
  const [insights, setInsights] = useState<Insight[]>([]);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<SystemSummary | null>(null);

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

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
        Discovery Feed
      </h2>
      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {loading && <p className="text-slate-500 text-sm">Loading insights...</p>}

        {!loading && insights.length === 0 && summary && (
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

        {!loading && insights.length === 0 && !summary && (
          <p className="text-slate-500 text-sm">
            No insights yet. The monitor will generate them as it collects data.
          </p>
        )}

        {insights.map((insight) => (
          <InsightCard key={insight.id} insight={insight} onPin={onPin} />
        ))}
      </div>
    </div>
  );
}
