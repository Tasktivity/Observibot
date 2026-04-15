"""CodeKnowledgeService — interface for agents to get business context."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from observibot.core.code_intelligence.models import FactSource, FactType, SemanticFact
from observibot.core.store import Store

log = logging.getLogger(__name__)

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "about", "between",
    "through", "during", "before", "after", "above", "below", "up", "down",
    "out", "off", "over", "under", "again", "further", "then", "once",
    "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "neither", "each", "every", "all", "any", "few", "more", "most",
    "other", "some", "such", "no", "only", "own", "same", "than", "too",
    "very", "just", "how", "many", "much", "what", "which", "who", "whom",
    "this", "that", "these", "those", "i", "me", "my", "we", "our", "you",
    "your", "he", "him", "his", "she", "her", "it", "its", "they", "them",
    "their", "show", "list", "get", "tell", "give", "find",
    "count", "total", "number", "recent", "latest", "last",
}


def _extract_ngrams(text: str, sizes: tuple[int, ...] = (2, 3)) -> set[str]:
    """Extract multi-word ngrams from text."""
    words = re.findall(r'\b\w+\b', text.lower())
    ngrams: set[str] = set()
    for n in sizes:
        for i in range(len(words) - n + 1):
            ngrams.add(" ".join(words[i:i + n]))
    return ngrams


class CodeKnowledgeService:
    """Interface that agents call to get business context."""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def should_inject_context(self, question: str) -> bool:
        """Always inject context. FTS retrieval + ranking is the real filter.

        Why: the prior conservative classifier excluded schema_analysis entity
        facts and dropped most questions, but the budget cost of injection is
        now ~500-800 tokens per query (well under the 3k budget). If FTS finds
        nothing relevant, get_context_for_question returns an empty list
        naturally, so an "always-on" gate has no observable downside.
        """
        return True

    async def get_context_for_question(
        self, question: str, max_facts: int = 10, max_tokens: int = 3000,
    ) -> list[dict]:
        """Retrieve relevant semantic facts for a user question."""
        # Pipeline-audit Fix 7: pull a wider candidate pool (3x final budget)
        # so the post-FTS sort has more to work with.
        fts_results = await self.store.search_semantic_facts(
            question, limit=max_facts * 3,
        )

        seen_ids: set[str] = set()
        ranked: list[dict] = []

        source_priority = {
            FactSource.USER_CORRECTION.value: 0,
            FactSource.CODE_EXTRACTION.value: 1,
            FactSource.SEMANTIC_MODELER.value: 2,
            FactSource.SCHEMA_ANALYSIS.value: 3,
        }

        # Pipeline-audit Fix 4: preserve FTS rank as a tiebreaker. Without
        # this, the source-priority sort silently dropped relevance ranking
        # so a low-relevance code_extraction fact would beat a high-relevance
        # schema_analysis fact about the actual question.
        for i, fact in enumerate(fts_results):
            if fact["id"] not in seen_ids:
                seen_ids.add(fact["id"])
                fact["_fts_rank"] = i
                ranked.append(fact)

        ranked.sort(key=lambda f: (
            source_priority.get(f.get("source", ""), 4),
            f.get("_fts_rank", 999),
            -(f.get("confidence", 0.0)),
        ))

        result: list[dict] = []
        token_budget = max_tokens
        for fact in ranked[:max_facts]:
            # More accurate estimate: format_context_for_prompt() emits
            # claim + tables + sql_condition + metadata. The old estimate
            # (claim // 4 + 20) underweighted facts with long tables lists
            # or long sql_condition and let oversized payloads through.
            est_tokens = (
                len(fact.get("claim", "")) // 4
                + len(str(fact.get("tables") or [])) // 4
                + len(str(fact.get("sql_condition") or "")) // 4
                + 30  # formatting overhead: "- \"concept\" means ... (meta)"
            )
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
        last_time_str = await self.store.get_code_intelligence_meta("last_extraction_at")
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
