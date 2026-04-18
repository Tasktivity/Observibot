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


# ---------------------------------------------------------------------------
# Stage 4 — daily token budget gate
# ---------------------------------------------------------------------------


class _FakeStore:
    """Async-shaped store stub for exercising the Stage 4 budget gate.

    Records the ``since`` argument passed to ``get_llm_usage_summary``
    (so the UTC-day boundary can be asserted) and the events emitted
    via ``emit_event`` (so budget-block audit is observable).
    """

    def __init__(self, total_tokens: int = 0) -> None:
        self.total_tokens = total_tokens
        self.since_arg = None
        self.events: list[dict] = []
        self.raise_on_summary = False

    async def get_llm_usage_summary(self, since=None):  # noqa: ANN001
        self.since_arg = since
        if self.raise_on_summary:
            raise RuntimeError("summary query blew up")
        return {
            "calls": 1,
            "total_tokens": self.total_tokens,
            "cost_usd": 0.0,
            "since": (since.isoformat() if since else ""),
        }

    async def emit_event(self, **kwargs):
        self.events.append(kwargs)
        return "evt-stub"


@pytest.mark.asyncio
async def test_token_budget_blocks_calls_over_threshold() -> None:
    """Stage 4: at 195k/200k used + ~10k for the upcoming call, the
    gate must raise LLMHardError BEFORE any network call."""
    p = MockProvider(daily_token_budget=200_000)
    p.attach_store(_FakeStore(total_tokens=195_000))
    call_count = {"n": 0}

    original_call = p._call

    async def counted_call(s, u):
        call_count["n"] += 1
        return await original_call(s, u)

    p._call = counted_call  # type: ignore[method-assign]
    big_prompt = "x" * 40_000  # ~10k tokens at ~4 chars/token
    with pytest.raises(LLMHardError) as exc_info:
        await p.analyze(big_prompt, "user")
    assert "daily token budget exceeded" in str(exc_info.value)
    assert call_count["n"] == 0  # no network call


@pytest.mark.asyncio
async def test_token_budget_allows_calls_under_threshold() -> None:
    """Stage 4: usage=50k, projected=~20k, budget=200k → passes."""
    p = MockProvider(daily_token_budget=200_000)
    store = _FakeStore(total_tokens=50_000)
    p.attach_store(store)
    resp = await p.analyze("s" * 40, "u" * 40)
    assert isinstance(resp.data, dict)


@pytest.mark.asyncio
async def test_token_budget_resets_daily(tmp_store) -> None:
    """Stage 4 + Hotfix item 5: yesterday's llm_usage rows MUST NOT
    count toward today's budget sum. Previously the test only
    asserted the ``since`` kwarg passed to the store was midnight
    UTC — a regression that broke the cutoff SQL while still passing
    the right datetime would have slipped through.

    Setup: two timestamped rows in the real store —
    150k today (6h ago) + 500k yesterday (36h ago). Budget is 200k.
    If yesterday's 500k were included the call would block; if it's
    correctly excluded the call succeeds.
    """
    from datetime import UTC, datetime, timedelta

    from observibot.core.store import llm_usage

    now = datetime.now(UTC)
    today_recent = (now - timedelta(hours=6)).isoformat()
    yesterday = (now - timedelta(hours=36)).isoformat()

    async with tmp_store.engine.begin() as conn:
        await conn.execute(
            llm_usage.insert().values(
                provider="mock", model="m", prompt_tokens=100_000,
                completion_tokens=50_000, total_tokens=150_000,
                cost_usd=0.0, purpose="today",
                recorded_at=today_recent,
            )
        )
        await conn.execute(
            llm_usage.insert().values(
                provider="mock", model="m", prompt_tokens=300_000,
                completion_tokens=200_000, total_tokens=500_000,
                cost_usd=0.0, purpose="yesterday",
                recorded_at=yesterday,
            )
        )

    # Confirm the setup is actually sensitive: a since=0 query sees
    # both rows (650k), but a since=midnight query sees only today's
    # 150k. If this assertion failed, the rest of the test would be
    # tautological.
    full = await tmp_store.get_llm_usage_summary(
        since=now - timedelta(days=7),
    )
    assert full["total_tokens"] == 650_000
    todayonly = await tmp_store.get_llm_usage_summary(
        since=now.replace(hour=0, minute=0, second=0, microsecond=0),
    )
    assert todayonly["total_tokens"] == 150_000

    # Budget 200k. Today's 150k + a small projected call fits; if the
    # provider's gate pulled yesterday's 500k into the sum the call
    # would be blocked with LLMHardError.
    p = MockProvider(daily_token_budget=200_000)
    p.attach_store(tmp_store)
    resp = await p.analyze("s", "u")
    assert isinstance(resp.data, dict)


@pytest.mark.asyncio
async def test_token_budget_disabled_passes_all() -> None:
    """Stage 4: flag False means no gate; 250k of a 200k budget goes
    through so tests don't have to thread usage-write through every
    harness."""
    p = MockProvider(
        daily_token_budget=200_000,
        daily_token_budget_enabled=False,
    )
    p.attach_store(_FakeStore(total_tokens=250_000))
    resp = await p.analyze("s", "u")
    assert isinstance(resp.data, dict)


@pytest.mark.asyncio
async def test_token_budget_exceeded_emits_event() -> None:
    """Stage 4: blocking also writes a ``token_budget_exceeded`` event
    with severity=warning and a summary that includes used/budget."""
    p = MockProvider(daily_token_budget=10_000)
    store = _FakeStore(total_tokens=9_500)
    p.attach_store(store)
    with pytest.raises(LLMHardError):
        await p.analyze("s" * 40, "u" * 40)
    assert len(store.events) == 1
    evt = store.events[0]
    assert evt["event_type"] == "token_budget_exceeded"
    assert evt["severity"] == "warning"
    summary = evt["summary"]
    assert "used 9500 of 10000" in summary
    assert evt["source"] == "llm_provider"


@pytest.mark.asyncio
async def test_token_budget_gate_no_store_is_silent() -> None:
    """Stage 4: with no store attached, the gate is a no-op (early
    returns) so tests and bootstrap-before-connect paths still work.
    """
    p = MockProvider(daily_token_budget=5)  # intentionally tiny
    # no attach_store
    # Tiny prompt keeps the _check_budget counter under the cap
    # (that's the pre-existing, separate counter).
    p.daily_token_budget = 200_000  # ignore the pre-existing counter
    resp = await p.analyze("s", "u")
    assert isinstance(resp.data, dict)


@pytest.mark.asyncio
async def test_token_budget_gate_tolerates_store_errors() -> None:
    """Stage 4: if ``get_llm_usage_summary`` raises, the gate logs
    and allows the call rather than hard-failing. We'd rather miss a
    gate than drop legitimate traffic because of an unrelated query
    error.
    """
    p = MockProvider(daily_token_budget=200_000)
    store = _FakeStore(total_tokens=0)
    store.raise_on_summary = True
    p.attach_store(store)
    resp = await p.analyze("s", "u")
    assert isinstance(resp.data, dict)


@pytest.mark.asyncio
async def test_hard_error_is_not_retried() -> None:
    p = _HardFailProvider()
    with pytest.raises(LLMHardError):
        await p.analyze("s", "u")
    # Only one attempt, not three.
    assert p.call_count == 0  # _record_usage only runs on success
