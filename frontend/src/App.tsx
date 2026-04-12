import { useCallback, useRef } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './auth/AuthProvider';
import { ProtectedRoute } from './auth/ProtectedRoute';
import { Layout } from './components/Layout';
import { DiscoveryFeed } from './zones/DiscoveryFeed';
import { Dashboard } from './zones/Dashboard';
import { Chat } from './zones/Chat';
import { api, type Insight } from './api/client';

const queryClient = new QueryClient();

function AppContent() {
  const dashboardRef = useRef<{ refresh: () => void }>(null);

  const handlePinInsight = useCallback(async (insight: Insight) => {
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

  return (
    <Layout>
      <div className="col-span-3 overflow-hidden h-full min-h-0">
        <DiscoveryFeed onPin={handlePinInsight} />
      </div>
      <div className="col-span-6 overflow-hidden h-full min-h-0">
        <Dashboard ref={dashboardRef} />
      </div>
      <div className="col-span-3 overflow-hidden h-full min-h-0">
        <Chat onPin={handlePinChat} />
      </div>
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
