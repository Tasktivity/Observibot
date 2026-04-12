import type { WidgetProps } from './WidgetRegistry';

export function KpiWidget({ config, title }: WidgetProps) {
  const value = (config?.value as number) ?? 0;
  const delta = config?.delta as number | undefined;
  const format = (config?.format as string) ?? 'number';

  const formatted = format === 'percent'
    ? `${(value * 100).toFixed(1)}%`
    : format === 'bytes'
      ? formatBytes(value)
      : value.toLocaleString();

  return (
    <div className="flex flex-col items-center justify-center h-full">
      <div className="text-3xl font-bold text-white">{formatted}</div>
      {title && <div className="text-xs text-slate-400 mt-1">{title}</div>}
      {delta !== undefined && (
        <div className={`text-sm mt-1 ${delta >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {delta >= 0 ? '\u2191' : '\u2193'} {Math.abs(delta).toFixed(1)}%
        </div>
      )}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
