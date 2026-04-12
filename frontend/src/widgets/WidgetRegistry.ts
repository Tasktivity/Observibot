import type { ComponentType } from 'react';
import { KpiWidget } from './KpiWidget';
import { TimeSeriesWidget } from './TimeSeriesWidget';
import { BarChartWidget } from './BarChartWidget';
import { TableWidget } from './TableWidget';
import { StatusWidget } from './StatusWidget';
import { TextSummaryWidget } from './TextSummaryWidget';

export interface WidgetProps {
  config: Record<string, unknown> | null;
  data?: unknown[];
  title?: string;
}

export const WIDGET_REGISTRY: Record<string, ComponentType<WidgetProps>> = {
  kpi_number: KpiWidget,
  time_series: TimeSeriesWidget,
  categorical_bar: BarChartWidget,
  table: TableWidget,
  status: StatusWidget,
  text_summary: TextSummaryWidget,
};

export const WIDGET_ALTERNATIVES: Record<string, string[]> = {
  kpi_number: ['table'],
  time_series: ['table'],
  categorical_bar: ['table'],
  table: ['categorical_bar'],
  status: [],
  text_summary: [],
};
