from __future__ import annotations

import json

import pytest

from observibot.agent.llm_provider import (
    BudgetExceededError,
    LLMHardError,
    LLMSoftError,
    MockProvider,
    _classify_provider_error,
    build_provider,
    parse_json_response,
)


async def test_mock_provider_returns_json() -> None:
    p = MockProvider()
    resp = await p.analyze("system", "user")
    assert isinstance(resp.data, dict)
    assert "app_type" in resp.data or "insights" in resp.data
    assert p.tokens_used > 0
    assert p.call_count == 1


async def test_mock_provider_anomaly_path() -> None:
    p = MockProvider()
    resp = await p.analyze(
        system_prompt="You are Observibot. Output only JSON.",
        user_prompt="Detected anomalies:\n- CRITICAL table_inserts(table=tasks) z=42",
    )
    assert "insights" in resp.data


async def test_mock_provider_question_path() -> None:
    p = MockProvider()
    resp = await p.analyze(
        system_prompt="You are Observibot. Output only JSON.",
        user_prompt="User question:\nHow many users do I have?",
    )
    assert "answer" in resp.data


async def test_mock_provider_budget_exceeded() -> None:
    p = MockProvider(daily_token_budget=5)
    # Consume budget
    await p.analyze("s", "u")
    with pytest.raises(BudgetExceededError):
        await p.analyze("s", "u")


async def test_mock_provider_with_canned_response() -> None:
    p = MockProvider(canned={"insights": [{"title": "x", "severity": "info", "summary": "y"}]})
    resp = await p.analyze("s", "u")
    assert resp.data["insights"][0]["title"] == "x"


def test_parse_json_fenced() -> None:
    text = "Here is the json:\n```json\n{\"a\": 1}\n```"
    assert parse_json_response(text) == {"a": 1}


def test_parse_json_plain() -> None:
    assert parse_json_response('{"b": 2}') == {"b": 2}


def test_parse_json_with_surrounding_noise() -> None:
    text = "prose prose {\"c\": 3} more prose"
    assert parse_json_response(text) == {"c": 3}


def test_parse_json_empty_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json_response("")


def test_parse_json_invalid_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json_response("no json here")


def test_build_provider_mock() -> None:
    p = build_provider("mock", "m", None)
    assert isinstance(p, MockProvider)


def test_build_provider_unknown_raises() -> None:
    with pytest.raises(LLMHardError):
        build_provider("fakeprovider", "m", None)


def test_build_provider_anthropic_missing_key_raises() -> None:
    with pytest.raises(LLMHardError):
        build_provider("anthropic", "claude", None)


def test_build_provider_openai_missing_key_raises() -> None:
    with pytest.raises(LLMHardError):
        build_provider("openai", "gpt-4o", None)


def test_classify_hard_vs_soft_errors() -> None:
    hard = _classify_provider_error(RuntimeError("401 Unauthorized"), "Anthropic")
    soft = _classify_provider_error(RuntimeError("Connection reset"), "Anthropic")
    quota = _classify_provider_error(RuntimeError("quota exceeded"), "OpenAI")
    assert isinstance(hard, LLMHardError)
    assert isinstance(soft, LLMSoftError)
    assert isinstance(quota, LLMHardError)


def test_classify_prompt_too_long_is_hard() -> None:
    """HTTP 400 'prompt is too long' must never retry — same payload = same 400."""
    exc = RuntimeError("Error code: 400 - prompt is too long: 204464 tokens > 200000 maximum")
    result = _classify_provider_error(exc, "Anthropic")
    assert isinstance(result, LLMHardError), "prompt-too-long must be hard"


def test_classify_context_length_exceeded_is_hard() -> None:
    exc = RuntimeError("Error 400: context_length_exceeded")
    result = _classify_provider_error(exc, "OpenAI")
    assert isinstance(result, LLMHardError)


def test_classify_invalid_request_error_is_hard() -> None:
    exc = RuntimeError("BadRequestError: invalid_request_error: missing required field")
    result = _classify_provider_error(exc, "Anthropic")
    assert isinstance(result, LLMHardError)


def test_classify_400_with_token_is_hard() -> None:
    exc = RuntimeError("Error code: 400 — too many tokens in request")
    result = _classify_provider_error(exc, "OpenAI")
    assert isinstance(result, LLMHardError)


def test_classify_rate_limit_is_soft() -> None:
    """HTTP 429 should be retried — rate limits self-heal."""
    exc = RuntimeError("Error code: 429 — rate_limit_exceeded")
    result = _classify_provider_error(exc, "Anthropic")
    assert isinstance(result, LLMSoftError), "rate limits must be retried"


def test_classify_server_error_is_soft() -> None:
    """HTTP 500 should be retried — transient server errors self-heal."""
    exc = RuntimeError("Error code: 500 — internal_server_error")
    result = _classify_provider_error(exc, "Anthropic")
    assert isinstance(result, LLMSoftError)


def test_classify_timeout_is_soft() -> None:
    exc = TimeoutError("Connection timed out after 30s")
    result = _classify_provider_error(exc, "OpenAI")
    assert isinstance(result, LLMSoftError)


class _PromptTooLongProvider(MockProvider):
    """Provider that raises a permanent prompt-too-long error on every call."""

    call_attempts = 0

    async def _call(self, system_prompt: str, user_prompt: str):
        type(self).call_attempts += 1
        raise LLMHardError(
            "Anthropic hard error: 400 — prompt is too long: 204464 tokens"
        )


@pytest.mark.asyncio
async def test_prompt_too_long_not_retried() -> None:
    """Hard failure from prompt overflow must abort immediately — no retry storm."""
    _PromptTooLongProvider.call_attempts = 0
    p = _PromptTooLongProvider()
    with pytest.raises(LLMHardError) as exc_info:
        await p.analyze("s", "u")
    assert "prompt is too long" in str(exc_info.value).lower()
    # Only one attempt — not three.
    assert _PromptTooLongProvider.call_attempts == 1


class _SoftFailProvider(MockProvider):
    """Provider that raises a transient soft error on every call."""

    call_attempts = 0

    async def _call(self, system_prompt: str, user_prompt: str):
        type(self).call_attempts += 1
        raise LLMSoftError("Anthropic soft error: 500 internal server error")


@pytest.mark.asyncio
async def test_soft_error_is_retried_three_times() -> None:
    """Transient server errors get the full 3-attempt retry."""
    _SoftFailProvider.call_attempts = 0
    p = _SoftFailProvider()
    with pytest.raises(LLMSoftError):
        await p.analyze("s", "u")
    assert _SoftFailProvider.call_attempts == 3


class _BadProvider(MockProvider):
    async def _call(self, system_prompt: str, user_prompt: str):
        return "not valid json at all", 1, 1


@pytest.mark.asyncio
async def test_retries_then_fails_on_bad_json() -> None:
    p = _BadProvider()
    with pytest.raises(LLMSoftError):
        await p.analyze("s", "u")


class _HardFailProvider(MockProvider):
    async def _call(self, system_prompt: str, user_prompt: str):
        raise LLMHardError("401 unauthorized")


@pytest.mark.asyncio
async def test_hard_error_is_not_retried() -> None:
    p = _HardFailProvider()
    with pytest.raises(LLMHardError):
        await p.analyze("s", "u")
    # Only one attempt, not three.
    assert p.call_count == 0  # _record_usage only runs on success
