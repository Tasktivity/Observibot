import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type SemanticFact } from '../api/client';

const SOURCE_COLORS: Record<string, string> = {
  code_extraction: 'bg-emerald-500/20 text-emerald-300',
  schema_analysis: 'bg-sky-500/20 text-sky-300',
  user_correction: 'bg-amber-500/20 text-amber-300',
  semantic_modeler: 'bg-slate-500/20 text-slate-300',
};

const TYPE_OPTIONS = [
  'definition',
  'workflow',
  'mapping',
  'entity',
  'rule',
  'correction',
];

const SOURCE_OPTIONS = [
  'code_extraction',
  'schema_analysis',
  'semantic_modeler',
  'user_correction',
];

const PAGE_SIZE = 50;

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color = value >= 0.8 ? 'bg-emerald-500' : value >= 0.5 ? 'bg-amber-500' : 'bg-rose-500';
  return (
    <div className="w-16 h-1.5 rounded bg-slate-700 overflow-hidden">
      <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function FactsTable() {
  const [facts, setFacts] = useState<SemanticFact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<string>('');
  const [factType, setFactType] = useState<string>('');
  const [activeOnly, setActiveOnly] = useState(true);
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editClaim, setEditClaim] = useState('');

  const debounceRef = useRef<number | null>(null);

  const fetchFacts = useCallback(
    async (append: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.knowledge.facts({
          source: source || undefined,
          fact_type: factType || undefined,
          active_only: activeOnly,
          search: search || undefined,
          limit: PAGE_SIZE,
          offset: append ? offset : 0,
        });
        setFacts((prev) => (append ? [...prev, ...data] : data));
        setHasMore(data.length === PAGE_SIZE);
        if (!append) setOffset(PAGE_SIZE);
        else setOffset(offset + PAGE_SIZE);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load facts';
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [source, factType, activeOnly, search, offset],
  );

  // Initial load + filter changes (reset offset, not append).
  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      setOffset(0);
      fetchFacts(false);
    }, 300);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, factType, activeOnly, search]);

  const deactivate = async (id: string) => {
    const updated = await api.knowledge.updateFact(id, { is_active: false });
    setFacts((prev) =>
      prev.map((f) => (f.id === id ? { ...f, is_active: updated.is_active } : f)),
    );
    if (activeOnly) {
      setFacts((prev) => prev.filter((f) => f.id !== id));
    }
  };

  const reactivate = async (id: string) => {
    const updated = await api.knowledge.updateFact(id, { is_active: true });
    setFacts((prev) =>
      prev.map((f) => (f.id === id ? { ...f, is_active: updated.is_active } : f)),
    );
  };

  const startEdit = (fact: SemanticFact) => {
    setEditingId(fact.id);
    setEditClaim(fact.claim);
  };

  const saveEdit = async () => {
    if (!editingId) return;
    const updated = await api.knowledge.updateFact(editingId, { claim: editClaim });
    setFacts((prev) => prev.map((f) => (f.id === editingId ? updated : f)));
    setEditingId(null);
    setEditClaim('');
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditClaim('');
  };

  const deleteFact = async (fact: SemanticFact) => {
    const ok = window.confirm(
      `Permanently delete this fact?\n\n"${fact.concept}": ${fact.claim.slice(0, 100)}\n\nThis cannot be undone. Prefer "Deactivate" for reversible changes.`,
    );
    if (!ok) return;
    await api.knowledge.deleteFact(fact.id);
    setFacts((prev) => prev.filter((f) => f.id !== fact.id));
  };

  const rows = useMemo(() => facts, [facts]);

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg">
      <div className="p-3 border-b border-slate-700 flex flex-wrap items-center gap-2">
        <input
          type="text"
          placeholder="Search concept or claim..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-slate-200 w-60 focus:outline-none focus:border-sky-500"
        />
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200"
        >
          <option value="">All sources</option>
          {SOURCE_OPTIONS.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          value={factType}
          onChange={(e) => setFactType(e.target.value)}
          className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200"
        >
          <option value="">All types</option>
          {TYPE_OPTIONS.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
          <input
            type="checkbox"
            checked={activeOnly}
            onChange={(e) => setActiveOnly(e.target.checked)}
            className="cursor-pointer"
          />
          Active only
        </label>
        <span className="text-xs text-slate-500 ml-auto">
          {rows.length} fact{rows.length === 1 ? '' : 's'}{hasMore ? '+' : ''}
        </span>
      </div>
      {error && <div className="p-3 text-rose-400 text-sm">Error: {error}</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-400 uppercase tracking-wider">
              <th className="px-3 py-2">Concept</th>
              <th className="px-3 py-2">Claim</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Confidence</th>
              <th className="px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((fact) => (
              <tr
                key={fact.id}
                className={`border-t border-slate-700/50 ${fact.is_active ? '' : 'opacity-50 line-through'}`}
              >
                <td className="px-3 py-2 text-slate-200 font-medium align-top">
                  {fact.concept}
                </td>
                <td className="px-3 py-2 text-slate-300 align-top max-w-md">
                  {editingId === fact.id ? (
                    <div className="flex flex-col gap-2">
                      <textarea
                        value={editClaim}
                        onChange={(e) => setEditClaim(e.target.value)}
                        className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-slate-200 w-full min-h-[60px]"
                      />
                      <div className="flex gap-2">
                        <button
                          onClick={saveEdit}
                          className="text-xs bg-sky-600 hover:bg-sky-500 text-white px-2 py-1 rounded"
                        >
                          Save
                        </button>
                        <button
                          onClick={cancelEdit}
                          className="text-xs text-slate-400 hover:text-slate-200"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    fact.claim
                  )}
                </td>
                <td className="px-3 py-2 text-slate-400 text-xs align-top">{fact.fact_type}</td>
                <td className="px-3 py-2 align-top">
                  <span
                    className={`text-xs px-2 py-0.5 rounded ${SOURCE_COLORS[fact.source] ?? 'bg-slate-700 text-slate-300'}`}
                  >
                    {fact.source}
                  </span>
                </td>
                <td className="px-3 py-2 align-top">
                  <div className="flex items-center gap-2">
                    <ConfidenceBar value={fact.confidence} />
                    <span className="text-xs text-slate-400">{fact.confidence.toFixed(2)}</span>
                  </div>
                </td>
                <td className="px-3 py-2 align-top">
                  <div className="flex gap-2 text-xs">
                    {editingId !== fact.id && (
                      <>
                        <button
                          onClick={() => startEdit(fact)}
                          className="text-sky-400 hover:text-sky-300"
                        >
                          Edit
                        </button>
                        {fact.is_active ? (
                          <button
                            onClick={() => deactivate(fact.id)}
                            className="text-amber-400 hover:text-amber-300"
                          >
                            Deactivate
                          </button>
                        ) : (
                          <button
                            onClick={() => reactivate(fact.id)}
                            className="text-emerald-400 hover:text-emerald-300"
                          >
                            Reactivate
                          </button>
                        )}
                        <button
                          onClick={() => deleteFact(fact)}
                          className="text-rose-400 hover:text-rose-300"
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {rows.length === 0 && !loading && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-slate-500 text-sm">
                  No facts match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="p-3 border-t border-slate-700 flex items-center justify-between">
        <span className="text-xs text-slate-500">
          {loading ? 'Loading...' : `Showing ${rows.length}${hasMore ? '+ (more available)' : ''}`}
        </span>
        {hasMore && !loading && (
          <button
            onClick={() => fetchFacts(true)}
            className="text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1 rounded"
          >
            Load more
          </button>
        )}
      </div>
    </div>
  );
}
