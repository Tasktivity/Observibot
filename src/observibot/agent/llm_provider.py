"""LLM provider abstraction with Mock, Anthropic, and OpenAI implementations."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from observibot.core.store import Store

log = logging.getLogger(__name__)

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(Exception):
    """Base error for LLM provider failures.

    Attributes:
        kind: ``"soft"`` (bad JSON, transient timeout) or ``"hard"`` (auth,
            quota, persistent API failure). Used by the monitor's circuit
            breaker to pick the right backoff policy.
    """

    kind: str = "soft"

    def __init__(self, message: str, *, kind: str = "soft") -> None:
        super().__init__(message)
        self.kind = kind


class LLMHardError(LLMError):
    """Provider-side failure that will not recover without human action."""

    kind = "hard"

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="hard")


class LLMSoftError(LLMError):
    """Transient provider or content failure; worth retrying later."""

    kind = "soft"

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="soft")


class BudgetExceededError(LLMHardError):
    """Raised when an LLM call would exceed the configured token budget."""


@dataclass
class LLMResponse:
    """Result of a single LLM call."""

    data: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    raw_text: str
    model: str


class LLMProvider(ABC):
    """Abstract LLM provider."""

    name: str = "base"

    def __init__(
        self,
        model: str,
        max_tokens_per_cycle: int = 4000,
        temperature: float = 0.2,
        daily_token_budget: int = 200_000,
        daily_token_budget_enabled: bool = True,
    ) -> None:
        self.model = model
        self.max_tokens_per_cycle = max_tokens_per_cycle
        self.temperature = temperature
        self.daily_token_budget = daily_token_budget
        self.daily_token_budget_enabled = daily_token_budget_enabled
        self._tokens_used = 0
        self._call_count = 0
        # Stage 4: store is attached after construction (factory
        # doesn't own one). When set AND ``daily_token_budget_enabled``
        # is True, ``analyze`` queries cumulative usage for the
        # current UTC day and refuses calls that would exceed the
        # budget. Left None in tests that don't need the gate.
        self.store: Store | None = None

    def attach_store(self, store: Store) -> None:
        """Wire the store used for daily-token-budget accounting.

        Called by the bootstrap path after the store is connected.
        Kept out of ``__init__`` because ``build_provider`` doesn't own
        a store reference and we don't want to force every test
        harness to construct one.
        """
        self.store = store

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def call_count(self) -> int:
        return self._call_count

    def _check_budget(self) -> None:
        if self._tokens_used >= self.daily_token_budget:
            raise BudgetExceededError(
                f"Daily LLM token budget {self.daily_token_budget} exceeded "
                f"(used {self._tokens_used})"
            )

    def _record_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self._tokens_used += prompt_tokens + completion_tokens
        self._call_count += 1

    async def analyze(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Send a system+user prompt and return the parsed response.

        Implements retry-with-backoff and JSON extraction. Subclasses implement
        :meth:`_call`. A :class:`LLMHardError` is re-raised immediately without
        retrying.

        Stage 4: when a store is attached and
        ``daily_token_budget_enabled`` is True, the call is gated on
        cumulative ``llm_usage.total_tokens`` for the current UTC day.
        A call that would push past ``daily_token_budget`` raises
        :class:`LLMHardError` before any network I/O. The circuit
        breaker picks up the hard error and opens with long backoff —
        exactly the desired behavior when we've burned the budget.
        """
        self._check_budget()
        await self._check_daily_token_budget(system_prompt, user_prompt)
        last_exc: Exception | None = None
        last_kind = "soft"
        for attempt in range(3):
            try:
                raw, prompt_tok, completion_tok = await self._call(
                    system_prompt, user_prompt
                )
                self._record_usage(prompt_tok, completion_tok)
                data = parse_json_response(raw)
                return LLMResponse(
                    data=data,
                    prompt_tokens=prompt_tok,
                    completion_tokens=completion_tok,
                    raw_text=raw,
                    model=self.model,
                )
            except LLMHardError:
                # Auth/quota errors will not recover by retrying.
                raise
            except (json.JSONDecodeError, LLMError) as exc:
                last_exc = exc
                last_kind = getattr(exc, "kind", "soft")
                log.warning("LLM attempt %s failed (%s): %s", attempt + 1, last_kind, exc)
                await asyncio.sleep(2**attempt)
            except Exception as exc:
                last_exc = exc
                log.warning("LLM call error attempt %s: %s", attempt + 1, exc)
                await asyncio.sleep(2**attempt)
        assert last_exc is not None
        raise LLMSoftError(f"LLM analyze failed after retries: {last_exc}") from last_exc

    @abstractmethod
    async def _call(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, int, int]:
        """Make a single (non-retried) LLM call.

        Returns:
            ``(raw_text, prompt_tokens, completion_tokens)``
        """

    # ------------------------------------------------------------------
    # Stage 4: daily-token-budget gate
    # ------------------------------------------------------------------

    async def _check_daily_token_budget(
        self, system_prompt: str, user_prompt: str,
    ) -> None:
        """Gate the upcoming call on cumulative usage for today (UTC).

        TODO(hosted-tier): per-tenant budgeting — currently global;
        revisit when hosted tier lands.
        """
        if not self.daily_token_budget_enabled:
            return
        if self.store is None:
            # No store attached (tests, bootstrap before connect): skip.
            return
        today_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        try:
            summary = await self.store.get_llm_usage_summary(since=today_start)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Token budget check failed to query usage: %s", exc)
            return
        used = int(summary.get("total_tokens", 0) or 0)
        # Pessimistic upper bound on what this call could consume:
        # prompt tokens estimated by the cheap ~4 chars/token rule,
        # plus the model's output cap.
        estimated_upcoming = (
            (len(system_prompt) + len(user_prompt)) // 4
            + int(self.max_tokens_per_cycle)
        )
        projected = used + estimated_upcoming
        if projected > self.daily_token_budget:
            msg = (
                f"daily token budget exceeded "
                f"(used {used} of {self.daily_token_budget}, "
                f"need {estimated_upcoming} for this call)"
            )
            # Emit a visible event so the operator sees the block in
            # the events timeline. Fire-and-forget — the block itself
            # is the load-bearing behavior; event emission is audit.
            try:
                await self.store.emit_event(
                    event_type="token_budget_exceeded",
                    source="llm_provider",
                    subject="daily_token_budget",
                    ref_table="llm_usage",
                    ref_id=today_start.isoformat(),
                    severity="warning",
                    summary=msg,
                )
            except Exception as exc:  # pragma: no cover
                log.debug("token_budget_exceeded event emit failed: %s", exc)
            raise LLMHardError(msg)


