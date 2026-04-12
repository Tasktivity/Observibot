import { useState, useEffect } from 'react';
import { api, type Insight } from '../api/client';
import { InsightCard } from '../components/InsightCard';

interface DiscoveryFeedProps {
  onPin?: (insight: Insight) => void;
}

export function DiscoveryFeed({ onPin }: DiscoveryFeedProps) {
  const [insights, setInsights] = useState<Insight[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.insights.list(50).then((data) => {
      setInsights(data);
      setLoading(false);
    }).catch(() => setLoading(false));

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
        {!loading && insights.length === 0 && (
          <p className="text-slate-500 text-sm">No insights yet. The monitor will generate them.</p>
        )}
        {insights.map((insight) => (
          <InsightCard key={insight.id} insight={insight} onPin={onPin} />
        ))}
      </div>
    </div>
  );
}
