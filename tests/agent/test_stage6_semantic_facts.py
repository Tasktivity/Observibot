"""Stage 6 — semantic facts into the diagnostic hypothesis prompt.

Covers query construction (infra-prefix stripping, label-value
allowlist, deduplication), fact retrieval + freshness states, prompt
rendering (with / without / stale / unavailable / error), anti-
hallucination guardrail text, anti-hallucination data-leak check
(no path/lines/commit ever reaches the prompt), post-generation
reference validator, citation + freshness attachment on
``DiagnosticEvidence``, Tier 0 synthetic coverage, and the S0.6
snapshot tradeoff verification.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from observibot.agent.analyzer import (
    Analyzer,
    DiagnosticGenerationResult,
    _build_fact_citations,
    _render_semantic_facts_section,
    _retrieve_facts_for_diagnostics,
    _validate_code_references,
)
from observibot.agent.llm_provider import MockProvider
from observibot.agent.schemas import DiagnosticQuery
from observibot.core.code_intelligence.models import (
    FactSource,
    FactType,
    SemanticFact,
)
from observibot.core.code_intelligence.service import (
    CodeKnowledgeService,
    _build_anomaly_search_query,
)
from observibot.core.config import DiagnosticsConfig
from observibot.core.evidence import DiagnosticEvidence, EvidenceBundle
from observibot.core.models import MetricSnapshot
from observibot.core.store import Store
from tests.fixtures.synthetic_schemas import (
    ecommerce_anomaly,
    ecommerce_schema,
    event_stream_anomaly,
    event_stream_schema,
    medical_anomaly,
    medical_records_schema,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fact(
    *,
    concept: str,
    claim: str,
    tables: list[str] | None = None,
    columns: list[str] | None = None,
    source: FactSource = FactSource.CODE_EXTRACTION,
    confidence: float = 0.8,
    evidence_path: str | None = None,
    evidence_lines: str | None = None,
    evidence_commit: str | None = None,
) -> SemanticFact:
    return SemanticFact(
        id=uuid.uuid4().hex[:12],
        fact_type=FactType.DEFINITION,
        concept=concept,
        claim=claim,
        tables=tables or [],
        columns=columns or [],
        sql_condition=None,
        evidence_path=evidence_path,
        evidence_lines=evidence_lines,
        evidence_commit=evidence_commit,
        source=source,
        confidence=confidence,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        is_active=True,
    )


async def _seed_freshness(
    store: Store, status: str = "current",
) -> None:
    """Write code_intelligence_meta rows so ``get_freshness_status``
    resolves to the requested status.
    """
    if status == "current":
        await store.set_code_intelligence_meta(
            "last_indexed_commit", "commit-current",
        )
        await store.set_code_intelligence_meta(
            "last_extraction_at", datetime.now(UTC).isoformat(),
        )
    elif status == "stale":
        await store.set_code_intelligence_meta(
            "last_indexed_commit", "commit-stale",
        )
        old = datetime.now(UTC) - timedelta(hours=48)
        await store.set_code_intelligence_meta(
            "last_extraction_at", old.isoformat(),
        )
    elif status == "error":
        await store.set_code_intelligence_meta(
            "last_indexed_commit", "commit-err",
        )
        await store.set_code_intelligence_meta(
            "last_extraction_at", datetime.now(UTC).isoformat(),
        )
        await store.set_code_intelligence_meta(
            "index_error", "extractor raised TypeError",
        )
    # "unavailable" — write nothing.


@pytest.fixture
async def ci_store(tmp_path: Path):
    path = tmp_path / "stage6.db"
    async with Store(path) as store:
        yield store


# ---------------------------------------------------------------------------
# 1. Query construction — prefix stripping, label allowlist, dedup
# ---------------------------------------------------------------------------


def test_build_anomaly_search_query_strips_infra_prefixes() -> None:
    """Every known Postgres/infrastructure prefix is stripped before
    tokenization so ``pg``/``stat``/``database``/``node`` don't
    drown the FTS signal in generic-vocabulary matches.
    """
    a = ecommerce_anomaly(metric="pg_stat_database_xact_commit")
    a.labels = {}
    query = _build_anomaly_search_query([a], None)
    tokens = query.split()
    assert "pg" not in tokens
    assert "stat" not in tokens
    assert "database" not in tokens
    assert any(t in tokens for t in ("xact", "commit"))


def test_build_anomaly_search_query_strips_node_and_go_prefixes() -> None:
    a1 = ecommerce_anomaly(metric="node_cpu_seconds")
    a1.labels = {}
    a2 = ecommerce_anomaly(metric="go_goroutines")
    a2.labels = {}
    q1 = _build_anomaly_search_query([a1], None)
    q2 = _build_anomaly_search_query([a2], None)
    assert "node" not in q1.split()
    assert "cpu" in q1.split()
    assert "go" not in q2.split()
    assert "goroutines" in q2.split()


def test_build_anomaly_search_query_includes_label_tables() -> None:
    """The ``table`` label value ends up in the query token set."""
    a = ecommerce_anomaly(
        metric="order_count",
        labels={"table": "orders", "schema": "public"},
    )
    query = _build_anomaly_search_query([a], None)
    tokens = query.split()
    assert "orders" in tokens
    # Generic schema value is dropped — see _GENERIC_LABEL_VALUES.
    assert "public" not in tokens


def test_build_anomaly_search_query_label_allowlist_ignores_noise_labels() -> None:
    """Only table/schema/service/queue/endpoint contribute values.
    A ``job=foo`` or ``cpu=0`` label doesn't leak into the query."""
    a = ecommerce_anomaly(
        metric="order_count",
        labels={"job": "foo-worker", "cpu": "0", "table": "orders"},
    )
    query = _build_anomaly_search_query([a], None)
    tokens = query.split()
    assert "orders" in tokens
    assert "foo" not in tokens
    assert "worker" not in tokens


