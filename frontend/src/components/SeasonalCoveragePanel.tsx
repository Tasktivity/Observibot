import { useEffect, useState } from 'react';
import { api, type SeasonalCoverage } from '../api/client';

function describeStatus(pct: number): { label: string; color: string } {
  if (pct >= 80) return { label: 'Active', color: 'text-emerald-400' };
  if (pct >= 10) return { label: 'Ramping up', color: 'text-amber-400' };
  return { label: 'Learning', color: 'text-slate-400' };
}

function estimateDaysToFullCoverage(
  oldestAgeDays: number | null,
  minWeeks: number,
): string | null {
  if (oldestAgeDays === null) return null;
  const needed = minWeeks * 7;
  const remaining = needed - oldestAgeDays;
  if (remaining <= 0) return null;
  return `~${Math.ceil(remaining)} day${remaining >= 1.5 ? 's' : ''}`;
}

export function SeasonalCoveragePanel() {
  const [data, setData] = useState<SeasonalCoverage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.health
      .seasonalCoverage()
      .then(setData)
      .catch((err: Error) => setError(err.message ?? 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-500">
        Loading seasonal coverage...
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-rose-400">
        Seasonal coverage unavailable: {error ?? 'no data'}
      </div>
    );
  }

  const status = describeStatus(data.pct_trusted);
  const eta = estimateDaysToFullCoverage(
    data.oldest_bucket_age_days,
    data.min_weeks_observed,
  );

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 mb-4">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-xs text-slate-400 uppercase tracking-wider">
          Seasonal Baselines
        </h3>
        <span className={`text-xs font-semibold ${status.color}`}>
          {status.label}
        </span>
      </div>
      <div className="text-sm text-slate-200">
        {data.trusted_buckets.toLocaleString()} of {data.total_buckets.toLocaleString()} buckets trusted
        <span className="text-slate-500"> ({data.pct_trusted}%)</span>
      </div>
      <div className="text-xs text-slate-500 mt-0.5">
        {data.total_buckets === 0
          ? 'No seasonal baselines yet — they populate after the first collection cycle.'
          : eta
          ? `Full coverage expected in ${eta} (needs ${data.min_weeks_observed} weeks of samples per bucket).`
          : `Trust threshold: ${data.min_weeks_observed} weeks of observations per bucket.`}
      </div>
    </div>
  );
}
