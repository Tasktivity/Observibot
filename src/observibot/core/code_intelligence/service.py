"""CodeKnowledgeService — interface for agents to get business context.

Stage 6 adds :meth:`CodeKnowledgeService.get_context_for_anomalies` as
a structured sibling to :meth:`get_context_for_question`. The anomaly
retriever feeds a prefix-stripped, label-allowlist-joined query into
``store.search_semantic_facts`` so the FTS signal reflects anomaly
content rather than infrastructure-prefix noise.

Note: canonical STOP_WORDS and token helpers live in
observibot.core.code_intelligence.retrieval. An earlier duplicate of
that set and an unused ``_extract_ngrams`` helper were removed from
this module in Stage 6; neither was reachable from any caller.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from observibot.core.code_intelligence.models import FactSource, FactType, SemanticFact
from observibot.core.store import Store

if TYPE_CHECKING:
    from observibot.core.anomaly import Anomaly
    from observibot.core.models import SystemModel

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# Stage 6: anomaly → FTS query construction
# -----------------------------------------------------------------------
#
# Infrastructure prefixes stripped from metric names before they enter
# the FTS query. These identify *where* a metric came from (Postgres'
# internal stat views, kernel-level probes, language runtimes, the
# managed platform layers we integrate with), not *what* the metric
# measures — leaving them in makes tokens like ``pg``/``stat``/
# ``database`` match too many unrelated facts and drown out the real
# signal. Every entry is Postgres/infrastructure vocabulary and applies
# identically to any customer. Extensions require a Tier 0 justification.
_INFRA_METRIC_PREFIXES: tuple[str, ...] = (
    "pg_stat_database_",
    "pg_stat_user_tables_",
    "pg_stat_user_indexes_",
    "pg_stat_bgwriter_",
    "pg_stat_activity_",
    "pg_locks_",
    "node_",
    "process_",
    "go_",
    "supavisor_",
    "railway_",
)

# Label keys whose *values* are plausibly relevant to fact retrieval.
# These are generic observability dimensions (what table, what schema,
# what service, what queue, what endpoint), not customer concepts. A
# label like ``cpu`` or ``job`` names the source, not the subject, so
# its value is noise for semantic retrieval. Extensions require a
# Tier 0 justification.
_LABEL_VALUE_ALLOWLIST: frozenset[str] = frozenset({
    "table",
    "schema",
    "service",
    "queue",
    "endpoint",
})

# Generic label values that should not contribute tokens even when the
# key is allowlisted — ``schema=public`` is universal and noisy;
# ``service=default`` likewise.
_GENERIC_LABEL_VALUES: frozenset[str] = frozenset({
    "public",
    "default",
    "",
})

# A looser, ASCII-only token splitter. ``build_fts5_query`` in
# ``retrieval.py`` owns the stop-word filter; this splitter just
# feeds it post-prefix-strip tokens joined with spaces.
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


def _strip_infra_prefix(metric_name: str) -> str:
    """Return ``metric_name`` with any known infrastructure prefix
    removed. Comparison is case-insensitive; only one prefix is
    stripped (the longest match), mirroring how the Prometheus-style
    prefixes compose.
    """
    lower = metric_name.lower()
    # Longest-match first so ``pg_stat_database_`` strips before
    # ``pg_`` hypothetically would (currently no plain ``pg_`` prefix
    # in the list — defensive).
    for prefix in sorted(_INFRA_METRIC_PREFIXES, key=len, reverse=True):
        if lower.startswith(prefix):
            return metric_name[len(prefix):]
    return metric_name


def _related_table_tokens(
    labels: dict[str, str] | None,
    system_model: SystemModel | None,
) -> list[str]:
    """Harvest label values + any related-column tokens from the
    system model for a table mentioned in the labels.

    Returns lowercased tokens, deduplicated in caller.
    """
    out: list[str] = []
    if not labels:
        return out
    for key, value in labels.items():
        if key not in _LABEL_VALUE_ALLOWLIST:
            continue
        value_str = str(value).strip().lower()
        if value_str in _GENERIC_LABEL_VALUES:
            continue
        out.append(value_str)
    # If a table label is present, pull the first few column names off
    # the system model as additional semantic-retrieval tokens. Column
    # names are customer vocabulary — the whole point of retrieval is
    # to surface facts about them.
    table_value = (labels.get("table") or "").strip().lower() if labels else ""
    if table_value and system_model is not None:
        for table in system_model.tables:
            if str(getattr(table, "name", "")).lower() != table_value:
                continue
            for col in list(table.columns)[:5]:
                if isinstance(col, dict):
                    col_name = str(col.get("name", "")).strip().lower()
                else:
                    col_name = str(col).strip().lower()
                if col_name:
                    out.append(col_name)
            break
    return out


def _build_anomaly_search_query(
    anomalies: list[Anomaly] | None,
    system_model: SystemModel | None,
) -> str:
    """Build an FTS-ready query string from anomaly metadata.

    Strategy (each step schema-agnostic):

    1. For each anomaly metric name, strip known infrastructure
       prefixes (``pg_stat_database_``, ``node_``, ``go_``, etc.)
       so Prometheus/platform noise doesn't drown the signal.
    2. Split the remainder on non-alphanumeric; drop empties.
    3. From each anomaly's ``labels`` dict, pull values whose keys
       are in the label-key allowlist (``table``, ``schema``,
       ``service``, ``queue``, ``endpoint``). Generic values
       (``public``, ``default``) are dropped.
    4. When a ``table`` label resolves against the system model,
       pull the top-5 column names off that table as extra tokens.
    5. Deduplicate (preserving first-seen order); join with spaces.

    Returns an empty string when there's nothing to search on —
    callers route that through ``store.search_semantic_facts`` which
    yields zero results for an empty query (the existing SQLite path
    falls through to "return whatever match it gets"; this is okay
    because the caller budgets the result count).
    """
    if not anomalies:
        return ""
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        if not token or token in seen:
            return
        seen.add(token)
        tokens.append(token)

    for a in anomalies:
        stripped = _strip_infra_prefix(getattr(a, "metric_name", "") or "")
        for raw in _TOKEN_SPLIT_RE.split(stripped):
            t = raw.strip().lower()
            if len(t) > 1:
                _add(t)
        for t in _related_table_tokens(
            getattr(a, "labels", None), system_model,
        ):
            for raw in _TOKEN_SPLIT_RE.split(t):
                piece = raw.strip().lower()
                if len(piece) > 1:
                    _add(piece)
    return " ".join(tokens)


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

    async def get_context_for_anomalies(
        self,
        anomalies: list[Anomaly],
        system_model: SystemModel | None,
        max_facts: int = 8,
        max_tokens: int = 1500,
    ) -> tuple[list[dict], str]:
        """Retrieve semantic facts relevant to an anomaly set.

        Returns ``(facts, freshness_status)`` where ``freshness_status``
        is one of ``"current" | "stale" | "unavailable" | "error"`` —
        the same set of values exposed by :meth:`get_freshness_status`.

        Unlike :meth:`get_context_for_question`, this takes structured
        anomaly input and builds an entity-first retrieval query from
        metric names (with infrastructure prefixes stripped), label
        values (filtered through a fixed allowlist), and — when the
        anomaly's labels include a table — related columns from the
        system model.

        When the code index is unavailable or errored, returns
        ``([], <status>)`` so the caller can append an
        :class:`EvidenceError` and render "not available" in the
        prompt. The call never raises on missing facts.
        """
        freshness = await self.get_freshness_status()
        status = str(freshness.get("status", "unavailable"))
        if status in ("unavailable", "error"):
            return [], status

        query = _build_anomaly_search_query(anomalies, system_model)
        if not query:
            return [], status

        # Mirror the question path's wider candidate pool so the
        # source-priority re-ranker has more to work with.
        fts_results = await self.store.search_semantic_facts(
            query, limit=max_facts * 3,
        )

        seen_ids: set[str] = set()
        ranked: list[dict] = []
        source_priority = {
            FactSource.USER_CORRECTION.value: 0,
            FactSource.CODE_EXTRACTION.value: 1,
            FactSource.SEMANTIC_MODELER.value: 2,
            FactSource.SCHEMA_ANALYSIS.value: 3,
        }
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
            est_tokens = (
                len(fact.get("claim", "")) // 4
                + len(str(fact.get("tables") or [])) // 4
                + len(str(fact.get("sql_condition") or "")) // 4
                + 30
            )
            if est_tokens > token_budget:
                break
            token_budget -= est_tokens
            result.append(fact)

        return result, status

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
