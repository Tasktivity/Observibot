import type { ReactNode } from 'react';
import { useAuth } from '../auth/AuthProvider';

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-slate-900 flex flex-col">
      <header className="bg-slate-800 border-b border-slate-700 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-sky-400">Observibot</h1>
          <span className="text-xs text-slate-500">AI SRE Dashboard</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-sm text-slate-400">{user?.email}</span>
          <button
            onClick={logout}
            className="text-sm text-slate-400 hover:text-sky-400 transition"
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="flex-1 grid grid-cols-12 gap-4 p-4 overflow-hidden" style={{ height: 'calc(100vh - 56px)' }}>
        {children}
      </main>
    </div>
  );
}
