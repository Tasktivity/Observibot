from __future__ import annotations

import pytest

from observibot.agent.analyzer import Analyzer
from observibot.agent.llm_provider import MockProvider
from observibot.agent.semantic_modeler import SemanticModeler

pytestmark = pytest.mark.asyncio


async def test_semantic_modeler_persists_business_context(tmp_store, sample_system_model) -> None:
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    modeler = SemanticModeler(analyzer=analyzer, store=tmp_store)
    result = await modeler.run(sample_system_model)
    assert "app_type" in result
    stored = await tmp_store.get_all_business_context()
    assert "app_type" in stored
    assert "critical_tables" in stored


async def test_semantic_modeler_respects_prompter(tmp_store, sample_system_model) -> None:
    analyzer = Analyzer(provider=MockProvider(), store=tmp_store)
    modeler = SemanticModeler(analyzer=analyzer, store=tmp_store)

    def prompter(suggestion, summary):
        data = suggestion.model_dump()
        data["app_type"] = "custom override"
        return data

    result = await modeler.run(sample_system_model, prompter=prompter)
    assert result["app_type"] == "custom override"
    stored = await tmp_store.get_business_context("app_type")
    assert stored == "custom override"