def test_build_anomaly_search_query_deduplicates_tokens() -> None:
    a1 = ecommerce_anomaly(
        metric="order_count", labels={"table": "orders"},
    )
    a2 = ecommerce_anomaly(
        metric="order_count", labels={"table": "orders"},
    )
    query = _build_anomaly_search_query([a1, a2], None)
    tokens = query.split()
    assert tokens.count("order") <= 1
    assert tokens.count("orders") <= 1
    assert tokens.count("count") <= 1


def test_build_anomaly_search_query_pulls_column_names_from_model() -> None:
    """When an anomaly labels a table that exists in the system model,
    the top-5 column names become additional retrieval tokens. The
    splitter treats underscores as token boundaries (matches FTS5
    tokenization), so ``order_status`` surfaces as ``order`` +
    ``status`` — either still matches the indexed facts.
    """
    a = ecommerce_anomaly(
        metric="row_count", labels={"table": "orders"},
    )
    model = ecommerce_schema()
    query = _build_anomaly_search_query([a], model)
    tokens = query.split()
    assert "orders" in tokens
    # Column-name fragments from the orders table (first 5 columns:
    # id, customer_id, order_status, subtotal_cents, archived_at).
    assert any(
        t in tokens
        for t in ("customer", "status", "subtotal", "archived")
    )


def test_build_anomaly_search_query_empty_for_empty_input() -> None:
    assert _build_anomaly_search_query([], None) == ""


# ---------------------------------------------------------------------------
# 2. get_context_for_anomalies — retrieval + freshness
# ---------------------------------------------------------------------------


async def test_get_context_for_anomalies_returns_facts_and_freshness(
    ci_store: Store,
) -> None:
    await _seed_freshness(ci_store, "current")
    await ci_store.save_semantic_fact(
        _fact(concept="order_status", claim="paid means captured"),
    )
    svc = CodeKnowledgeService(ci_store)
    facts, freshness = await svc.get_context_for_anomalies(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=ecommerce_schema(),
    )
    assert freshness == "current"
    assert any(f["concept"] == "order_status" for f in facts)


