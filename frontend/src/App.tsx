import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from './auth/AuthProvider';
import { ProtectedRoute } from './auth/ProtectedRoute';
import { Layout } from './components/Layout';
import { DiscoveryFeed } from './zones/DiscoveryFeed';
import { Dashboard } from './zones/Dashboard';
import { Chat } from './zones/Chat';

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ProtectedRoute>
          <Layout>
            <div className="col-span-3 overflow-hidden">
              <DiscoveryFeed />
            </div>
            <div className="col-span-6 overflow-hidden">
              <Dashboard />
            </div>
            <div className="col-span-3 overflow-hidden">
              <Chat />
            </div>
          </Layout>
        </ProtectedRoute>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
