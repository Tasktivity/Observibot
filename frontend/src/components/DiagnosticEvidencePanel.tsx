import { useState } from 'react';
import type { DiagnosticEvidence } from '../api/client';

interface DiagnosticEvidencePanelProps {
  diagnostics: DiagnosticEvidence[];
}

/**
 * Renders the diagnostic queries the agent ran (or tried to run) in
 * response to an anomaly. O1 of Step 3.4 — the evidence itself is the
 * product; the operator must be able to inspect the SQL and rows that
 * back the insight narrative, not just read the narrative.
 */
export function DiagnosticEvidencePanel({
  diagnostics,
}: DiagnosticEvidencePanelProps) {
  const [open, setOpen] = useState(false);
  if (!diagnostics || diagnostics.length === 0) return null;

  const succeeded = diagnostics.filter((d) => !d.error).length;
  const rejected = diagnostics.length - succeeded;

  return (
    <div className="mt-3 border-t border-slate-700/50 pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 text-xs text-slate-400 hover:text-slate-200"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span className="font-medium">Diagnostic evidence</span>
        <span className="text-slate-500">
          {succeeded} succeeded{rejected > 0 ? `, ${rejected} rejected/errored` : ''}
        </span>
      </button>
      {open && (
        <div className="mt-2 space-y-3">
          {diagnostics.map((d, i) => (
            <DiagnosticRow key={i} diag={d} />
          ))}
        </div>
      )}
    </div>
  );
}

function DiagnosticRow({ diag }: { diag: DiagnosticEvidence }) {
  const [showAllRows, setShowAllRows] = useState(false);
  const hasError = Boolean(diag.error);
  const previewRows = showAllRows ? diag.rows : diag.rows.slice(0, 10);
  const executed = diag.executed_at
    ? new Date(diag.executed_at).toLocaleString()
    : '';

  const handleCopy = () => {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(diag.sql).catch(() => {
        /* ignore */
      });
    }
  };

  return (
    <div className="rounded border border-slate-700/60 bg-slate-900/40 p-2 text-xs">
      <div className="flex items-start gap-2">
        <span
          className={
            hasError
              ? 'inline-flex h-4 w-4 items-center justify-center rounded-full bg-red-500/20 text-red-400'
              : 'inline-flex h-4 w-4 items-center justify-center rounded-full bg-green-500/20 text-green-400'
          }
          aria-label={hasError ? 'rejected' : 'succeeded'}
        >
          {hasError ? '\u2717' : '\u2713'}
        </span>
        <div className="flex-1">
          <div className="font-medium text-slate-200">{diag.hypothesis}</div>
          {diag.explanation && (
            <div className="text-slate-400 mt-0.5">{diag.explanation}</div>
          )}
        </div>
      </div>
      <div className="mt-2 flex items-start gap-2">
        <pre className="flex-1 overflow-x-auto bg-slate-950/60 rounded p-2 text-[11px] leading-snug text-slate-200 whitespace-pre-wrap break-all">
          {diag.sql}
        </pre>
        <button
          type="button"
          onClick={handleCopy}
          className="shrink-0 px-1.5 py-0.5 rounded text-[10px] text-slate-400 hover:text-slate-100 border border-slate-600/60"
          title="Copy SQL"
        >
          copy
        </button>
      </div>
      <div className="mt-1.5 flex items-center justify-between">
        <div className="text-slate-400">
          {hasError
            ? `rejected: ${diag.error}`
            : `returned ${diag.row_count} rows`}
        </div>
        {executed && <div className="text-slate-500">{executed}</div>}
      </div>
      {!hasError && diag.rows.length > 0 && (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-slate-400">
                {Object.keys(diag.rows[0] || {}).map((k) => (
                  <th key={k} className="px-1.5 py-0.5 text-left font-normal">
                    {k}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {previewRows.map((row, ri) => (
                <tr key={ri} className="text-slate-200">
                  {Object.keys(diag.rows[0] || {}).map((k) => (
                    <td
                      key={k}
                      className="px-1.5 py-0.5 border-t border-slate-800/60"
                    >
                      {formatCell(row[k])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {diag.rows.length > 10 && (
            <button
              type="button"
              onClick={() => setShowAllRows((v) => !v)}
              className="mt-1 text-slate-400 hover:text-slate-200"
            >
              {showAllRows
                ? 'show first 10'
                : `show all ${diag.rows.length} rows`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
