import { useState, useEffect, useImperativeHandle, forwardRef, useCallback } from 'react';
import { api, type Widget } from '../api/client';
import { WIDGET_REGISTRY } from '../widgets/WidgetRegistry';
import { ViewAsDropdown } from '../components/ViewAsDropdown';

export interface DashboardHandle {
  refresh: () => void;
}

export const Dashboard = forwardRef<DashboardHandle>(function Dashboard(_props, ref) {
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [viewOverrides, setViewOverrides] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  const loadWidgets = useCallback(() => {
    api.widgets.list().then(setWidgets).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadWidgets(); }, [loadWidgets]);

  useImperativeHandle(ref, () => ({ refresh: loadWidgets }), [loadWidgets]);

  const removeWidget = async (id: string) => {
    await api.widgets.remove(id);
    setWidgets((prev) => prev.filter((w) => w.id !== id));
  };

  const switchView = (widgetId: string, newType: string) => {
    setViewOverrides((prev) => ({ ...prev, [widgetId]: newType }));
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
          {widgets.map((w) => {
            const effectiveType = viewOverrides[w.id] ?? w.widget_type;
            const WidgetComponent = WIDGET_REGISTRY[effectiveType];

            return (
              <div key={w.id} className="bg-slate-800 rounded-lg border border-slate-700 p-4 min-h-[200px]">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-semibold text-slate-200">
                    {w.title || w.widget_type}
                  </h3>
                  <div className="flex items-center gap-2">
                    <ViewAsDropdown
                      currentType={w.widget_type}
                      onSwitch={(newType) => switchView(w.id, newType)}
                    />
                    <button
                      onClick={() => removeWidget(w.id)}
                      className="text-xs text-slate-500 hover:text-red-400"
                    >
                      Remove
                    </button>
                  </div>
                </div>
                <div className="h-[160px]">
                  {WidgetComponent ? (
                    <WidgetComponent
                      config={w.config}
                      title={w.title}
                      data={w.data_source ? [w.data_source] : []}
                    />
                  ) : (
                    <div className="flex items-center justify-center h-full text-slate-500 text-sm">
                      Unknown widget type: {effectiveType}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
});
