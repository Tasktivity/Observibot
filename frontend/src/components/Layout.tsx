import { type ReactNode, useCallback, useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthProvider';
import { api, type MonitorIntervals } from '../api/client';

const COLLECTION_OPTIONS = [
  { value: 60, label: '1 min' },
  { value: 120, label: '2 min' },
  { value: 300, label: '5 min' },
  { value: 600, label: '10 min' },
  { value: 900, label: '15 min' },
  { value: 1800, label: '30 min' },
  { value: 3600, label: '1 hr' },
  { value: 7200, label: '2 hr' },
  { value: 14400, label: '4 hr' },
  { value: 28800, label: '8 hr' },
  { value: 43200, label: '12 hr' },
];

const ANALYSIS_OPTIONS = [
  { value: 300, label: '5 min' },
  { value: 600, label: '10 min' },
  { value: 900, label: '15 min' },
  { value: 1800, label: '30 min' },
  { value: 3600, label: '1 hr' },
  { value: 7200, label: '2 hr' },
  { value: 14400, label: '4 hr' },
  { value: 28800, label: '8 hr' },
  { value: 43200, label: '12 hr' },
  { value: 86400, label: '24 hr' },
];

function IntervalSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: number;
  options: { value: number; label: string }[];
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex items-center gap-1.5 text-xs text-slate-400">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="bg-slate-700 text-slate-200 text-xs rounded px-1.5 py-1
                   border border-slate-600 hover:border-sky-500
                   focus:outline-none focus:border-sky-400 cursor-pointer"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  );
}

export function Layout({
  children,
  headerSlot,
}: {
  children: ReactNode;
  headerSlot?: ReactNode;
}) {
  const { user, logout } = useAuth();
  const [intervals, setIntervals] = useState<MonitorIntervals | null>(null);

  useEffect(() => {
    api.system.intervals()
      .then(setIntervals)
      .catch((err: Error) =>
        console.error('[Layout] failed to load intervals', err),
      );
  }, []);

  const handleCollectionChange = useCallback(
    (value: number) => {
      setIntervals((prev) => {
        if (!prev) return prev;
        // If the new collection >= current analysis, bump analysis to the next
        // available option above the new collection value.
        const needsBump = prev.analysis_interval_seconds <= value;
        const newAnalysis = needsBump
          ? (ANALYSIS_OPTIONS.find((o) => o.value > value)?.value
              ?? ANALYSIS_OPTIONS[ANALYSIS_OPTIONS.length - 1].value)
          : prev.analysis_interval_seconds;
        const update: Partial<MonitorIntervals> = {
          collection_interval_seconds: value,
          ...(needsBump ? { analysis_interval_seconds: newAnalysis } : {}),
        };
        api.system.updateIntervals(update).catch((err: Error) =>
          console.error('[Layout] failed to update intervals', err),
        );
        return { ...prev, collection_interval_seconds: value, analysis_interval_seconds: newAnalysis };
      });
    },
    [],
  );

  const handleAnalysisChange = useCallback(
    (value: number) => {
      api.system.updateIntervals({ analysis_interval_seconds: value }).catch(
        (err: Error) =>
          console.error('[Layout] failed to update analysis interval', err),
      );
      setIntervals((prev) => prev ? { ...prev, analysis_interval_seconds: value } : prev);
    },
    [],
  );

  const validAnalysisOptions = intervals
    ? ANALYSIS_OPTIONS.filter((o) => o.value > intervals.collection_interval_seconds)
    : ANALYSIS_OPTIONS;

  return (
    <div className="h-screen bg-slate-900 flex flex-col">
      <header className="bg-slate-800 border-b border-slate-700 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-sky-400">Observibot</h1>
          <span className="text-xs text-slate-500">AI SRE Dashboard</span>
          {headerSlot && <div className="ml-3">{headerSlot}</div>}
        </div>
        {intervals && (
          <div className="flex items-center gap-4">
            <IntervalSelect
              label="Collection"
              value={intervals.collection_interval_seconds}
              options={COLLECTION_OPTIONS}
              onChange={handleCollectionChange}
            />
            <IntervalSelect
              label="Analysis"
              value={intervals.analysis_interval_seconds}
              options={validAnalysisOptions}
              onChange={handleAnalysisChange}
            />
          </div>
        )}
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
