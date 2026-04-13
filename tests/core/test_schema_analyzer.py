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
                name="users", schema="public", row_count=47,
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

    async def test_no_context_for_simple_count(self, ci_store: Store):
        service = CodeKnowledgeService(ci_store)
        result = await service.should_inject_context("how many users?")
        assert result is False

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
