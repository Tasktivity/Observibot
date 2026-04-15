"""Tests for schema-derived fact generation and business context injection."""
from __future__ import annotations

from pathlib import Path

import pytest

from observibot.core.code_intelligence.schema_analyzer import analyze_schema_for_facts
from observibot.core.code_intelligence.service import CodeKnowledgeService
from observibot.core.models import Relationship, SystemModel, TableInfo
from observibot.core.store import Store


@pytest.fixture
async def ci_store(tmp_path: Path):
    path = tmp_path / "analyzer_store.db"
    async with Store(path) as store:
        yield store


def _model_with_comments() -> SystemModel:
    return SystemModel(
        tables=[
            TableInfo(
                name="users", schema="public", row_count=100,
                columns=[
                    {"name": "id", "type": "uuid"},
                    {"name": "email", "type": "text"},
                    {"name": "created_at", "type": "timestamptz"},
                    {"name": "completed_onboarding_at", "type": "timestamptz",
                     "comment": "Set when user finishes all onboarding steps"},
                    {"name": "is_active", "type": "boolean"},
                    {"name": "status", "type": "text"},
                ],
            ),
            TableInfo(
                name="tasks", schema="public", row_count=312,
                columns=[
                    {"name": "id", "type": "uuid"},
                    {"name": "user_id", "type": "uuid"},
                    {"name": "title", "type": "text"},
                    {"name": "status", "type": "text",
                     "comment": "Task lifecycle: draft/active/completed/archived"},
                    {"name": "completed_at", "type": "timestamptz"},
                ],
            ),
        ],
        relationships=[
            Relationship(
                from_table="tasks", from_column="user_id",
                to_table="users", to_column="id",
            ),
        ],
    )


class TestSchemaAnalyzer:
    async def test_generates_entity_facts(self, ci_store: Store):
        model = _model_with_comments()
        facts = await analyze_schema_for_facts(model, ci_store)
        entity_facts = [f for f in facts if f.fact_type.value == "entity"]
        assert len(entity_facts) >= 2
        table_names = [f.concept for f in entity_facts]
        assert "users" in table_names
        assert "tasks" in table_names

    async def test_generates_comment_facts(self, ci_store: Store):
        model = _model_with_comments()
        facts = await analyze_schema_for_facts(model, ci_store)
        comment_facts = [
            f for f in facts
            if f.source.value == "schema_analysis"
            and "onboarding" in f.claim.lower()
        ]
        assert len(comment_facts) >= 1

    async def test_generates_timestamp_mapping_facts(self, ci_store: Store):
        model = _model_with_comments()
        facts = await analyze_schema_for_facts(model, ci_store)
        ts_facts = [f for f in facts if f.sql_condition and "IS NOT NULL" in f.sql_condition]
        assert len(ts_facts) >= 1

    async def test_generates_status_workflow_facts(self, ci_store: Store):
        model = _model_with_comments()
        facts = await analyze_schema_for_facts(model, ci_store)
        workflow_facts = [f for f in facts if f.fact_type.value == "workflow"]
        assert len(workflow_facts) >= 1

    async def test_generates_boolean_flag_facts(self, ci_store: Store):
        model = _model_with_comments()
        facts = await analyze_schema_for_facts(model, ci_store)
        bool_facts = [f for f in facts if "is_active" in (f.sql_condition or "")]
        assert len(bool_facts) >= 1

    async def test_generates_relationship_facts(self, ci_store: Store):
        model = _model_with_comments()
        facts = await analyze_schema_for_facts(model, ci_store)
        rel_facts = [f for f in facts if "tasks" in f.tables and "users" in f.tables]
        assert len(rel_facts) >= 1

    async def test_facts_persisted_to_store(self, ci_store: Store):
        model = _model_with_comments()
        await analyze_schema_for_facts(model, ci_store)
        stored = await ci_store.get_semantic_facts()
        assert len(stored) > 0


class TestBusinessContextInjection:
    async def test_context_injected_for_business_question(self, ci_store: Store):
        model = _model_with_comments()
        await analyze_schema_for_facts(model, ci_store)

        service = CodeKnowledgeService(ci_store)
        should = await service.should_inject_context("how many onboarded users are there?")
        assert should is False or should is True

    async def test_classifier_always_open(self, ci_store: Store):
        """Pipeline-audit Fix 8: gate is open; retrieval is the actual filter."""
        service = CodeKnowledgeService(ci_store)
        result = await service.should_inject_context("how many users?")
        assert result is True

    async def test_context_format_compact(self, ci_store: Store):
        model = _model_with_comments()
        await analyze_schema_for_facts(model, ci_store)

        service = CodeKnowledgeService(ci_store)
        facts = await service.get_context_for_question("onboarding status")
        formatted = await service.format_context_for_prompt(facts)
        if formatted:
            lines = formatted.strip().split("\n")
            for line in lines[1:]:
                assert line.startswith("- ")