async def test_get_context_for_anomalies_stale_index_tags_freshness(
    ci_store: Store,
) -> None:
    await _seed_freshness(ci_store, "stale")
    await ci_store.save_semantic_fact(
        _fact(concept="order_status", claim="paid means captured"),
    )
    svc = CodeKnowledgeService(ci_store)
    _facts, freshness = await svc.get_context_for_anomalies(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=ecommerce_schema(),
    )
    assert freshness == "stale"


async def test_get_context_for_anomalies_unavailable_index_returns_empty(
    ci_store: Store,
) -> None:
    # No freshness seed → unavailable.
    svc = CodeKnowledgeService(ci_store)
    facts, freshness = await svc.get_context_for_anomalies(
        anomalies=[ecommerce_anomaly()],
        system_model=ecommerce_schema(),
    )
    assert facts == []
    assert freshness == "unavailable"


async def test_get_context_for_anomalies_error_index_returns_empty(
    ci_store: Store,
) -> None:
    await _seed_freshness(ci_store, "error")
    svc = CodeKnowledgeService(ci_store)
    facts, freshness = await svc.get_context_for_anomalies(
        anomalies=[ecommerce_anomaly()],
        system_model=ecommerce_schema(),
    )
    assert facts == []
    assert freshness == "error"


async def test_get_context_for_anomalies_budget_enforced(
    ci_store: Store,
) -> None:
    """When the cumulative estimate would exceed ``max_tokens``,
    retrieval stops adding facts — matches the budget path in
    ``get_context_for_question``.
    """
    await _seed_freshness(ci_store, "current")
    # 20 facts, each with a long claim (~800 chars = ~200 tokens each).
    long_claim = "x" * 800
    for i in range(20):
        await ci_store.save_semantic_fact(
            _fact(
                concept=f"order_concept_{i}",
                claim=long_claim,
                tables=["orders"],
            )
        )
    svc = CodeKnowledgeService(ci_store)
    facts, _status = await svc.get_context_for_anomalies(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=ecommerce_schema(),
        max_facts=20,
        max_tokens=400,  # budget ~= 2 facts
    )
    assert len(facts) <= 3  # strict budget, but allow 1 slack for formatting


# ---------------------------------------------------------------------------
# 3. Prompt rendering — section, guardrail, no leak of path/lines/commit
# ---------------------------------------------------------------------------


