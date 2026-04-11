"""Semantic modeler — interactive onboarding that builds business context."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from observibot.agent.analyzer import Analyzer, summarize_system
from observibot.agent.schemas import LLMSystemAnalysis
from observibot.core.models import SystemModel
from observibot.core.store import Store

log = logging.getLogger(__name__)


class SemanticModeler:
    """Interpret a SystemModel via the LLM and persist the business context.

    The modeler accepts an optional ``prompter`` callable so the CLI can
    walk the user through an interactive confirmation step; tests substitute
    a pass-through prompter.
    """

    def __init__(self, analyzer: Analyzer, store: Store) -> None:
        self.analyzer = analyzer
        self.store = store

    async def run(
        self,
        system_model: SystemModel,
        prompter: Callable[[LLMSystemAnalysis, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run the modeler and persist a business-context dict.

        Args:
            system_model: The model to analyze.
            prompter: Optional callback receiving the LLM suggestion and the
                human-readable system summary, and returning the confirmed
                business-context dict. Defaults to accepting the suggestion
                unchanged.

        Returns:
            The business-context dict that was written to the store.
        """
        suggestion = await self.analyzer.analyze_system(system_model)
        log.info("Semantic modeler LLM suggestion: %s", suggestion.model_dump())

        if prompter is None:
            confirmed: dict[str, Any] = suggestion.model_dump()
        else:
            confirmed = prompter(suggestion, summarize_system(system_model))

        await self.store.set_business_context("app_type", confirmed.get("app_type"))
        await self.store.set_business_context(
            "critical_tables", confirmed.get("critical_tables") or []
        )
        await self.store.set_business_context(
            "key_metrics", confirmed.get("key_metrics") or []
        )
        await self.store.set_business_context(
            "summary", confirmed.get("summary") or confirmed.get("app_description") or ""
        )
        await self.store.set_business_context(
            "risks", confirmed.get("risks") or []
        )
        return confirmed
