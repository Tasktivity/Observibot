"""Measure actual planning prompt section sizes for a single query.

Usage: python scripts/measure_prompt_sizes.py "How many users?"
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from observibot.agent.schema_catalog import (  # noqa: E402
    build_app_schema_description,
    build_observability_schema_description,
)
from observibot.core.code_intelligence.service import CodeKnowledgeService  # noqa: E402
from observibot.core.config import load_config  # noqa: E402
from observibot.core.store import Store  # noqa: E402


async def measure(question: str) -> None:
    cfg = load_config()
    store = Store(cfg.store.path)
    await store.connect()

    obs_schema = build_observability_schema_description()

    system_model = await store.get_latest_system_snapshot()
    app_desc = build_app_schema_description(system_model)
    app_section = (
        "2. query_application(sql) — Query the monitored application's "
        "production database (read-only). Use for app-specific data.\n"
        f"   Available tables:\n{app_desc}"
    )

    knowledge_service = CodeKnowledgeService(store)
    business_context_section = ""
    should_inject = await knowledge_service.should_inject_context(question)
    all_facts = await store.get_semantic_facts(active_only=True)

    if should_inject:
        facts = await knowledge_service.get_context_for_question(question)
        if facts:
            business_context_section = (
                await knowledge_service.format_context_for_prompt(facts)
            )

    # Template size (everything except the variable sections).
    from observibot.agent.chat_agent import PLANNING_PROMPT
    template_only = PLANNING_PROMPT.format(
        obs_schema="",
        app_tool_section="",
        business_context_section="",
        question="",
    )

    assembled = PLANNING_PROMPT.format(
        obs_schema=obs_schema,
        app_tool_section=app_section,
        business_context_section=business_context_section,
        question=question,
    )

    def _report(label: str, text: str) -> None:
        chars = len(text)
        tokens = chars // 4
        print(f"  {label:35s} {chars:>10,} chars   ~{tokens:>10,} tokens")

    print(f"\nQuery: {question!r}")
    print(f"  semantic_facts_total_in_store       {len(all_facts):>10,}")
    print(f"  should_inject_context               {should_inject}")
    print()
    print("Section sizes:")
    _report("A. obs_schema", obs_schema)
    _report("B. app_section", app_section)
    _report("   (app_schema only — inside B)", app_desc)
    _report("C. business_context_section", business_context_section)
    _report("E. question", question)
    _report("PLANNING_PROMPT template", template_only)
    print()
    _report("FULL planning_prompt", assembled)

    await store.close()


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "How many users?"
    asyncio.run(measure(q))
