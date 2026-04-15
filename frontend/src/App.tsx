import { useCallback, useRef, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './auth/AuthProvider';
import { ProtectedRoute } from './auth/ProtectedRoute';
import { Layout } from './components/Layout';
import { DiscoveryFeed } from './zones/DiscoveryFeed';
import { Dashboard } from './zones/Dashboard';
import { Chat, type ChatHandle } from './zones/Chat';
import { AgentMemory } from './views/AgentMemory';
import { ErrorBoundary } from './components/ErrorBoundary';
import { api, type Insight } from './api/client';

const queryClient = new QueryClient();

type TabKey = 'monitor' | 'memory';

function TabBar({ active, onChange }: { active: TabKey; onChange: (t: TabKey) => void }) {
  const tab = (key: TabKey, label: string) => (
    <button
      key={key}
      onClick={() => onChange(key)}
      className={`px-3 py-1.5 text-sm rounded-md transition ${
        active === key
          ? 'bg-sky-500/20 text-sky-300'
          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
      }`}
      data-testid={`tab-${key}`}
    >
      {label}
    </button>
  );

  return (
    <div className="flex items-center gap-1 bg-slate-800/60 rounded-lg p-0.5">
      {tab('monitor', 'Monitor')}
      {tab('memory', 'Agent Memory')}
    </div>
  );
}

function MonitorView() {
  const dashboardRef = useRef<{ refresh: () => void }>(null);
  const chatRef = useRef<ChatHandle>(null);

  const handlePromoteInsight = useCallback(async (insight: Insight) => {
    await api.widgets.create({
      widget_type: 'text_summary',
      title: insight.title,
      config: { text: insight.summary },
    });
    dashboardRef.current?.refresh();
  }, []);

  const handlePinChat = useCallback(async (data: {
    widget_type: string;
    title: string;
    config?: unknown;
    data_source?: unknown;
  }) => {
    await api.widgets.create({
      widget_type: data.widget_type,
      title: data.title,
      config: (data.config as Record<string, unknown>) ?? undefined,
    });
    dashboardRef.current?.refresh();
  }, []);

  const handleInvestigate = useCallback((insight: Insight) => {
    const question = `Investigate this finding: ${insight.title}. ${insight.summary}`;
    chatRef.current?.investigate(question);
  }, []);

  return (
    <>
      <div className="col-span-3 overflow-hidden h-full min-h-0">
        <ErrorBoundary name="Discovery Feed">
          <DiscoveryFeed
            onPromote={handlePromoteInsight}
            onInvestigate={handleInvestigate}
          />
        </ErrorBoundary>
      </div>
      <div className="col-span-6 overflow-hidden h-full min-h-0">
        <ErrorBoundary name="Dashboard">
          <Dashboard ref={dashboardRef} />
        </ErrorBoundary>
      </div>
      <div className="col-span-3 overflow-hidden h-full min-h-0">
        <ErrorBoundary name="Chat">
          <Chat ref={chatRef} onPin={handlePinChat} />
        </ErrorBoundary>
      </div>
    </>
  );
}

function AppContent() {
  const [tab, setTab] = useState<TabKey>('monitor');

  return (
    <Layout headerSlot={<TabBar active={tab} onChange={setTab} />}>
      {tab === 'monitor' ? (
        <MonitorView />
      ) : (
        <div className="col-span-12 overflow-hidden h-full min-h-0">
          <ErrorBoundary name="Agent Memory">
            <AgentMemory />
          </ErrorBoundary>
        </div>
      )}
    </Layout>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ProtectedRoute>
          <AppContent />
        </ProtectedRoute>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
