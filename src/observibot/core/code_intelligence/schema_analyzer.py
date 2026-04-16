"""Schema-derived semantic fact generation from SystemModel structure."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from observibot.core.code_intelligence.models import FactSource, FactType, SemanticFact
from observibot.core.models import SystemModel
from observibot.core.store import Store

log = logging.getLogger(__name__)


async def analyze_schema_for_facts(
    system_model: SystemModel, store: Store,
) -> list[SemanticFact]:
    """Extract semantic facts from schema structure and naming patterns."""
    facts: list[SemanticFact] = []

    for table in system_model.tables:
        facts.extend(_analyze_table(table))

    for rel in system_model.relationships:
        facts.append(_relationship_fact(rel))

    for fact in facts:
        await store.save_semantic_fact(fact)

    # Clean up any duplicates from prior runs
    removed = await store.dedup_semantic_facts()
    if removed:
        log.info("Deduplicated %d existing semantic facts", removed)

    log.info("Generated %d schema-derived semantic facts", len(facts))
    return facts


def _make_id() -> str:
    return uuid.uuid4().hex[:12]


def _analyze_table(table) -> list[SemanticFact]:
    facts: list[SemanticFact] = []

    facts.append(SemanticFact(
        id=_make_id(),
        fact_type=FactType.ENTITY,
        concept=table.name,
        claim=(
            f"{table.fqn} table with {table.row_count or '?'} rows, "
            f"columns: {', '.join(c.get('name', '') for c in table.columns[:8])}"
        ),
        tables=[table.name],
        columns=[f"{table.name}.{c.get('name', '')}" for c in table.columns[:8]],
        source=FactSource.SCHEMA_ANALYSIS,
        confidence=0.9,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    ))

    soft_delete_cols = [
        c.get("name") for c in table.columns
        if isinstance(c, dict) and (c.get("name") or "").lower()
        in _SOFT_DELETE_COLUMN_NAMES
    ]
    if soft_delete_cols:
        primary = soft_delete_cols[0]
        if primary.startswith("is_"):
            filter_hint = f"WHERE {primary} = false"
        else:
            filter_hint = f"WHERE {primary} IS NULL"
        facts.append(SemanticFact(
            id=_make_id(),
            fact_type=FactType.RULE,
            concept=f"{table.name} soft-delete filter",
            claim=(
                f"{table.name} uses soft-deletes via {', '.join(soft_delete_cols)}. "
                f"COUNT / trend queries must add `{filter_hint}` unless the "
                f"question is explicitly about deleted or archived rows — "
                f"otherwise results include tombstones and will disagree with "
                f"what the application shows."
            ),
            tables=[table.name],
            columns=[f"{table.name}.{c}" for c in soft_delete_cols],
            sql_condition=filter_hint,
            source=FactSource.SCHEMA_ANALYSIS,
            confidence=0.9,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ))

    rls_policies = getattr(table, "rls_policies", None) or []
    if rls_policies:
        policy_names = ", ".join(
            p.get("name", "?") for p in rls_policies[:8]
            if isinstance(p, dict)
        )
        facts.append(SemanticFact(
            id=_make_id(),
            fact_type=FactType.RULE,
            concept=f"{table.name} row-level security",
            claim=(
                f"{table.name} has {len(rls_policies)} row-level security "
                f"policies ({policy_names}). Non-superuser / non-service-role "
                f"sessions will see a subset of rows; a zero or low COUNT may "
                f"mean 'blocked by RLS' rather than 'no data exists'. Do not "
                f"conclude absence without confirming session role privileges."
            ),
            tables=[table.name],
            columns=[],
            source=FactSource.SCHEMA_ANALYSIS,
            confidence=0.95,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ))

    for col in table.columns:
        col_name = col.get("name", "")
        col_type = col.get("type", "")
        comment = col.get("comment")

        if comment:
            facts.append(SemanticFact(
                id=_make_id(),
                fact_type=FactType.DEFINITION,
                concept=f"{table.name}.{col_name}",
                claim=comment,
                tables=[table.name],
                columns=[f"{table.name}.{col_name}"],
                source=FactSource.SCHEMA_ANALYSIS,
                confidence=0.95,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ))

        if col_name.endswith("_at") and "timestamp" in col_type.lower():
            facts.append(SemanticFact(
                id=_make_id(),
                fact_type=FactType.MAPPING,
                concept=_col_to_concept(col_name),
                claim=f"{col_name} is a timestamp tracking when this event occurred",
                tables=[table.name],
                columns=[f"{table.name}.{col_name}"],
                sql_condition=f"{col_name} IS NOT NULL",
                source=FactSource.SCHEMA_ANALYSIS,
                confidence=0.7,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ))

        if col_name == "status" or col_name.endswith("_status"):
            facts.append(SemanticFact(
                id=_make_id(),
                fact_type=FactType.WORKFLOW,
                concept=f"{table.name} status",
                claim=f"{table.name} has a status column ({col_name}) implying a state machine",
                tables=[table.name],
                columns=[f"{table.name}.{col_name}"],
                source=FactSource.SCHEMA_ANALYSIS,
                confidence=0.6,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ))

        top_values = col.get("top_values") if isinstance(col, dict) else None
        if top_values:
            rendered = ", ".join(
                f"{tv['value']!r} ({tv['frequency']:.0%})"
                for tv in top_values[:10]
                if tv.get("value") is not None
            )
            if rendered:
                qualifier = (
                    "actual values"
                    if col.get("values_exhaustive")
                    else "top observed values"
                )
                facts.append(SemanticFact(
                    id=_make_id(),
                    fact_type=FactType.DEFINITION,
                    concept=f"{table.name}.{col_name} values",
                    claim=(
                        f"{table.name}.{col_name} {qualifier}: {rendered}. "
                        f"Use these exact strings in WHERE/CASE filters."
                    ),
                    tables=[table.name],
                    columns=[f"{table.name}.{col_name}"],
                    source=FactSource.SCHEMA_ANALYSIS,
                    confidence=0.95,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                ))

        if col_name.startswith("is_") or col_name.startswith("has_"):
            concept_name = col_name.replace("is_", "").replace("has_", "")
            facts.append(SemanticFact(
                id=_make_id(),
                fact_type=FactType.MAPPING,
                concept=concept_name,
                claim=f"{col_name} is a boolean flag on {table.name}",
                tables=[table.name],
                columns=[f"{table.name}.{col_name}"],
                sql_condition=f"{col_name} = true",
                source=FactSource.SCHEMA_ANALYSIS,
                confidence=0.7,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ))

    return facts


def _relationship_fact(rel) -> SemanticFact:
    return SemanticFact(
        id=_make_id(),
        fact_type=FactType.MAPPING,
        concept=f"{rel.from_table} to {rel.to_table}",
        claim=(
            f"{rel.from_table} references {rel.to_table} "
            f"via {rel.from_column} → {rel.to_column}"
        ),
        tables=[rel.from_table, rel.to_table],
        columns=[
            f"{rel.from_table}.{rel.from_column}",
            f"{rel.to_table}.{rel.to_column}",
        ],
        source=FactSource.SCHEMA_ANALYSIS,
        confidence=0.95,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


_AT_SUFFIX = re.compile(r"(.+)_at$")

# Column names that indicate a soft-delete / tombstone pattern. When a table
# has one of these, queries that don't filter it will silently include
# deleted rows in counts and trends — reliably wrong in user-facing answers.
_SOFT_DELETE_COLUMN_NAMES = frozenset({
    "deleted_at", "archived_at", "removed_at", "canceled_at", "cancelled_at",
    "is_deleted", "is_archived", "is_removed",
})


def _col_to_concept(col_name: str) -> str:
    m = _AT_SUFFIX.match(col_name)
    if m:
        return m.group(1).replace("_", " ")
    return col_name.replace("_", " ")
