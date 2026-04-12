import ReactECharts from 'echarts-for-react';
import type { WidgetProps } from './WidgetRegistry';

export function TimeSeriesWidget({ config, data }: WidgetProps) {
  const xField = (config?.x as string) ?? 'collected_at';
  const yField = (config?.y as string) ?? 'value';
  const chartType = (config?.chart_type as string) ?? 'line';

  const items = (data ?? []) as Record<string, unknown>[];

  const option = {
    grid: { top: 10, right: 10, bottom: 30, left: 50 },
    xAxis: {
      type: 'category' as const,
      data: items.map((d) => {
        const v = d[xField];
        if (typeof v === 'string' && v.includes('T')) {
          return new Date(v).toLocaleTimeString();
        }
        return String(v ?? '');
      }),
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value' as const,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [{
      data: items.map((d) => Number(d[yField] ?? 0)),
      type: chartType === 'area' ? 'line' : 'line',
      smooth: true,
      lineStyle: { color: '#38bdf8' },
      itemStyle: { color: '#38bdf8' },
      areaStyle: chartType === 'area' ? { color: 'rgba(56, 189, 248, 0.1)' } : undefined,
    }],
    tooltip: { trigger: 'axis' as const },
  };

  return (
    <div className="h-full w-full">
      <ReactECharts option={option} style={{ height: '100%', width: '100%' }} />
    </div>
  );
}