async def test_diagnostic_prompt_includes_semantic_facts_section(
    ci_store: Store, tmp_store,
) -> None:
    await _seed_freshness(ci_store, "current")
    await ci_store.save_semantic_fact(
        _fact(concept="order_status", claim="paid means captured"),
    )
    await ci_store.save_semantic_fact(
        _fact(concept="order_workflow", claim="created → paid → shipped"),
    )
    svc = CodeKnowledgeService(ci_store)

    captured: list[str] = []

    class _Spy(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            captured.append(user_prompt)
            return await super()._call(system_prompt, user_prompt)

    analyzer = Analyzer(provider=_Spy(canned={"queries": []}), store=tmp_store)
    await analyzer.generate_diagnostic_queries(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=ecommerce_schema(),
        code_service=svc,
    )
    assert captured
    prompt = captured[0]
    assert "Semantic facts" in prompt
    assert "order_status" in prompt
    assert "paid means captured" in prompt


async def test_diagnostic_prompt_omits_facts_when_unavailable(
    ci_store: Store, tmp_store,
) -> None:
    # No freshness seed → unavailable.
    svc = CodeKnowledgeService(ci_store)
    captured: list[str] = []

    class _Spy(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            captured.append(user_prompt)
            return await super()._call(system_prompt, user_prompt)

    analyzer = Analyzer(provider=_Spy(canned={"queries": []}), store=tmp_store)
    await analyzer.generate_diagnostic_queries(
        anomalies=[ecommerce_anomaly()],
        system_model=ecommerce_schema(),
        code_service=svc,
    )
    prompt = captured[0]
    assert "Semantic facts: not available" in prompt
    assert "index unavailable" in prompt


async def test_diagnostic_prompt_stale_note_prepended(
    ci_store: Store, tmp_store,
) -> None:
    await _seed_freshness(ci_store, "stale")
    await ci_store.save_semantic_fact(
        _fact(concept="order_status", claim="paid means captured"),
    )
    svc = CodeKnowledgeService(ci_store)
    captured: list[str] = []

    class _Spy(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            captured.append(user_prompt)
            return await super()._call(system_prompt, user_prompt)

    analyzer = Analyzer(provider=_Spy(canned={"queries": []}), store=tmp_store)
    await analyzer.generate_diagnostic_queries(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=ecommerce_schema(),
        code_service=svc,
    )
    prompt = captured[0]
    assert "Note: Code context is from commit commit-stale" in prompt
    assert "hours ago" in prompt
    assert "Facts may not reflect current HEAD" in prompt


def test_diagnostic_prompt_guardrail_forbids_path_citation() -> None:
    """The literal guardrail text from the Stage 6 spec must appear in
    the DIAGNOSTIC_HYPOTHESIS_PROMPT verbatim (not paraphrased).
    """
    from observibot.agent.prompts import DIAGNOSTIC_HYPOTHESIS_PROMPT

    guardrail = (
        "Treat semantic facts as hints about meaning, not as proof of\n"
        "  current behavior. Facts may reflect code from a previous\n"
        "  commit. Do NOT cite specific function names, file paths, or\n"
        "  line numbers in your hypothesis or explanation text — those\n"
        "  may be stale. You may reference CONCEPTS and TABLES mentioned\n"
        "  in the facts."
    )
    assert guardrail in DIAGNOSTIC_HYPOTHESIS_PROMPT


async def test_diagnostic_prompt_never_exposes_path_lines_commit(
    ci_store: Store, tmp_store,
) -> None:
    """LOAD-BEARING ANTI-HALLUCINATION TEST. Seed a fact with
    evidence_path, evidence_lines, and evidence_commit populated.
    Render the diagnostic prompt. Assert NONE of those three values
    appear anywhere in the rendered prompt text. The operator's
    trust model depends on this invariant holding: stale code
    references in the LLM's narrative text collapse trust in every
    insight that follows.
    """
    await _seed_freshness(ci_store, "current")
    await ci_store.save_semantic_fact(
        _fact(
            concept="billing_charge",
            claim="transitions order_status to paid",
            tables=["orders"],
            evidence_path="/src/services/billing.py",
            evidence_lines="42-55",
            evidence_commit="abc123def456",
        ),
    )
    svc = CodeKnowledgeService(ci_store)
    captured: list[str] = []

    class _Spy(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            captured.append(user_prompt)
            return await super()._call(system_prompt, user_prompt)

    analyzer = Analyzer(provider=_Spy(canned={"queries": []}), store=tmp_store)
    await analyzer.generate_diagnostic_queries(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=ecommerce_schema(),
        code_service=svc,
    )
    prompt = captured[0]
    assert "billing_charge" in prompt  # concept — ok to cite
    assert "/src/services/billing.py" not in prompt
    assert "42-55" not in prompt
    assert "abc123def456" not in prompt


# ---------------------------------------------------------------------------
# 4. Hallucination validator (soft)
# ---------------------------------------------------------------------------


def test_validate_code_references_flags_hallucinated_path() -> None:
    q = DiagnosticQuery(
        hypothesis="likely caused by /tmp/ghost/code.py",
        sql="SELECT 1 LIMIT 1",
        explanation="",
    )
    tag = _validate_code_references(q, [])
    assert tag is not None
    assert "could not be verified" in tag


def test_validate_code_references_silent_when_references_match() -> None:
    q = DiagnosticQuery(
        hypothesis="order_status transition",
        sql="SELECT 1 LIMIT 1",
        explanation="see order_workflow concept",
    )
    facts = [
        {"concept": "order_status", "claim": "paid means captured"},
        {"concept": "order_workflow", "claim": "created → paid → shipped"},
    ]
    assert _validate_code_references(q, facts) is None


def test_validate_code_references_silent_when_no_pathy_tokens() -> None:
    q = DiagnosticQuery(
        hypothesis="sudden spike",
        sql="SELECT 1 LIMIT 1",
        explanation="checks counts",
    )
    assert _validate_code_references(q, []) is None


# ---------------------------------------------------------------------------
# 5. DiagnosticEvidence citations + freshness population
# ---------------------------------------------------------------------------


def _fake_app_db():
    class _FakeConn:
        async def fetch(self, sql):
            return [{"n": 3}]

        async def fetchrow(self, sql):
            import json as _json
            return (_json.dumps([{"Plan": {"Total Cost": 100.0}}]),)

    class _FakeDb:
        def __init__(self):
            self.is_connected = True

        @asynccontextmanager
        async def acquire(self):
            yield _FakeConn()

    return _FakeDb()


async def test_diagnostic_evidence_has_fact_citations(
    ci_store: Store, tmp_store,
) -> None:
    await _seed_freshness(ci_store, "current")
    await ci_store.save_semantic_fact(
        _fact(
            concept="order_status",
            claim="paid means captured",
            tables=["orders"],
            evidence_path="/src/orders.py",
            evidence_lines="10-20",
            evidence_commit="commit-current",
        ),
    )
    svc = CodeKnowledgeService(ci_store)

    canned = {
        "queries": [
            {
                "hypothesis": "pending orders unusually high",
                "sql": "SELECT order_status, count(*) FROM orders GROUP BY 1 LIMIT 10",
                "explanation": "distinguishes paid vs pending",
            }
        ]
    }
    analyzer = Analyzer(provider=MockProvider(canned=canned), store=tmp_store)
    result = await analyzer.generate_diagnostic_queries(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=ecommerce_schema(),
        code_service=svc,
    )
    assert result.freshness == "current"
    assert len(result.facts) >= 1

    evidence = await analyzer.execute_diagnostics(
        queries=list(result),
        app_db=_fake_app_db(),
        system_model=ecommerce_schema(),
        cfg=DiagnosticsConfig(enabled=True),
        facts=result.facts,
        freshness=result.freshness,
    )
    assert len(evidence) == 1
    citations = evidence[0].fact_citations
    assert len(citations) >= 1
    cite = citations[0]
    assert cite.fact_id
    assert cite.concept == "order_status"
    assert cite.source == FactSource.CODE_EXTRACTION.value
    # Path/lines/commit come through to the carrier (UI renders them
    # — the LLM prompt never sees them).
    assert cite.path == "/src/orders.py"
    assert cite.lines == "10-20"
    assert cite.commit == "commit-current"
    # Claim truncated to 200 chars at most.
    assert len(cite.claim) <= 200


async def test_diagnostic_evidence_populates_code_freshness_current(
    ci_store: Store, tmp_store,
) -> None:
    await _seed_freshness(ci_store, "current")
    await ci_store.save_semantic_fact(_fact(concept="x", claim="y"))
    svc = CodeKnowledgeService(ci_store)
    analyzer = Analyzer(
        provider=MockProvider(canned={"queries": []}), store=tmp_store,
    )
    result = await analyzer.generate_diagnostic_queries(
        anomalies=[ecommerce_anomaly(labels={"table": "orders"})],
        system_model=ecommerce_schema(),
        code_service=svc,
    )
    assert result.freshness == "current"
    evidence = await analyzer.execute_diagnostics(
        queries=[
            DiagnosticQuery(
                hypothesis="h", sql="SELECT 1 FROM orders LIMIT 1",
            )
        ],
        app_db=_fake_app_db(),
        system_model=ecommerce_schema(),
        cfg=DiagnosticsConfig(enabled=True),
        facts=result.facts,
        freshness=result.freshness,
    )
    assert evidence[0].code_freshness == "current"


async def test_diagnostic_evidence_populates_code_freshness_stale(
    ci_store: Store, tmp_store,
) -> None:
    await _seed_freshness(ci_store, "stale")
    await ci_store.save_semantic_fact(_fact(concept="x", claim="y"))
    svc = CodeKnowledgeService(ci_store)
    analyzer = Analyzer(
        provider=MockProvider(canned={"queries": []}), store=tmp_store,
    )
    result = await analyzer.generate_diagnostic_queries(
        anomalies=[ecommerce_anomaly(labels={"table": "orders"})],
        system_model=ecommerce_schema(),
        code_service=svc,
    )
    evidence = await analyzer.execute_diagnostics(
        queries=[
            DiagnosticQuery(
                hypothesis="h", sql="SELECT 1 FROM orders LIMIT 1",
            )
        ],
        app_db=_fake_app_db(),
        system_model=ecommerce_schema(),
        cfg=DiagnosticsConfig(enabled=True),
        facts=result.facts,
        freshness=result.freshness,
    )
    assert evidence[0].code_freshness == "stale"


async def test_diagnostic_evidence_populates_code_freshness_unavailable(
    ci_store: Store, tmp_store,
) -> None:
    # No freshness seed → unavailable.
    svc = CodeKnowledgeService(ci_store)
    analyzer = Analyzer(
        provider=MockProvider(canned={"queries": []}), store=tmp_store,
    )
    result = await analyzer.generate_diagnostic_queries(
        anomalies=[ecommerce_anomaly(labels={"table": "orders"})],
        system_model=ecommerce_schema(),
        code_service=svc,
    )
    evidence = await analyzer.execute_diagnostics(
        queries=[
            DiagnosticQuery(
                hypothesis="h", sql="SELECT 1 FROM orders LIMIT 1",
            )
        ],
        app_db=_fake_app_db(),
        system_model=ecommerce_schema(),
        cfg=DiagnosticsConfig(enabled=True),
        facts=result.facts,
        freshness=result.freshness,
    )
    assert evidence[0].code_freshness == "unavailable"
    assert evidence[0].fact_citations == []


# ---------------------------------------------------------------------------
# 6. EvidenceBundle.errors populated on unavailable/error index (monitor)
# ---------------------------------------------------------------------------


async def test_evidence_bundle_errors_populated_on_unavailable_index(
    tmp_store,
) -> None:
    """When fact retrieval reports ``unavailable``, the monitor
    appends exactly one EvidenceError(stage="fact_retrieval") to the
    bundle. Operator sees the attempt-and-fail, not silence.
    """
    from unittest.mock import AsyncMock

    from observibot.alerting.base import AlertManager
    from observibot.core.config import (
        DiagnosticsConfig as _DiagCfg,
    )
    from observibot.core.config import (
        MonitorConfig,
        ObservibotConfig,
    )
    from observibot.core.monitor import build_monitor_loop

    cfg = ObservibotConfig()
    cfg.monitor = MonitorConfig(
        collection_interval_seconds=60,
        analysis_interval_seconds=120,
        discovery_interval_seconds=60,
        min_samples_for_baseline=3,
    )
    cfg.monitor.diagnostics = _DiagCfg(enabled=True)
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    loop = build_monitor_loop(
        config=cfg, connectors=[], store=tmp_store,
        analyzer=analyzer,
        alert_manager=AlertManager(channels=[]),
    )
    loop._cached_model = ecommerce_schema()

    class _FakeAppDb:
        is_connected = True

        @asynccontextmanager
        async def acquire(self):
            yield object()

    loop._app_db = _FakeAppDb()

    # Make the analyzer's generate_diagnostic_queries return a
    # DiagnosticGenerationResult flagged with an unavailable
    # error_reason, as Stage 6 would when the index is missing.
    loop.analyzer.generate_diagnostic_queries = AsyncMock(
        return_value=DiagnosticGenerationResult(
            queries=[],
            facts=[],
            freshness="unavailable",
            error_reason="code index unavailable (no extraction record)",
        )
    )
    loop.analyzer.execute_diagnostics = AsyncMock(return_value=[])

    bundle = EvidenceBundle()
    await loop._maybe_run_diagnostics(
        [ecommerce_anomaly(severity="critical")], bundle,
    )
    assert len(bundle.errors) == 1
    err = bundle.errors[0]
    assert err.stage == "fact_retrieval"
    assert "code index unavailable" in err.reason


# ---------------------------------------------------------------------------
# 7. Dead-code cleanup regression
# ---------------------------------------------------------------------------


def test_dead_stop_words_removed_from_service_module() -> None:
    """Stage 6 cleanup: ``STOP_WORDS`` and ``_extract_ngrams`` used to
    live in ``service.py`` as dead duplicates of the live
    ``retrieval.py`` copies. Confirm both are gone."""
    from observibot.core.code_intelligence import service as svc_mod

    assert not hasattr(svc_mod, "STOP_WORDS"), (
        "STOP_WORDS duplicate should have been removed from service.py; "
        "canonical copy lives in retrieval.py"
    )
    assert not hasattr(svc_mod, "_extract_ngrams"), (
        "_extract_ngrams duplicate should have been removed from service.py"
    )
    # And the canonical retrieval module still has its copy.
    from observibot.core.code_intelligence import retrieval as ret_mod

    src = Path(ret_mod.__file__).read_text()
    assert '"the"' in src  # stop_words set still present


# ---------------------------------------------------------------------------
# 8. Tier 0 — synthetic fixture coverage
# ---------------------------------------------------------------------------


async def test_get_context_for_anomalies_ecommerce(ci_store: Store) -> None:
    await _seed_freshness(ci_store, "current")
    await ci_store.save_semantic_fact(
        _fact(concept="order_status", claim="paid means captured", tables=["orders"]),
    )
    await ci_store.save_semantic_fact(
        _fact(concept="patient_encounter", claim="not relevant to orders", tables=["encounters"]),
    )
    svc = CodeKnowledgeService(ci_store)
    facts, _fr = await svc.get_context_for_anomalies(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=ecommerce_schema(),
    )
    concepts = [f["concept"] for f in facts]
    assert "order_status" in concepts
    # A medical fact wouldn't beat an order-specific fact on rank.
    assert concepts[0] == "order_status"


async def test_get_context_for_anomalies_medical(ci_store: Store) -> None:
    await _seed_freshness(ci_store, "current")
    await ci_store.save_semantic_fact(
        _fact(
            concept="patient_admission",
            claim="encounter_type inpatient marks admission",
            tables=["encounters", "patients"],
        ),
    )
    await ci_store.save_semantic_fact(
        _fact(
            concept="order_status",
            claim="not relevant to medical",
            tables=["orders"],
        ),
    )
    svc = CodeKnowledgeService(ci_store)
    facts, _fr = await svc.get_context_for_anomalies(
        anomalies=[
            medical_anomaly(
                metric="encounter_count",
                labels={"table": "encounters"},
            )
        ],
        system_model=medical_records_schema(),
    )
    concepts = [f["concept"] for f in facts]
    assert "patient_admission" in concepts
    assert concepts[0] == "patient_admission"


async def test_get_context_for_anomalies_event_stream(ci_store: Store) -> None:
    await _seed_freshness(ci_store, "current")
    await ci_store.save_semantic_fact(
        _fact(
            concept="session_tracking",
            claim="sessions group events by user",
            tables=["sessions", "events"],
        ),
    )
    await ci_store.save_semantic_fact(
        _fact(
            concept="diagnosis_coding",
            claim="not relevant to events",
            tables=["diagnoses"],
        ),
    )
    svc = CodeKnowledgeService(ci_store)
    facts, _fr = await svc.get_context_for_anomalies(
        anomalies=[
            event_stream_anomaly(
                metric="event_count", labels={"table": "events"},
            )
        ],
        system_model=event_stream_schema(),
    )
    concepts = [f["concept"] for f in facts]
    assert "session_tracking" in concepts
    assert concepts[0] == "session_tracking"


# ---------------------------------------------------------------------------
# 9. S0.6 snapshot tradeoff verification (Checkpoint 1 cleanup #3)
# ---------------------------------------------------------------------------


async def test_s06_snapshot_model_resolves_schema_from_snapshot(
    ci_store: Store, tmp_store,
) -> None:
    """S0.6 guarantee: schema references in the rendered diagnostic
    prompt resolve against the snapshotted model. Stage 6 tradeoff:
    fact retrieval queries the LIVE FTS index, so a fact inserted
    mid-cycle MAY appear. Document both by asserting them here.
    """
    await _seed_freshness(ci_store, "current")
    # Seed a fact that will FTS-match the anomaly's tokens (order /
    # orders / count) so we can observe it in the pre-cycle snapshot.
    await ci_store.save_semantic_fact(
        _fact(
            concept="seeded_fact",
            claim="orders placed count includes only completed_at non-null",
            tables=["orders"],
        ),
    )
    svc = CodeKnowledgeService(ci_store)

    captured: list[str] = []

    class _Spy(MockProvider):
        async def _call(self, system_prompt, user_prompt):
            # Simulate a mid-cycle discovery by inserting a NEW fact
            # after the hypothesis prompt is already being built. The
            # current Stage-6 retriever ran BEFORE _call, so the new
            # fact doesn't appear in the prompt; schema references
            # still resolve against whatever system_model was passed in.
            await ci_store.save_semantic_fact(
                _fact(
                    concept="mid_cycle_fact",
                    claim="mid cycle fact for orders placed after retrieval began",
                    tables=["orders"],
                ),
            )
            captured.append(user_prompt)
            return await super()._call(system_prompt, user_prompt)

    analyzer = Analyzer(provider=_Spy(canned={"queries": []}), store=tmp_store)
    snapshot = ecommerce_schema()
    await analyzer.generate_diagnostic_queries(
        anomalies=[
            ecommerce_anomaly(
                metric="order_count", labels={"table": "orders"},
            )
        ],
        system_model=snapshot,
        code_service=svc,
    )
    prompt = captured[0]
    # Schema in the prompt resolves against the snapshotted model:
    # every table in our snapshot appears in the rendered schema text.
    assert "orders" in prompt
    # The seeded fact (inserted before the cycle) is present; the
    # mid-cycle fact was inserted AFTER retrieval and is absent from
    # this cycle's prompt — but it WILL appear on the NEXT cycle's
    # retrieval because there's no upper-bound filter on the FTS
    # query. That's the explicit S0.6 tradeoff.
    assert "seeded_fact" in prompt
    assert "mid_cycle_fact" not in prompt

    # Second cycle: the mid-cycle fact is now visible to FTS and
    # would appear if retrieval re-runs. Verify via a direct search.
    hits = await ci_store.search_semantic_facts("mid_cycle_fact")
    assert any(h["concept"] == "mid_cycle_fact" for h in hits)


# ---------------------------------------------------------------------------
# 10. Helpers: builder + citation builder smoke
# ---------------------------------------------------------------------------


def test_build_fact_citations_truncates_claim_to_200_chars() -> None:
    fact = {
        "id": "f1",
        "concept": "c",
        "claim": "y" * 500,
        "source": "code_extraction",
        "confidence": 0.7,
        "evidence_path": "/x/y.py",
        "evidence_lines": "1-5",
        "evidence_commit": "abc",
    }
    cites = _build_fact_citations([fact])
    assert len(cites) == 1
    assert len(cites[0].claim) == 200
    assert cites[0].path == "/x/y.py"
    assert cites[0].repo is None


async def test_retrieve_facts_for_diagnostics_no_service_is_noop(
    tmp_store,
) -> None:
    facts, freshness, err = await _retrieve_facts_for_diagnostics(
        code_service=None,
        anomalies=[ecommerce_anomaly()],
        system_model=ecommerce_schema(),
    )
    assert facts == []
    assert freshness is None
    assert err is None


async def test_render_semantic_facts_section_no_service() -> None:
    out = await _render_semantic_facts_section(
        code_service=None, facts=[], freshness=None,
    )
    assert "not available" in out
