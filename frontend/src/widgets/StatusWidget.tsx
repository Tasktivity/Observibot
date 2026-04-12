import type { WidgetProps } from './WidgetRegistry';

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  healthy: { bg: 'bg-green-500/20', text: 'text-green-400', label: 'Healthy' },
  degraded: { bg: 'bg-amber-500/20', text: 'text-amber-400', label: 'Degraded' },
  unhealthy: { bg: 'bg-red-500/20', text: 'text-red-400', label: 'Unhealthy' },
  unknown: { bg: 'bg-slate-500/20', text: 'text-slate-400', label: 'Unknown' },
};

export function StatusWidget({ config, title }: WidgetProps) {
  const status = (config?.status as string) ?? 'unknown';
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.unknown;

  return (
    <div className={`flex items-center justify-center h-full rounded-lg ${style.bg}`}>
      <div className="text-center">
        <div className={`text-lg font-bold ${style.text}`}>{style.label}</div>
        {title && <div className="text-xs text-slate-400 mt-1">{title}</div>}
      </div>
    </div>
  );
}
