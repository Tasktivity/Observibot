"""Tests for the code intelligence shared knowledge layer."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from observibot.core.code_intelligence.models import (
    FactSource,
    FactType,
    SemanticFact,
)
from observibot.core.code_intelligence.retrieval import build_fts5_query
from observibot.core.code_intelligence.service import CodeKnowledgeService
from observibot.core.store import Store


def _make_fact(
    concept: str = "onboarded",
    claim: str = "completed_onboarding_at IS NOT NULL",
    tables: list[str] | None = None,
    columns: list[str] | None = None,
    sql_condition: str | None = "completed_onboarding_at IS NOT NULL",
    source: FactSource = FactSource.SCHEMA_ANALYSIS,
    confidence: float = 0.8,
    fact_type: FactType = FactType.DEFINITION,
    is_active: bool = True,
) -> SemanticFact:
    return SemanticFact(
        id=uuid.uuid4().hex[:12],
        fact_type=fact_type,
        concept=concept,
        claim=claim,
        tables=tables or ["users"],
        columns=columns or ["users.completed_onboarding_at"],
        sql_condition=sql_condition,
        source=source,
        confidence=confidence,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        is_active=is_active,
    )


@pytest.fixture
async def ci_store(tmp_path: Path):
    path = tmp_path / "ci_store.db"
    async with Store(path) as store:
        yield store


class TestSemanticFactStorage:
    async def test_save_and_retrieve_fact(self, ci_store: Store):
        fact = _make_fact()
        await ci_store.save_semantic_fact(fact)

        results = await ci_store.get_semantic_facts(concept="onboarded")
        assert len(results) == 1
        assert results[0]["concept"] == "onboarded"
        assert results[0]["claim"] == "completed_onboarding_at IS NOT NULL"
        assert results[0]["tables"] == ["users"]

    async def test_save_multiple_facts(self, ci_store: Store):
        f1 = _make_fact(concept="onboarded")
        f2 = _make_fact(concept="active_user", claim="last_login_at within 30 days")
        await ci_store.save_semantic_fact(f1)
        await ci_store.save_semantic_fact(f2)

        all_facts = await ci_store.get_semantic_facts()
        assert len(all_facts) == 2

    async def test_upsert_existing_fact(self, ci_store: Store):
        fact = _make_fact()
        await ci_store.save_semantic_fact(fact)

        fact.claim = "updated claim"
        fact.confidence = 0.95
        await ci_store.save_semantic_fact(fact)

        results = await ci_store.get_semantic_facts(concept="onboarded")
        assert len(results) == 1
        assert results[0]["claim"] == "updated claim"
        assert results[0]["confidence"] == 0.95

    async def test_filter_by_fact_type(self, ci_store: Store):
        f1 = _make_fact(fact_type=FactType.DEFINITION)
        f2 = _make_fact(concept="workflow", fact_type=FactType.WORKFLOW, claim="task lifecycle")
        await ci_store.save_semantic_fact(f1)
        await ci_store.save_semantic_fact(f2)

        defs = await ci_store.get_semantic_facts(fact_type="definition")
        assert len(defs) == 1
        assert defs[0]["concept"] == "onboarded"

    async def test_deactivate_fact(self, ci_store: Store):
        fact = _make_fact()
        await ci_store.save_semantic_fact(fact)

        await ci_store.deactivate_semantic_fact(fact.id)

        active = await ci_store.get_semantic_facts(active_only=True)
        assert len(active) == 0

        all_facts = await ci_store.get_semantic_facts(active_only=False)
        assert len(all_facts) == 1
        assert all_facts[0]["is_active"] is False

    async def test_inactive_facts_excluded_by_default(self, ci_store: Store):
        active = _make_fact(concept="active_concept")
        inactive = _make_fact(concept="old_concept", is_active=False)
        await ci_store.save_semantic_fact(active)
        await ci_store.save_semantic_fact(inactive)

        results = await ci_store.get_semantic_facts()
        assert len(results) == 1
        assert results[0]["concept"] == "active_concept"


class TestFTS5Search:
    async def test_search_by_concept(self, ci_store: Store):
        fact = _make_fact(concept="onboarded", claim="user completed onboarding")
        await ci_store.save_semantic_fact(fact)

        results = await ci_store.search_semantic_facts("onboarded")
        assert len(results) == 1
        assert results[0]["concept"] == "onboarded"

    async def test_search_by_claim(self, ci_store: Store):
        fact = _make_fact(concept="signup", claim="user registration flow completes")
        await ci_store.save_semantic_fact(fact)

        results = await ci_store.search_semantic_facts("registration")
        assert len(results) == 1

    async def test_search_ranking_exact_over_partial(self, ci_store: Store):
        exact = _make_fact(concept="onboarded", claim="user completed onboarding", confidence=0.9)
        partial = _make_fact(
            concept="user_status", claim="general user state tracking", confidence=0.5,
        )
        await ci_store.save_semantic_fact(exact)
        await ci_store.save_semantic_fact(partial)

        results = await ci_store.search_semantic_facts("onboarded")
        assert len(results) >= 1
        assert results[0]["concept"] == "onboarded"

    async def test_search_no_results(self, ci_store: Store):
        fact = _make_fact()
        await ci_store.save_semantic_fact(fact)

        results = await ci_store.search_semantic_facts("xyznonexistent")
        assert len(results) == 0

    async def test_search_limit(self, ci_store: Store):
        for i in range(10):
            f = _make_fact(concept=f"concept_{i}", claim=f"claim about users {i}")
            await ci_store.save_semantic_fact(f)

        results = await ci_store.search_semantic_facts("users", limit=3)
        assert len(results) <= 3


class TestUserCorrections:
    async def test_user_correction_highest_confidence(self, ci_store: Store):
        auto = _make_fact(
            concept="onboarded", source=FactSource.SCHEMA_ANALYSIS, confidence=0.7,
        )
        await ci_store.save_semantic_fact(auto)

        await ci_store.save_user_correction(
            concept="onboarded",
            claim="email_verified = true AND profile_complete = true",
            tables=["users"],
            columns=["users.email_verified", "users.profile_complete"],
            sql_condition="email_verified = true AND profile_complete = true",
        )

        results = await ci_store.get_semantic_facts(concept="onboarded")
        assert len(results) == 2
        corrections = [r for r in results if r["source"] == "user_correction"]
        assert corrections[0]["confidence"] == 1.0

    async def test_correction_takes_precedence_in_search(self, ci_store: Store):
        auto = _make_fact(
            concept="active", claim="automated definition", confidence=0.6,
        )
        correction = _make_fact(
            concept="active", claim="user corrected definition",
            source=FactSource.USER_CORRECTION, confidence=1.0,
        )
        await ci_store.save_semantic_fact(auto)
        await ci_store.save_semantic_fact(correction)

        service = CodeKnowledgeService(ci_store)
        facts = await service.get_context_for_question("active users")
        assert len(facts) >= 1
        assert facts[0]["source"] == "user_correction"


class TestQuestionClassifier:
    """Pipeline-audit Fix 8: classifier is now always-on.

    Why: the prior conservative gate excluded schema_analysis entity facts
    and silently dropped most questions; FTS retrieval + ranking now provides
    the actual relevance filter (returns [] when nothing matches).
    """

    async def test_always_returns_true(self, ci_store: Store):
        service = CodeKnowledgeService(ci_store)
        assert await service.should_inject_context("how many users?") is True
        assert await service.should_inject_context("list all tables") is True
        assert await service.should_inject_context("what is the churn rate?") is True

    async def test_business_term_triggers_context(self, ci_store: Store):
        fact = _make_fact(concept="onboarded")
        await ci_store.save_semantic_fact(fact)

        service = CodeKnowledgeService(ci_store)
        assert await service.should_inject_context("how many onboarded users?") is True

    async def test_retrieval_returns_empty_for_unrelated_questions(
        self, ci_store: Store
    ):
        """When the gate is open, retrieval is the filter — no false matches."""
        service = CodeKnowledgeService(ci_store)
        facts = await service.get_context_for_question(
            "what color is the sky on mars?"
        )
        assert facts == []


class TestFormatContextForPrompt:
    async def test_empty_facts_returns_empty(self, ci_store: Store):
        service = CodeKnowledgeService(ci_store)
        result = await service.format_context_for_prompt([])
        assert result == ""

    async def test_format_includes_metadata(self, ci_store: Store):
        facts = [{
            "concept": "onboarded",
            "claim": "completed_onboarding_at IS NOT NULL",
            "sql_condition": "completed_onboarding_at IS NOT NULL",
            "tables": ["users"],
            "confidence": 0.9,
            "source": "user_correction",
        }]
        service = CodeKnowledgeService(ci_store)
        result = await service.format_context_for_prompt(facts)
        assert "onboarded" in result
        assert "completed_onboarding_at IS NOT NULL" in result
        assert "table: users" in result
        assert "confidence: 0.9" in result
        assert "source: user_correction" in result


class TestTokenBudget:
    async def test_truncation_within_budget(self, ci_store: Store):
        for i in range(20):
            f = _make_fact(
                concept=f"concept_{i}",
                claim=f"a very long claim about concept {i} " * 10,
            )
            await ci_store.save_semantic_fact(f)

        service = CodeKnowledgeService(ci_store)
        facts = await service.get_context_for_question(
            "concept", max_facts=20, max_tokens=200,
        )
        total_chars = sum(len(f.get("claim", "")) for f in facts)
        assert total_chars < 200 * 4 + 200


class TestRetrievalRankingAndBudget:
    """Pipeline-audit Fix 4 + Fix 7: FTS rank preserved as tiebreaker, and
    larger default budget so complex questions get more facts."""

    async def test_default_budget_raised(self, ci_store: Store):
        """Default max_facts=10 (was 5), max_tokens=3000 (was 1500)."""
        for i in range(20):
            await ci_store.save_semantic_fact(_make_fact(
                concept=f"order_metric_{i}",
                claim=f"order metric definition number {i}",
                source=FactSource.SCHEMA_ANALYSIS,
            ))

        service = CodeKnowledgeService(ci_store)
        facts = await service.get_context_for_question("order metric")
        # New default lets up to 10 through (was capped at 5)
        assert len(facts) > 5
        assert len(facts) <= 10

    async def test_fts_rank_used_as_tiebreaker_within_same_source(
        self, ci_store: Store
    ):
        """Within the same source priority, the higher-FTS-relevance fact wins.

        Why: previously the sort key was (source_priority, -confidence) which
        completely discarded FTS rank. A high-relevance fact with confidence
        0.7 would lose to a less-relevant one with confidence 0.8.
        """
        # Both schema_analysis (same source priority) — only FTS rank should
        # determine the ordering between them.
        relevant = _make_fact(
            concept="inventory_sync_health",
            claim="v_inventory_sync_health view tracks inventory sync errors",
            source=FactSource.SCHEMA_ANALYSIS,
            confidence=0.6,
        )
        unrelated = _make_fact(
            concept="generic_record",
            claim="generic record without inventory context",
            source=FactSource.SCHEMA_ANALYSIS,
            confidence=0.95,
        )
        await ci_store.save_semantic_fact(relevant)
        await ci_store.save_semantic_fact(unrelated)

        service = CodeKnowledgeService(ci_store)
        facts = await service.get_context_for_question("inventory sync errors")
        assert facts, "FTS should return at least the relevant fact"
        # The FTS-most-relevant fact must rank first within its source bucket
        # even though it has a lower confidence score.
        assert facts[0]["concept"] == "inventory_sync_health"

    async def test_user_corrections_still_outrank_fts(self, ci_store: Store):
        """Source priority still wins overall — corrections beat everything."""
        correction = _make_fact(
            concept="active_user",
            claim="user correction definition",
            source=FactSource.USER_CORRECTION,
            confidence=1.0,
        )
        relevant_schema = _make_fact(
            concept="active_user",
            claim="active user from schema analysis",
            source=FactSource.SCHEMA_ANALYSIS,
            confidence=0.7,
        )
        await ci_store.save_semantic_fact(correction)
        await ci_store.save_semantic_fact(relevant_schema)

        service = CodeKnowledgeService(ci_store)
        facts = await service.get_context_for_question("active user")
        assert facts[0]["source"] == "user_correction"


class TestFTS5QueryBuilder:
    def test_basic_query(self):
        result = build_fts5_query("what does onboarded mean?")
        assert "onboarded" in result
        assert "mean" in result

    def test_stop_words_removed(self):
        result = build_fts5_query("what is the definition of active")
        assert "what" not in result.split(" OR ")
        assert "active" in result

    def test_empty_question(self):
        result = build_fts5_query("")
        assert result == ""

    def test_only_stop_words(self):
        result = build_fts5_query("is the a")
        assert len(result) > 0


class TestBusinessContextTableStillWorks:
    async def test_set_and_get_business_context(self, ci_store: Store):
        await ci_store.set_business_context("app_type", "SaaS")
        result = await ci_store.get_business_context("app_type")
        assert result == "SaaS"

    async def test_get_all_business_context(self, ci_store: Store):
        await ci_store.set_business_context("key1", "val1")
        await ci_store.set_business_context("key2", "val2")
        all_ctx = await ci_store.get_all_business_context()
        assert "key1" in all_ctx
        assert "key2" in all_ctx


class TestCodeIntelligenceMeta:
    async def test_set_and_get_meta(self, ci_store: Store):
        await ci_store.set_code_intelligence_meta("last_indexed_commit", "abc123")
        result = await ci_store.get_code_intelligence_meta("last_indexed_commit")
        assert result == "abc123"

    async def test_get_nonexistent_meta(self, ci_store: Store):
        result = await ci_store.get_code_intelligence_meta("nonexistent")
        assert result is None
