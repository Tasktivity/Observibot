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


def _col_to_concept(col_name: str) -> str:
    m = _AT_SUFFIX.match(col_name)
    if m:
        return m.group(1).replace("_", " ")
    return col_name.replace("_", " ")
