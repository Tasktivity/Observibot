import { useState } from 'react';
import type { WidgetProps } from './WidgetRegistry';
import { humanizeColumn, formatTimestamp, formatMetricValue } from '../utils/format';

const ISO_RE = /^\d{4}-\d{2}-\d{2}T/;

function formatCell(val: unknown): string {
  if (val == null) return '';
  if (typeof val === 'number') return formatMetricValue(val);
  const s = String(val);
  if (ISO_RE.test(s)) return formatTimestamp(s);
  return s;
}

export function TableWidget({ config, data }: WidgetProps) {
  const pageSize = (config?.page_size as number) ?? 10;
  const columns = (config?.columns as string[]) ?? [];
  const [page, setPage] = useState(0);

  const items = (data ?? []) as Record<string, unknown>[];
  const cols = columns.length > 0 ? columns : items.length > 0 ? Object.keys(items[0]) : [];
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const pageItems = items.slice(page * pageSize, (page + 1) * pageSize);

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs text-left">
          <thead className="text-slate-400 uppercase border-b border-slate-700">
            <tr>
              {cols.map((col) => (
                <th key={col} className="px-2 py-1">{humanizeColumn(col)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageItems.map((row, i) => (
              <tr key={i} className="border-b border-slate-800 hover:bg-slate-800/50">
                {cols.map((col) => (
                  <td key={col} className="px-2 py-1 text-slate-300">
                    {formatCell(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-2 text-xs text-slate-400">
          <button
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
            className="disabled:opacity-30"
          >
            Prev
          </button>
          <span>{page + 1} / {totalPages}</span>
          <button
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
            className="disabled:opacity-30"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
