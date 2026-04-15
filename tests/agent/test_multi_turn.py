"""Tests for structured session turns and multi-turn resolution helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from observibot.agent.chat_agent import (
    MULTI_TURN_INSTRUCTIONS,
    ChatResult,
    _build_session_context,
    _extract_entities,
    _extract_metric_from_sql,
    _extract_timeframe_from_sql,
    _smart_truncate,
    run_chat_agent,
)


# ---------- _smart_truncate ----------


def test_smart_truncate_short_text_unchanged():
    assert _smart_truncate("hello", 100) == "hello"


def test_smart_truncate_cuts_at_word_boundary():
    text = "the quick brown fox jumps over the lazy dog"
    out = _smart_truncate(text, 20)
    # Should end at a space-boundary, not mid-word.
    assert out.endswith("...")
    assert not out.startswith("the quick brown fox j")  # no mid-word cut
    assert " " in out[:-3]


def test_smart_truncate_hard_cut_when_no_space_nearby():
    # Long token with no space until after the max — fall back to raw cut.
    out = _smart_truncate("aaaaaaaaaaaaaaaaaaaaa" + " end", 10)
    assert out.endswith("...")
    assert len(out) <= 15


def test_smart_truncate_empty_string():
    assert _smart_truncate("", 50) == ""


# ---------- metric/timeframe regex extractors ----------


def test_extract_metric_from_sql_equals():
    sql = "SELECT * FROM metric_snapshots WHERE metric_name = 'node_cpu_seconds_total'"
    assert _extract_metric_from_sql(sql) == "node_cpu_seconds_total"


def test_extract_metric_from_sql_in_clause():
    sql = "SELECT * FROM metric_snapshots WHERE metric_name IN ('active_connections', 'cache_hit_ratio')"
    assert _extract_metric_from_sql(sql) == "active_connections"


def test_extract_metric_from_sql_none():
    assert _extract_metric_from_sql("SELECT 1") is None
    assert _extract_metric_from_sql("") is None


def test_extract_timeframe_interval_days():
    sql = "SELECT * FROM x WHERE collected_at > now() - interval '7 days'"
    assert _extract_timeframe_from_sql(sql) == "last 7 days"


def test_extract_timeframe_interval_hours():
    sql = "WHERE created_at > now() - interval '24 hours'"
    assert _extract_timeframe_from_sql(sql) == "last 24 hours"


def test_extract_timeframe_current_date():
    assert _extract_timeframe_from_sql("WHERE dt = CURRENT_DATE") == "today"


def test_extract_timeframe_none():
    assert _extract_timeframe_from_sql("SELECT 1") is None


# ---------- _extract_entities ----------


def test_extract_entities_populates_table_from_sql():
    result = ChatResult(
        answer="47",
        sql_queries=["SELECT count(*) FROM users"],
        domains_hit=["application"],
    )
    entities = _extract_entities("How many users?", result)
    assert entities["domain"] == "application"
    assert entities["table"] == "users"
    assert entities["all_tables"] == ["users"]


def test_extract_entities_handles_multiple_tables():
    result = ChatResult(
        answer="joined",
        sql_queries=[
            "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id"
        ],
        domains_hit=["application"],
    )
    entities = _extract_entities("orders by user", result)
    assert entities["table"] == "users"
    assert set(entities["all_tables"]) == {"users", "orders"}


def test_extract_entities_extracts_metric_and_timeframe():
    result = ChatResult(
        answer="OK",
        sql_queries=[
            "SELECT value FROM metric_snapshots "
            "WHERE metric_name = 'cache_hit_ratio' "
            "AND collected_at > now() - interval '7 days'"
        ],
        domains_hit=["observability"],
    )
    entities = _extract_entities("cache trends", result)
    assert entities["metric"] == "cache_hit_ratio"
    assert entities["timeframe"] == "last 7 days"
    assert entities["table"] == "metric_snapshots"


def test_extract_entities_tolerates_missing_sql():
    result = ChatResult(answer="no sql", sql_queries=[], domains_hit=[])
    entities = _extract_entities("hi", result)
    assert entities["domain"] is None
    assert entities["table"] is None
    assert entities["metric"] is None


def test_extract_entities_tolerates_parse_failure():
    result = ChatResult(
        answer="broken",
        sql_queries=["THIS IS NOT SQL ::: {{"],
        domains_hit=["observability"],
    )
    entities = _extract_entities("bad", result)
    assert entities["domain"] == "observability"
    # Parse failure should NOT raise; table just stays None.
    assert entities["table"] is None


def test_extract_entities_captures_widget_type():
    result = ChatResult(
        answer="chart",
        sql_queries=["SELECT * FROM insights"],
        domains_hit=["observability"],
        widget_plan={"widget_type": "kpi_number", "data": []},
    )
    entities = _extract_entities("kpi", result)
    assert entities["widget_type"] == "kpi_number"


# ---------- _build_session_context ----------


def _turn(question: str, entities: dict, sql: str = "", answer: str = "OK") -> dict:
    return {
        "question_summary": question,
        "answer_summary": answer,
        "entities": entities,
        "sql": sql,
        "domain": entities.get("domain"),
    }


def test_build_session_context_empty_returns_empty_string():
    assert _build_session_context([]) == ""
    assert _build_session_context(None or []) == ""


def test_build_session_context_includes_state_block():
    turns = [
        _turn(
            "How many users?",
            {"table": "users", "domain": "application"},
            sql="SELECT count(*) FROM users",
            answer="47 users",
        ),
    ]
    ctx = _build_session_context(turns)
    assert "Conversation Context" in ctx
    assert "Current conversation state" in ctx
    assert "Last table: users" in ctx
    assert "Last domain: application" in ctx
    assert "SELECT count(*) FROM users" in ctx


def test_build_session_context_includes_metric_and_timeframe():
    turns = [
        _turn(
            "cache metrics",
            {
                "table": "metric_snapshots",
                "domain": "observability",
                "metric": "cache_hit_ratio",
                "timeframe": "last 7 days",
            },
            sql="SELECT value FROM metric_snapshots WHERE metric_name = 'cache_hit_ratio'",
        ),
    ]
    ctx = _build_session_context(turns)
    assert "cache_hit_ratio" in ctx
    assert "last 7 days" in ctx
    assert "Last metric: cache_hit_ratio" in ctx


def test_build_session_context_drops_oldest_under_budget():
    # With a tight 50-token budget, only the most recent turn should fit.
    long_sql = "SELECT " + ", ".join([f"col{i}" for i in range(30)]) + " FROM t"
    turns = [
        _turn(f"old-q-{i}", {"table": "t", "domain": "observability"}, sql=long_sql)
        for i in range(5)
    ] + [_turn("newest", {"table": "u", "domain": "application"}, sql="SELECT 1")]
    ctx = _build_session_context(turns, max_tokens=20)
    assert "newest" in ctx
    # State block reflects the newest turn.
    assert "Last table: u" in ctx
    # At least some of the older turns should have been dropped.
    assert ctx.count("Turn ") <= 2


def test_build_session_context_stays_within_token_budget():
    """With 5 realistic turns, total prompt section stays well under 1000 tokens."""
    turns = [
        _turn(
            f"question {i}: how many users signed up last {i+1} days",
            {
                "table": "users",
                "domain": "application",
                "metric": None,
                "timeframe": f"last {i+1} days",
            },
            sql=f"SELECT count(*) FROM users WHERE created_at > now() - interval '{i+1} days'",
            answer=f"answer {i}",
        )
        for i in range(5)
    ]
    ctx = _build_session_context(turns, max_tokens=1000)
    # Rough token estimate is len // 4 per the agent's helper.
    assert len(ctx) // 4 <= 1200  # small margin for headers


# ---------- Multi-turn instructions wiring ----------


def test_multi_turn_instructions_mentions_references():
    text = " ".join(MULTI_TURN_INSTRUCTIONS.lower().split())
    assert "that" in text
    assert "group by" in text
    assert "current conversation" in text  # state block reference


class _FakeProvider:
    """LLM provider double that captures the planning prompt and replays a fixed plan."""

    def __init__(self, plan_response: dict, synth_response: dict | None = None):
        self.plan_response = plan_response
        self.synth_response = synth_response or {
            "narrative": "Here's your answer.",
            "domains": ["application"],
        }
        self.captured_plan_prompt: str | None = None
        self.calls = 0

    async def analyze(self, system_prompt: str, user_prompt: str):
        self.calls += 1
        if self.calls == 1:
            self.captured_plan_prompt = user_prompt
            resp = MagicMock()
            resp.data = self.plan_response
            return resp
        resp = MagicMock()
        resp.data = self.synth_response
        return resp


@pytest.mark.asyncio
async def test_planning_prompt_includes_session_context_when_present():
    """Multi-turn instructions + state block appear ONLY when session context exists."""
    plan = {
        "tool_calls": [{
            "name": "query_application",
            "parameters": {"sql": "SELECT count(*) FROM users GROUP BY month"},
        }],
        "reasoning": "breakdown",
    }
    provider = _FakeProvider(plan)

    store = MagicMock()
    store.engine = MagicMock()
    store.engine.url = "sqlite+aiosqlite:///:memory:"
    store.emit_event = AsyncMock()
    store.save_user_correction = AsyncMock()

    # Pre-seed a turn that established the "users" subject.
    prior_turn = {
        "question_summary": "How many users?",
        "answer_summary": "47 users",
        "entities": {
            "table": "users",
            "domain": "application",
            "metric": None,
            "timeframe": None,
        },
        "sql": "SELECT count(*) FROM users",
        "domain": "application",
    }

    app_db = MagicMock()
    app_db.is_connected = False  # so the tool call fails fast — we only care about prompt shape

    await run_chat_agent(
        question="Break that down by signup month",
        provider=provider,
        store=store,
        app_db=app_db,
        system_model=None,
        session_context=[prior_turn],
    )

    prompt = provider.captured_plan_prompt or ""
    assert "Multi-turn resolution" in prompt
    assert "Current conversation state" in prompt
    assert "Last table: users" in prompt
    assert "Last domain: application" in prompt
    assert "Conversation Context" in prompt


@pytest.mark.asyncio
async def test_planning_prompt_omits_session_context_when_absent():
    """Standalone queries should not carry multi-turn instructions."""
    plan = {
        "tool_calls": [{
            "name": "query_observability",
            "parameters": {"sql": "SELECT * FROM insights LIMIT 10"},
        }],
    }
    provider = _FakeProvider(plan)

    store = MagicMock()
    store.engine = MagicMock()
    store.engine.url = "sqlite+aiosqlite:///:memory:"
    store.emit_event = AsyncMock()
    store.save_user_correction = AsyncMock()

    await run_chat_agent(
        question="show recent insights",
        provider=provider,
        store=store,
        app_db=None,
        system_model=None,
        session_context=None,
    )

    prompt = provider.captured_plan_prompt or ""
    assert "Multi-turn resolution" not in prompt
    assert "Current conversation state" not in prompt
    assert "Conversation Context" not in prompt


def test_single_exchange_recorded_per_turn():
    """The chat route now stores one record per exchange, not two."""
    from observibot.api.routes.chat import _record_turn
    from observibot.api.schemas import ChatResponse
    from observibot.api.session_store import SessionStore

    store = SessionStore()
    session = store.create_session("user-1")

    resp = ChatResponse(
        answer="There are 47 users.",
        sql_query="SELECT count(*) FROM users",
        domains_hit=["application"],
        session_id=session.session_id,
    )
    result = ChatResult(
        answer="There are 47 users.",
        sql_queries=["SELECT count(*) FROM users"],
        domains_hit=["application"],
    )
    _record_turn(store, session.session_id, "How many users?", resp, result)

    turns = store.get_context(session.session_id)
    assert len(turns) == 1  # single exchange, not user+assistant
    assert turns[0]["question_summary"].startswith("How many users?")
    assert turns[0]["answer_summary"].startswith("There are 47 users")
    assert turns[0]["entities"]["table"] == "users"
    assert turns[0]["entities"]["domain"] == "application"
    assert turns[0]["sql"] == "SELECT count(*) FROM users"
