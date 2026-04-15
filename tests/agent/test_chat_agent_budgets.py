"""Budget enforcement and prompt-size logging for chat_agent."""
from __future__ import annotations

import logging

import pytest

from observibot.agent.chat_agent import (
    APP_SCHEMA_BUDGET_TOKENS,
    BUSINESS_CONTEXT_BUDGET_TOKENS,
    OBS_SCHEMA_BUDGET_TOKENS,
    PROMPT_ERROR_TOKENS,
    PROMPT_WARN_TOKENS,
    _enforce_budget,
    _estimate_tokens,
    _log_prompt_size,
)


def test_enforce_budget_keeps_short_text_unchanged() -> None:
    text = "hello world"
    assert _enforce_budget(text, max_tokens=100, label="test") == text


def test_enforce_budget_truncates_long_text() -> None:
    # 1000 chars = ~250 tokens; budget 10 tokens = ~40 chars.
    text = "A" * 1000
    out = _enforce_budget(text, max_tokens=10, label="test")
    assert len(out) <= 100  # truncated + the note
    assert "[Truncated:" in out
    assert "'test'" in out
    assert "10-token budget" in out


def test_enforce_budget_respects_line_boundaries() -> None:
    # Multiple newlines in final window — truncation should snap to nearest.
    text = "\n".join(f"line-{i:03d}" for i in range(500))  # ~4500 chars
    out = _enforce_budget(text, max_tokens=200, label="lines")  # ~800 chars
    # Should not end mid-word — the final content line (before the note)
    # should terminate on a line-N boundary.
    body = out.split("\n[Truncated:")[0]
    final_line = body.rstrip("\n").split("\n")[-1]
    # Accept either a clean "line-NNN" or an empty trailing line.
    assert final_line == "" or final_line.startswith("line-")


def test_enforce_budget_adds_visible_note_not_silent() -> None:
    """Truncation must be visible — silent data loss is a bug, not a feature."""
    text = "payload " * 2000
    out = _enforce_budget(text, max_tokens=50, label="payload")
    assert "[Truncated:" in out, "truncation must emit a visible note"
    assert "payload" in out.split("[Truncated:")[1].lower()


def test_enforce_budget_zero_budget() -> None:
    # Edge case: zero budget should not crash.
    out = _enforce_budget("some text here", max_tokens=0, label="zero")
    assert "[Truncated:" in out


def test_estimate_tokens_rough_4_chars_per_token() -> None:
    assert _estimate_tokens("") == 0
    assert _estimate_tokens("abcd") == 1
    assert _estimate_tokens("a" * 100) == 25


def test_log_prompt_size_debug_for_small_prompt(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="observibot.agent.chat_agent")
    _log_prompt_size("x" * 100, "Planning", {"a": "hi", "b": "there"})
    planning_records = [r for r in caplog.records if "Planning prompt" in r.message]
    assert planning_records, "expected Planning prompt log line"
    assert planning_records[0].levelno == logging.DEBUG


def test_log_prompt_size_warning_over_30k(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="observibot.agent.chat_agent")
    # ~35k tokens = ~140k chars.
    big = "x" * (PROMPT_WARN_TOKENS * 4 + 10_000)
    _log_prompt_size(big, "Planning", {"giant": big})
    records = [r for r in caplog.records if "Planning prompt" in r.message]
    assert records and records[-1].levelno == logging.WARNING


def test_log_prompt_size_error_over_150k(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="observibot.agent.chat_agent")
    huge = "x" * (PROMPT_ERROR_TOKENS * 4 + 10_000)
    _log_prompt_size(huge, "Planning", {"massive": huge})
    records = [r for r in caplog.records if "Planning prompt" in r.message]
    assert records and records[-1].levelno == logging.ERROR


def test_log_prompt_size_includes_section_breakdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="observibot.agent.chat_agent")
    _log_prompt_size(
        "full prompt text",
        "Planning",
        {"obs_schema": "x" * 400, "app_section": "y" * 800, "question": "q"},
    )
    records = [r for r in caplog.records if "Planning prompt" in r.message]
    msg = records[-1].message
    assert "obs_schema=" in msg
    assert "app_section=" in msg
    assert "question=" in msg


