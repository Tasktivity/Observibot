"""Schema catalog — builds LLM-consumable descriptions of available tables."""
from __future__ import annotations

from observibot.core.models import SystemModel, TableInfo

SENSITIVE_COLUMN_PATTERNS = {
    "api_key", "api_token", "secret", "password", "hash",
    "token", "credential", "private_key", "embedding",
    "openai_api_key", "service_role_key",
}


def _is_sensitive_column(col_name: str) -> bool:
    name_lower = col_name.lower()
    return any(pat in name_lower for pat in SENSITIVE_COLUMN_PATTERNS)


def build_app_schema_description(model: SystemModel | None) -> str:
    """Build a compact schema description of the monitored app's tables."""
    if model is None or not model.tables:
        return "(no application schema discovered)"
    lines = []
    for table in sorted(model.tables, key=lambda t: t.fqn)[:50]:
        safe_cols = [c for c in table.columns if not _is_sensitive_column(c.get("name", ""))]
        cols = ", ".join(
            f"{c['name']} ({c.get('type', '?')})"
            for c in safe_cols[:15]
        )
        if len(safe_cols) > 15:
            cols += ", ..."
        row_hint = f" (~{table.row_count} rows)" if table.row_count else ""
        lines.append(f"  {table.fqn}{row_hint}: {cols}")
    return "\n".join(lines)


def build_observability_schema_description() -> str:
    """Build schema description for Observibot's internal store tables."""
    from observibot.core.store import metadata as store_metadata

    lines = []
    internal_tables = [
        "metric_snapshots", "insights", "alert_history",
        "change_events", "business_context", "llm_usage",
        "metric_baselines", "system_snapshots",
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
