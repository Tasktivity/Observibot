import { FactsTable } from '../components/FactsTable';
import { FeedbackSummary } from '../components/FeedbackSummary';
import { KnowledgeStats } from '../components/KnowledgeStats';
import { RecentEventsTimeline } from '../components/RecentEventsTimeline';

export function AgentMemory() {
  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="max-w-6xl mx-auto">
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-1">
            Agent Memory
          </h2>
          <p className="text-xs text-slate-500">
            Inspect and edit what Observibot has learned. Deactivate incorrect facts,
            edit claims, or delete outdated knowledge. Changes take effect on the
            next chat query.
          </p>
        </div>
        <KnowledgeStats />
        <div className="mb-4">
          <FactsTable />
        </div>
        <div className="mb-4">
          <FeedbackSummary />
        </div>
        <div className="mb-4">
          <RecentEventsTimeline />
        </div>
      </div>
    </div>
  );
}
