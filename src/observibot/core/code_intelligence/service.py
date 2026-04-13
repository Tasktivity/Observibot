"""CodeKnowledgeService — interface for agents to get business context."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from observibot.core.code_intelligence.models import FactSource, FactType, SemanticFact
from observibot.core.store import Store

log = logging.getLogger(__name__)

STRUCTURAL_ONLY_PATTERN = re.compile(
    r"^(how many|count|total|list|show|what)\b.*"
    r"\b(users?|tables?|rows?|columns?|services?|metrics?|insights?|alerts?)\s*\??$",
    re.IGNORECASE,
)

SIMPLE_AGGREGATE_WORDS = {
    "count", "total", "how many", "list", "show me", "what are",
    "latest", "recent", "last",
}


class CodeKnowledgeService:
    """Interface that agents call to get business context."""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def should_inject_context(self, question: str) -> bool:
        """Deterministic question classifier: does this question need business context?

        Returns False for pure schema/aggregate questions like 'how many users?'
        Returns True for business-logic questions like 'how many onboarded users?'
        Conservative: returns False when uncertain.
        """
        q = question.lower().strip().rstrip("?").strip()

        concepts = await self.store.get_semantic_facts(active_only=True)
        concept_terms = {f["concept"].lower() for f in concepts}
        q_words = set(re.findall(r'\b\w+\b', q))
        if q_words & concept_terms:
            return True

        facts = await self.store.search_semantic_facts(q, limit=3)
        if facts:
            return True

        if STRUCTURAL_ONLY_PATTERN.match(q):
            return False

        return False

    async def get_context_for_question(
        self, question: str, max_facts: int = 5, max_tokens: int = 1500,
    ) -> list[dict]:
        """Retrieve relevant semantic facts for a user question."""
        fts_results = await self.store.search_semantic_facts(question, limit=max_facts * 2)

        seen_ids: set[str] = set()
        ranked: list[dict] = []

        source_priority = {
            FactSource.USER_CORRECTION.value: 0,
            FactSource.CODE_EXTRACTION.value: 1,
            FactSource.SEMANTIC_MODELER.value: 2,
            FactSource.SCHEMA_ANALYSIS.value: 3,
        }

        for fact in fts_results:
            if fact["id"] not in seen_ids:
                seen_ids.add(fact["id"])
                ranked.append(fact)

        ranked.sort(key=lambda f: (
            source_priority.get(f.get("source", ""), 4),
            -(f.get("confidence", 0.0)),
        ))

        result: list[dict] = []
        token_budget = max_tokens
        for fact in ranked[:max_facts]:
            est_tokens = len(fact.get("claim", "")) // 4 + 20
            if est_tokens > token_budget:
                break
            token_budget -= est_tokens
            result.append(fact)

        return result

    async def format_context_for_prompt(self, facts: list[dict]) -> str:
        """Format retrieved facts as a compact prompt section with evidence."""
        if not facts:
            return ""

        lines = ["## Business Context (relevant definitions for this question)"]
        for f in facts:
            parts = [f'"{f["concept"]}"']
            if f.get("claim"):
                parts.append(f"means {f['claim']}")
            if f.get("sql_condition"):
                parts.append(f"[SQL: {f['sql_condition']}]")

            meta_parts = []
            if f.get("tables"):
                tables = f["tables"] if isinstance(f["tables"], list) else [f["tables"]]
                meta_parts.append(f"table: {', '.join(tables)}")
            if f.get("confidence") is not None:
                meta_parts.append(f"confidence: {f['confidence']:.1f}")
            if f.get("source"):
                meta_parts.append(f"source: {f['source']}")

            line = " ".join(parts)
            if meta_parts:
                line += f" ({', '.join(meta_parts)})"
            lines.append(f"- {line}")

        return "\n".join(lines)

    async def get_freshness_status(self, stale_threshold_hours: int = 24) -> dict:
        """Get code intelligence freshness metadata."""
        last_commit = await self.store.get_code_intelligence_meta("last_indexed_commit")
        last_time_str = await self.store.get_code_intelligence_meta("last_index_time")
        error_msg = await self.store.get_code_intelligence_meta("index_error")

        if last_time_str is None:
            return {
                "status": "unavailable",
                "last_indexed_commit": None,
                "last_index_time": None,
                "error_message": None,
            }

        try:
            last_time = datetime.fromisoformat(last_time_str)
        except (ValueError, TypeError):
            return {
                "status": "error",
                "last_indexed_commit": last_commit,
                "last_index_time": last_time_str,
                "error_message": "Invalid timestamp format",
            }

        age = datetime.now(UTC) - last_time
        if error_msg:
            status = "error"
        elif age > timedelta(hours=stale_threshold_hours):
            status = "stale"
        else:
            status = "current"

        return {
            "status": status,
            "last_indexed_commit": last_commit,
            "last_index_time": last_time_str,
            "error_message": error_msg,
        }

    async def get_freshness_warning(self) -> str | None:
        """Get a warning message if code intelligence data is stale or unavailable."""
        freshness = await self.get_freshness_status()
        if freshness["status"] == "stale":
            return (
                f"Note: Business context may be outdated "
                f"(last updated {freshness['last_index_time']})."
            )
        return None

    async def record_correction(
        self, concept: str, claim: str, tables: list[str],
        columns: list[str], sql_condition: str | None,
    ) -> None:
        """Store a user-provided correction as a high-priority fact."""
        fact = SemanticFact(
            id=uuid.uuid4().hex[:12],
            fact_type=FactType.CORRECTION,
            concept=concept,
            claim=claim,
            tables=tables,
            columns=columns,
            sql_condition=sql_condition,
            source=FactSource.USER_CORRECTION,
            confidence=1.0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            is_active=True,
        )
        await self.store.save_semantic_fact(fact)
        log.info("Stored user correction for concept '%s'", concept)
