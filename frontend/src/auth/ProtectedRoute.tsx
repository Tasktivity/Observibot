import type { ReactNode } from 'react';
import { useAuth } from './AuthProvider';
import { LoginPage } from './LoginPage';

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-900">
        <div className="text-slate-400">Loading...</div>
      </div>
    );
  }

  if (!user) return <LoginPage />;

  return <>{children}</>;
}
