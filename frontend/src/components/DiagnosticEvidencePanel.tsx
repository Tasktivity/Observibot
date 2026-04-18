import { useState } from 'react';
import type { DiagnosticEvidence, FactCitation } from '../api/client';

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
      <CodeContextSection
        freshness={diag.code_freshness ?? null}
        citations={diag.fact_citations ?? []}
      />
    </div>
  );
}

const FRESHNESS_BADGES: Record<
  NonNullable<DiagnosticEvidence['code_freshness']>,
  { text: string; className: string } | null
> = {
  current: null,
  stale: {
    text: 'Code context: stale (index \u226524h old)',
    className: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
  },
  unavailable: {
    text: 'Code context: unavailable',
    className: 'bg-slate-700/60 text-slate-300 border-slate-600/60',
  },
  error: {
    text: 'Code context: error',
    className: 'bg-red-500/20 text-red-300 border-red-500/30',
  },
};

function CodeContextSection({
  freshness,
  citations,
}: {
  freshness: DiagnosticEvidence['code_freshness'] | null;
  citations: FactCitation[];
}) {
  const badge = freshness ? FRESHNESS_BADGES[freshness] : null;
  if (!badge && citations.length === 0) {
    return null;
  }
  const shown = citations.slice(0, 3);
  return (
    <div className="mt-2 border-t border-slate-700/40 pt-1.5">
      {badge && (
        <div
          className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] ${badge.className}`}
        >
          {badge.text}
        </div>
      )}
      {shown.length > 0 && (
        <div className="mt-1.5 space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">
            Code context
          </div>
          {shown.map((cite, i) => (
            <CitationRow key={`${cite.fact_id}-${i}`} cite={cite} />
          ))}
        </div>
      )}
    </div>
  );
}

const SOURCE_BADGES: Record<string, string> = {
  code_extraction: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  schema_analysis: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  user_correction: 'bg-green-500/20 text-green-300 border-green-500/30',
  semantic_modeler: 'bg-slate-700/60 text-slate-300 border-slate-600/60',
};

function CitationRow({ cite }: { cite: FactCitation }) {
  const [expanded, setExpanded] = useState(false);
  const claimLong = cite.claim.length > 120;
  const shownClaim = expanded || !claimLong
    ? cite.claim
    : `${cite.claim.slice(0, 120)}\u2026`;
  const sourceClass =
    SOURCE_BADGES[cite.source] ??
    'bg-slate-700/60 text-slate-300 border-slate-600/60';
  const shortCommit = cite.commit ? cite.commit.slice(0, 7) : null;
  return (
    <div className="rounded border border-slate-800/60 bg-slate-950/40 p-1.5 text-[11px]">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="font-semibold text-slate-200">{cite.concept}</span>
        <span
          className={`inline-flex items-center rounded border px-1 py-0 text-[9px] uppercase tracking-wide ${sourceClass}`}
          title={`Source: ${cite.source}`}
        >
          {cite.source}
        </span>
        <span
          className="text-[9px] text-slate-500"
          title="Fact confidence score"
        >
          {cite.confidence.toFixed(2)}
        </span>
      </div>
      {cite.claim && (
        <div className="mt-0.5 text-slate-300 whitespace-pre-wrap break-words">
          {shownClaim}
          {claimLong && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="ml-1 text-[10px] text-slate-400 hover:text-slate-200"
            >
              {expanded ? 'less' : 'more'}
            </button>
          )}
        </div>
      )}
      {(cite.path || shortCommit) && (
        <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-500">
          {cite.path && (
            <code className="rounded bg-slate-900/60 px-1 text-slate-400">
              {cite.path}
              {cite.lines ? `:${cite.lines}` : ''}
            </code>
          )}
          {shortCommit && (
            <span>
              <span className="text-slate-600">indexed at</span>{' '}
              <code className="rounded bg-slate-900/60 px-1 text-slate-400">
                {shortCommit}
              </code>
            </span>
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
