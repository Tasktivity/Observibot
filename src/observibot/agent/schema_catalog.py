"""Schema catalog — builds LLM-consumable descriptions of available tables."""
from __future__ import annotations

from observibot.core.models import SystemModel, TableInfo

SENSITIVE_COLUMN_PATTERNS = {
    "api_key", "api_token", "secret", "password", "hash",
    "token", "credential", "private_key", "embedding",
    "openai_api_key", "service_role_key",
}

# Kept in sync with schema_analyzer._SOFT_DELETE_COLUMN_NAMES so both the
# planning prompt and the retrieval-time semantic facts flag the same set.
_SOFT_DELETE_COLUMN_NAMES = frozenset({
    "deleted_at", "archived_at", "removed_at", "canceled_at", "cancelled_at",
    "is_deleted", "is_archived", "is_removed",
})


def _is_sensitive_column(col_name: str) -> bool:
    name_lower = col_name.lower()
    return any(pat in name_lower for pat in SENSITIVE_COLUMN_PATTERNS)


def _col_desc(c: dict) -> str:
    base = f"{c['name']} ({c.get('type', '?')})"
    if c.get("comment"):
        base += f' — "{c["comment"]}"'
    top_values = c.get("top_values")
    if top_values:
        rendered = ", ".join(
            f"{tv['value']!r}={tv['frequency']:.0%}"
            for tv in top_values[:8]
            if tv.get("value") is not None
        )
        if rendered:
            label = "values" if c.get("values_exhaustive") else "top values"
            base += f" [{label}: {rendered}]"
    return base


def build_app_schema_description(
    model: SystemModel | None,
    question: str | None = None,
    max_chars: int = 50_000,
    full_detail_tables: int = 15,
    max_columns: int = 20,
) -> str:
    """Build a compact schema description of the monitored app's tables.

    Pipeline-audit Fix 3: when ``question`` is provided, score tables by
    keyword overlap and emit the top-N with full column detail PLUS a thin
    index (name + row count) for the remaining tables. This prevents the
    old alphabetical cut from silently dropping high-value aggregate views
    (e.g. anything starting with ``v_*``) past index 50.

    When ``question`` is None we keep the legacy behaviour: alphabetical,
    full detail for the first ``full_detail_tables`` and a thin index for
    the rest. ``max_chars`` is a hard ceiling enforced after assembly.
    """
    if model is None or not model.tables:
        return "(no application schema discovered)"

    all_tables = sorted(model.tables, key=lambda t: t.fqn)

    detail_tables: list = []
    if question:
        relevant = retrieve_relevant_tables(
            question, model, max_tables=full_detail_tables,
        )
        relevant_names = {t.name for t in relevant}
        # Preserve the relevance ordering for the detail section
        detail_tables = [t for t in relevant if t.name in relevant_names]

    if not detail_tables:
        # Either no question, or no keyword overlap — fall back to alphabetical
        detail_tables = all_tables[:full_detail_tables]

    detail_names = {t.name for t in detail_tables}

    lines: list[str] = []
    for table in detail_tables:
        safe_cols = [
            c for c in table.columns if not _is_sensitive_column(c.get("name", ""))
        ]
        cols = ", ".join(_col_desc(c) for c in safe_cols[:max_columns])
        if len(safe_cols) > max_columns:
            cols += ", ..."
        annotations: list[str] = []
        if table.row_count is not None:
            annotations.append(f"~{table.row_count} rows")
        soft_delete_cols = [
            c.get("name") for c in table.columns
            if isinstance(c, dict)
            and (c.get("name") or "").lower() in _SOFT_DELETE_COLUMN_NAMES
        ]
        if soft_delete_cols:
            annotations.append(
                f"soft-delete via {soft_delete_cols[0]} — filter unless "
                f"asking about deleted rows"
            )
        rls_policies = getattr(table, "rls_policies", None) or []
        if rls_policies:
            annotations.append(
                f"{len(rls_policies)} RLS policies — zero results may be "
                f"permission-filtered"
            )
        annotation_str = f" ({'; '.join(annotations)})" if annotations else ""
        lines.append(f"  {table.fqn}{annotation_str}: {cols}")

    remaining = [t for t in all_tables if t.name not in detail_names]
    if remaining:
        lines.append("")
        lines.append("  Other tables (name, approximate rows):")
        for table in remaining:
            row_hint = f" ~{table.row_count} rows" if table.row_count else ""
            lines.append(f"    {table.fqn}{row_hint}")

    result = "\n".join(lines)
    if len(result) > max_chars:
        cut = result[:max_chars]
        last_nl = cut.rfind("\n")
        if last_nl > 0:
            cut = cut[:last_nl]
        result = cut + "\n  [Schema truncated due to size]"
    return result


def build_observability_schema_description() -> str:
    """Build schema description for Observibot's internal store tables."""
    from observibot.core.store import metadata as store_metadata

    lines = []
    internal_tables = [
        "metric_snapshots", "insights", "alert_history",
        "change_events", "business_context", "llm_usage",
        "system_snapshots",
        # metric_baselines: re-add when seasonal baselines (Step 3) populate it.
    ]
    for name in internal_tables:
        table = store_metadata.tables.get(name)
        if table is None:
            continue
        cols = ", ".join(f"{c.name} ({c.type})" for c in table.columns)
        lines.append(f"  {name}: {cols}")
    return "\n".join(lines)


def get_app_table_names(model: SystemModel | None) -> set[str]:
    """Extract table names from the discovered system model."""
    if model is None:
        return set()
    return {t.name for t in model.tables}


def retrieve_relevant_tables(
    question: str,
    model: SystemModel,
    max_tables: int = 10,
) -> list[TableInfo]:
    """Keyword-based retrieval of relevant tables for a question."""
    question_lower = question.lower()
    scored: list[tuple[int, TableInfo]] = []
    for table in model.tables:
        score = 0
        for word in table.name.split("_"):
            if len(word) > 2 and word in question_lower:
                score += 10
        for col in table.columns:
            col_name = col.get("name", "") if isinstance(col, dict) else col
            for word in str(col_name).split("_"):
                if len(word) > 2 and word in question_lower:
                    score += 2
        if score > 0:
            scored.append((score, table))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:max_tables]]