def test_budget_constants_are_reasonable() -> None:
    """Sanity check: total per-section budgets fit comfortably under 200k."""
    total = (
        OBS_SCHEMA_BUDGET_TOKENS
        + APP_SCHEMA_BUDGET_TOKENS
        + BUSINESS_CONTEXT_BUDGET_TOKENS
    )
    assert total < 20_000, "per-section budgets should stay well under model limits"


def test_log_prompt_size_handles_empty_sections(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty section values must be omitted, not rendered as '=0tok'."""
    caplog.set_level(logging.DEBUG, logger="observibot.agent.chat_agent")
    _log_prompt_size(
        "prompt",
        "Planning",
        {"populated": "hello", "empty": "", "question": "q"},
    )
    records = [r for r in caplog.records if "Planning prompt" in r.message]
    msg = records[-1].message
    assert "empty=" not in msg
    assert "populated=" in msg


# Pipeline-audit Fix 5: row-type-aware sampling


def test_sample_rows_returns_all_when_under_max() -> None:
    from observibot.agent.chat_agent import _sample_rows

    rows = [{"id": i} for i in range(10)]
    sample, desc = _sample_rows(rows)
    assert sample == rows
    assert "complete" in desc


def test_sample_rows_time_series_includes_first_last_middle() -> None:
    from observibot.agent.chat_agent import _sample_rows

    rows = [{"bucket_at": f"2026-01-{i:02d}", "value": i} for i in range(1, 101)]
    sample, desc = _sample_rows(rows)

    assert "time-series sample" in desc
    bucket_dates = [r["bucket_at"] for r in sample]
    # First-10
    assert "2026-01-01" in bucket_dates
    assert "2026-01-10" in bucket_dates
    # Last-10
    assert "2026-01-91" in bucket_dates
    assert "2026-01-100" in bucket_dates
    # At least one middle-range row (between days 30 and 70 inclusive)
    middle = [
        d for d in bucket_dates
        if 30 <= int(d.split("-")[-1]) <= 70
    ]
    assert middle, f"expected at least one middle-range sample, got {bucket_dates}"


def test_sample_rows_numeric_includes_summary_stats() -> None:
    from observibot.agent.chat_agent import _sample_rows

    rows = [{"category": f"cat_{i % 5}", "value": i * 1.5} for i in range(200)]
    sample, desc = _sample_rows(rows)
    assert "Stats" in desc
    assert "min" in desc
    assert "max" in desc
    assert "value" in desc
    assert len(sample) == 20


def test_sample_rows_default_returns_head_and_tail() -> None:
    from observibot.agent.chat_agent import _sample_rows

    rows = [{"category": f"cat_{i}"} for i in range(100)]
    sample, desc = _sample_rows(rows)
    assert "first 15 + last 5" in desc
    cats = [r["category"] for r in sample]
    assert "cat_0" in cats
    assert "cat_14" in cats
    assert "cat_99" in cats


def test_sample_rows_handles_empty_list() -> None:
    from observibot.agent.chat_agent import _sample_rows

    sample, desc = _sample_rows([])
    assert sample == []
    assert "complete" in desc


def test_format_tool_results_uses_smart_sampling() -> None:
    from observibot.agent.chat_agent import ToolResult, _format_tool_results

    rows = [{"day": f"2026-01-{i:02d}", "v": i} for i in range(1, 101)]
    result = ToolResult(domain="application", success=True, rows=rows, sql="SELECT *")
    out = _format_tool_results([result])
    # Description should mention time-series, not the old "100 rows. Sample:" string
    assert "time-series sample" in out
    assert "2026-01-100" in out


# Pipeline-audit Fix 6: metric_baselines is empty today and was producing
# "no baselines" narratives — leave it out of the planner allowlist until the
# seasonal-baseline work in Step 3 actually populates it.


def test_metric_baselines_not_in_planner_allowlists() -> None:
    from observibot.agent.chat_agent import OBSERVABILITY_TABLES
    from observibot.agent.schema_catalog import build_observability_schema_description

    assert "metric_baselines" not in OBSERVABILITY_TABLES
    desc = build_observability_schema_description()
    assert "metric_baselines" not in desc