def parse_json_response(text: str) -> dict[str, Any]:
    """Extract a JSON object from an LLM response.

    Strips ``json`` code fences and falls back to a permissive object regex.
    Raises :class:`json.JSONDecodeError` if no valid JSON object can be found.
    """
    if not text:
        raise json.JSONDecodeError("empty LLM response", text, 0)
    cleaned = text.strip()
    fenced = JSON_FENCE_RE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = JSON_OBJECT_RE.search(cleaned)
        if match:
            return json.loads(match.group(0))
        raise


# ----------------------------------------------------------------------
# Mock provider — used in tests and when no API key is configured
# ----------------------------------------------------------------------


class MockProvider(LLMProvider):
    """Returns canned but realistic JSON responses. Useful for tests and demos."""

    name = "mock"

    def __init__(
        self,
        model: str = "mock-model",
        canned: dict[str, Any] | None = None,
        max_tokens_per_cycle: int = 4000,
        temperature: float = 0.2,
        daily_token_budget: int = 200_000,
        daily_token_budget_enabled: bool = True,
    ) -> None:
        super().__init__(
            model=model,
            max_tokens_per_cycle=max_tokens_per_cycle,
            temperature=temperature,
            daily_token_budget=daily_token_budget,
            daily_token_budget_enabled=daily_token_budget_enabled,
        )
        self.canned = canned

    def _default_response(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        # Match on markers we put in the real prompt templates so we can tell
        # the three modes apart unambiguously.
        text = user_prompt.lower()
        if "detected anomalies:" in text:
            return {
                "insights": [
                    {
                        "title": "Mock anomaly insight",
                        "severity": "warning",
                        "summary": "Mock provider returned a synthetic insight.",
                        "details": "This is a placeholder used by the mock LLM provider.",
                        "related_metrics": ["table_inserts"],
                        "related_tables": ["public.tasks"],
                        "recommended_actions": ["Investigate recent deploys"],
                        "confidence": 0.6,
                    }
                ]
            }
        if "tool_calls" in text or "query_observability" in text:
            return {
                "tool_calls": [
                    {
                        "name": "query_observability",
                        "parameters": {
                            "sql": "SELECT metric_name, value, collected_at "
                                   "FROM metric_snapshots "
                                   "ORDER BY collected_at DESC LIMIT 20",
                        },
                    }
                ],
                "reasoning": "Querying observability metrics",
            }
        if "tool results:" in text or "interpret the results" in text:
            return {
                "narrative": (
                    "Your system is collecting metrics normally. "
                    "The most recent data shows healthy metric values "
                    "across all monitored tables."
                ),
                "widget_config": {
                    "widget_type": "table",
                    "title": "Recent Metrics",
                    "columns": ["metric_name", "value", "collected_at"],
                },
                "domains": ["observability"],
                "freshness": "from latest data",
                "warnings": [],
            }
        if "allowed tables" in text and "user question:" in text:
            return {
                "sql": "SELECT metric_name, value, collected_at "
                       "FROM metric_snapshots "
                       "ORDER BY collected_at DESC LIMIT 20",
                "widget_type": "time_series",
                "title": "Recent metrics",
                "encoding": {"x": "collected_at", "y": "value"},
            }
        if "user question:" in text:
            return {
                "answer": "Mock answer from MockProvider.",
                "evidence": ["table_row_count"],
                "follow_ups": [],
            }
        return {
            "app_type": "task management app",
            "summary": "Synthetic mock system summary describing a task management app.",
            "critical_tables": ["public.tasks", "public.users"],
            "key_metrics": [
                "table_row_count",
                "table_inserts",
                "active_connections",
            ],
            "risks": [
                "Long-running queries on tasks table during peak load",
                "Unbounded growth of audit/event tables",
            ],
            "questions": [
                "What's the expected daily task creation rate?",
                "Which tables represent paying customer state?",
            ],
        }

    async def _call(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, int, int]:
        if self.canned is not None:
            data = self.canned
        else:
            data = self._default_response(system_prompt, user_prompt)
        text = json.dumps(data)
        prompt_tokens = max(1, (len(system_prompt) + len(user_prompt)) // 4)
        completion_tokens = max(1, len(text) // 4)
        return text, prompt_tokens, completion_tokens


# ----------------------------------------------------------------------
# Anthropic provider
# ----------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""

    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        api_key: str | None = None,
        max_tokens_per_cycle: int = 4000,
        temperature: float = 0.2,
        daily_token_budget: int = 200_000,
        daily_token_budget_enabled: bool = True,
    ) -> None:
        super().__init__(
            model=model,
            max_tokens_per_cycle=max_tokens_per_cycle,
            temperature=temperature,
            daily_token_budget=daily_token_budget,
            daily_token_budget_enabled=daily_token_budget_enabled,
        )
        if not api_key:
            raise LLMHardError(
                "Anthropic provider requires an API key (set ANTHROPIC_API_KEY)"
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMHardError("anthropic SDK is not installed") from exc
        self._client = AsyncAnthropic(api_key=api_key)

    async def _call(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, int, int]:
        try:
            msg = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens_per_cycle,
                temperature=self.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:  # pragma: no cover
            raise _classify_provider_error(exc, "Anthropic") from exc
        text = "".join(getattr(block, "text", "") for block in (msg.content or []))
        usage = getattr(msg, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return text, prompt_tokens, completion_tokens


# ----------------------------------------------------------------------
# OpenAI provider
# ----------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider."""

    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        max_tokens_per_cycle: int = 4000,
        temperature: float = 0.2,
        daily_token_budget: int = 200_000,
        daily_token_budget_enabled: bool = True,
    ) -> None:
        super().__init__(
            model=model,
            max_tokens_per_cycle=max_tokens_per_cycle,
            temperature=temperature,
            daily_token_budget=daily_token_budget,
            daily_token_budget_enabled=daily_token_budget_enabled,
        )
        if not api_key:
            raise LLMHardError(
                "OpenAI provider requires an API key (set OPENAI_API_KEY)"
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMHardError("openai SDK is not installed") from exc
        self._client = AsyncOpenAI(api_key=api_key)

    async def _call(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, int, int]:
        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens_per_cycle,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:  # pragma: no cover
            raise _classify_provider_error(exc, "OpenAI") from exc
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return text, prompt_tokens, completion_tokens


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def build_provider(
    provider: str,
    model: str,
    api_key: str | None,
    max_tokens_per_cycle: int = 4000,
    temperature: float = 0.2,
    daily_token_budget: int = 200_000,
    daily_token_budget_enabled: bool = True,
) -> LLMProvider:
    """Build an LLM provider for the given name.

    Falls back to :class:`MockProvider` if ``provider`` is ``mock``, empty,
    or if a real provider can't be constructed (e.g. missing API key) and the
    name is ``mock``-prefixed.

    Stage 4: ``daily_token_budget_enabled`` propagates through to the
    provider instance. The store is attached by the caller after
    construction via :meth:`LLMProvider.attach_store`.
    """
    p = (provider or "mock").lower()
    if p in ("mock", "none", ""):
        return MockProvider(
            model=model or "mock-model",
            max_tokens_per_cycle=max_tokens_per_cycle,
            temperature=temperature,
            daily_token_budget=daily_token_budget,
            daily_token_budget_enabled=daily_token_budget_enabled,
        )
    if p == "anthropic":
        return AnthropicProvider(
            model=model,
            api_key=api_key,
            max_tokens_per_cycle=max_tokens_per_cycle,
            temperature=temperature,
            daily_token_budget=daily_token_budget,
            daily_token_budget_enabled=daily_token_budget_enabled,
        )
    if p == "openai":
        return OpenAIProvider(
            model=model,
            api_key=api_key,
            max_tokens_per_cycle=max_tokens_per_cycle,
            temperature=temperature,
            daily_token_budget=daily_token_budget,
            daily_token_budget_enabled=daily_token_budget_enabled,
        )
    raise LLMHardError(f"Unknown LLM provider: {provider}")


_HARD_ERROR_HINTS = (
    "401",
    "403",
    "auth",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "permission",
    "quota",
    "billing",
    "account",
    "not found: model",
    # Permanent 400 conditions — retrying the identical oversized/malformed
    # payload will always return the same error.
    "prompt is too long",
    "context length",
    "context_length_exceeded",
    "too many tokens",
    "maximum context",
    "maximum tokens",
    "max_tokens",
    "invalid_request_error",
)

# HTTP 400 by itself is ambiguous (could be a transient validation quirk),
# but when paired with any of these substrings, treat as hard.
_STATUS_400_HARD_HINTS = (
    "prompt",
    "context",
    "token",
    "invalid_request",
    "invalid request",
)


def _classify_provider_error(exc: Exception, provider_name: str) -> LLMError:
    """Wrap a provider exception in the right ``LLMError`` subclass.

    We can't reliably import provider-specific exception classes without
    tying ourselves to them, so we sniff the stringified error for known
    auth/quota/overflow signals.

    Hard (no retry): auth, quota, prompt-too-long, invalid request, 401, 403.
    Soft (retry):    transient errors, 429 rate limits, 5xx server errors,
                     network/timeouts, malformed JSON.

    HTTP 400 is classified as hard when paired with prompt/context/token
    keywords — the same oversized payload will always return the same 400.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(hint in text for hint in _HARD_ERROR_HINTS):
        return LLMHardError(f"{provider_name} hard error: {exc}")
    # HTTP 400 with prompt/context/token hints — permanent bad request.
    if "400" in text and any(h in text for h in _STATUS_400_HARD_HINTS):
        return LLMHardError(f"{provider_name} hard error: {exc}")
    return LLMSoftError(f"{provider_name} soft error: {exc}")


def estimate_cost_usd(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """Very rough cost estimate. Mock provider returns 0."""
    if provider in ("mock", "none", ""):
        return 0.0
    rates = {
        ("anthropic", "claude-sonnet-4-20250514"): (3.0 / 1_000_000, 15.0 / 1_000_000),
        ("openai", "gpt-4o"): (2.5 / 1_000_000, 10.0 / 1_000_000),
    }
    in_rate, out_rate = rates.get((provider, model), (3.0 / 1_000_000, 15.0 / 1_000_000))
    return prompt_tokens * in_rate + completion_tokens * out_rate
