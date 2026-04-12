import { useState, useEffect } from 'react';
import { api, type Widget } from '../api/client';

export function Dashboard() {
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [loading, setLoading] = useState(true);

  const loadWidgets = () => {
    api.widgets.list().then(setWidgets).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { loadWidgets(); }, []);

  const removeWidget = async (id: string) => {
    await api.widgets.remove(id);
    setWidgets((prev) => prev.filter((w) => w.id !== id));
  };

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
        Dashboard
      </h2>
      <div className="flex-1 overflow-y-auto">
        {loading && <p className="text-slate-500 text-sm">Loading widgets...</p>}
        {!loading && widgets.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <p className="text-slate-500 text-sm">
              No widgets pinned yet. Pin insights or chat results to build your dashboard.
            </p>
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {widgets.map((w) => (
            <div key={w.id} className="bg-slate-800 rounded-lg border border-slate-700 p-4">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-semibold text-slate-200">{w.title || w.widget_type}</h3>
                <button
                  onClick={() => removeWidget(w.id)}
                  className="text-xs text-slate-500 hover:text-red-400"
                >
                  Remove
                </button>
              </div>
              <div className="text-xs text-slate-400">
                Type: {w.widget_type}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
