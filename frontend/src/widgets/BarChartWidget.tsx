import ReactECharts from 'echarts-for-react';
import type { WidgetProps } from './WidgetRegistry';

export function BarChartWidget({ config, data }: WidgetProps) {
  const xField = (config?.x as string) ?? 'name';
  const yField = (config?.y as string) ?? 'value';
  const orientation = (config?.orientation as string) ?? 'vertical';

  const items = (data ?? []) as Record<string, unknown>[];
  const categories = items.map((d) => String(d[xField] ?? ''));
  const values = items.map((d) => Number(d[yField] ?? 0));

  const isHorizontal = orientation === 'horizontal';

  const option = {
    grid: { top: 10, right: 10, bottom: 30, left: 60 },
    xAxis: {
      type: isHorizontal ? ('value' as const) : ('category' as const),
      data: isHorizontal ? undefined : categories,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: isHorizontal ? ('category' as const) : ('value' as const),
      data: isHorizontal ? categories : undefined,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [{
      data: values,
      type: 'bar' as const,
      itemStyle: { color: '#38bdf8' },
    }],
    tooltip: { trigger: 'axis' as const },
  };

  return (
    <div className="h-full w-full">
      <ReactECharts option={option} style={{ height: '100%', width: '100%' }} />
    </div>
  );
}
