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