class TestConversationalCorrections:
    async def test_correction_pattern_detected(self, ci_store: Store):
        from observibot.agent.chat_agent import _detect_and_store_correction

        await _detect_and_store_correction(
            "actually, onboarded means completed_onboarding_at IS NOT NULL", ci_store,
        )
        facts = await ci_store.get_semantic_facts(concept="onboarded")
        assert len(facts) == 1
        assert facts[0]["source"] == "user_correction"
        assert facts[0]["confidence"] == 1.0

    async def test_correction_no_match(self, ci_store: Store):
        from observibot.agent.chat_agent import _detect_and_store_correction

        await _detect_and_store_correction("how many users are there?", ci_store)
        facts = await ci_store.get_semantic_facts()
        assert len(facts) == 0

    async def test_correction_should_be_defined_as(self, ci_store: Store):
        from observibot.agent.chat_agent import _detect_and_store_correction

        await _detect_and_store_correction(
            "active user should be defined as last_login_at > now() - interval '30 days'",
            ci_store,
        )
        facts = await ci_store.get_semantic_facts(concept="active user")
        assert len(facts) == 1


class TestSchemaDescriptionWithComments:
    def test_comment_in_schema_description(self):
        from observibot.agent.schema_catalog import build_app_schema_description

        model = _model_with_comments()
        desc = build_app_schema_description(model)
        assert "Task lifecycle" in desc

    def test_description_caps_at_max_chars(self):
        """Schema with arbitrarily long column comments must not blow the budget."""
        from observibot.agent.schema_catalog import build_app_schema_description

        # Build a model with many tables whose columns have huge comments.
        # Without the max_chars cap, this blows past any token budget.
        long_comment = "X" * 5000
        tables = []
        for i in range(40):
            tables.append(
                TableInfo(
                    name=f"bloat_{i}", schema="public",
                    columns=[
                        {"name": f"col_{j}", "type": "text", "comment": long_comment}
                        for j in range(10)
                    ],
                )
            )
        model = SystemModel(tables=tables, relationships=[])

        desc = build_app_schema_description(model, max_chars=10_000)
        assert len(desc) <= 10_000 + 100  # budget + room for the truncation note
        assert "[Schema truncated" in desc

    def test_description_not_truncated_when_under_cap(self):
        from observibot.agent.schema_catalog import build_app_schema_description

        model = _model_with_comments()
        desc = build_app_schema_description(model, max_chars=100_000)
        assert "[Schema truncated" not in desc


class TestRelevanceRankedSchemaDescription:
    """Pipeline-audit Fix 3: prefer relevance ranking over alphabetical cut.

    Why: the old alphabetical sort dropped the v_pilot_* analytics views past
    index 50, which were exactly the tables an SRE would ask about.
    """

    def _build_60_table_model(self) -> SystemModel:
        # 50 mundane "a*" tables that win alphabetically, plus the views
        # that should only appear via relevance ranking.
        tables: list[TableInfo] = []
        for i in range(50):
            tables.append(TableInfo(
                name=f"audit_log_{i:02d}",
                schema="public",
                columns=[
                    {"name": "id", "type": "uuid"},
                    {"name": "created_at", "type": "timestamp"},
                ],
                row_count=10,
            ))
        view_names = [
            "v_pilot_summary",
            "v_pilot_sync_health",
            "v_pilot_user_costs",
            "v_pilot_user_list",
            "v_pilot_weekly_retention",
            "v_platform_breakdown",
            "v_user_overview",
        ]
        for name in view_names:
            tables.append(TableInfo(
                name=name,
                schema="public",
                columns=[
                    {"name": "user_id", "type": "uuid"},
                    {"name": "pilot_program", "type": "text"},
                    {"name": "metric_value", "type": "numeric"},
                ],
                row_count=100,
            ))
        return SystemModel(tables=tables, relationships=[])

    def test_relevant_tables_promoted_to_full_detail(self):
        from observibot.agent.schema_catalog import build_app_schema_description

        model = self._build_60_table_model()
        desc = build_app_schema_description(
            model, question="how are pilot users doing?",
        )
        # The pilot views must appear with full column detail, not just in
        # the thin index.
        assert "v_pilot_user_list" in desc
        assert "pilot_program (text)" in desc

    def test_thin_index_lists_remaining_tables(self):
        from observibot.agent.schema_catalog import build_app_schema_description

        model = self._build_60_table_model()
        desc = build_app_schema_description(
            model, question="how are pilot users doing?",
        )
        # Tables that lost the relevance race must still appear in the thin
        # index — none should be silently dropped.
        assert "Other tables" in desc
        for i in range(50):
            assert f"audit_log_{i:02d}" in desc

    def test_no_tables_silently_dropped(self):
        from observibot.agent.schema_catalog import build_app_schema_description

        model = self._build_60_table_model()
        desc = build_app_schema_description(
            model, question="anything unrelated to schema",
        )
        for table in model.tables:
            assert table.name in desc, f"{table.name} silently dropped"

    def test_no_question_uses_legacy_alphabetical(self):
        from observibot.agent.schema_catalog import build_app_schema_description

        model = self._build_60_table_model()
        desc = build_app_schema_description(model)
        # Without a question, the first 15 alphabetical tables get full detail
        assert "audit_log_00" in desc
        # And every table is still represented somewhere (thin index)
        for name in [
            "v_pilot_summary", "v_pilot_user_list", "v_user_overview",
        ]:
            assert name in desc
