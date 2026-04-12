import type { WidgetProps } from './WidgetRegistry';

export function TextSummaryWidget({ config, title }: WidgetProps) {
  const text = (config?.text as string) ?? '';

  return (
    <div className="h-full overflow-y-auto">
      {title && <h3 className="text-sm font-semibold text-slate-200 mb-2">{title}</h3>}
      <div className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed">
        {text || 'No content available.'}
      </div>
    </div>
  );
}
