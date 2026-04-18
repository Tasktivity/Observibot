"""Shared prompt-assembly utilities for the agent layer.

Extracted from :mod:`observibot.agent.chat_agent` so the monitor-side
analyzer (and Step 3.4's diagnostic evidence rendering) can reuse the
same token budgeting and sampling logic without importing the chat
agent.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

log = logging.getLogger(__name__)


# Thresholds for :func:`log_prompt_size`. Warning fires well before any
# real limit; error fires close to the 200k context window so it shows
# up in logs. Both are relative to the model's context window, not to
# the customer's data scale — this is explicitly token-space, not
# row-space, so they are Tier 0 scale-invariant.
PROMPT_WARN_TOKENS = 30_000
PROMPT_ERROR_TOKENS = 150_000


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token. Cheap and good enough."""
    return len(text) // 4


def enforce_budget(text: str, max_tokens: int, label: str) -> str:
    """Truncate ``text`` so it fits within ``max_tokens`` tokens.

    Truncation is char-based (~4 chars/token). Trims to the last newline
    in the final 30% of the cut so we don't slice mid-entry, then
    appends a visible note so the LLM knows the section was truncated
    (not silent data loss). Returns the original text unchanged if
    already under budget.
    """
    est = estimate_tokens(text)
    if est <= max_tokens:
        return text
    max_chars = max_tokens * 4
    truncated = text[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars * 0.7:
        truncated = truncated[:last_nl]
    log.warning(
        "Prompt section '%s' truncated: ~%d tokens → ~%d tokens (budget=%d)",
        label, est, estimate_tokens(truncated), max_tokens,
    )
    return (
        truncated
        + f"\n[Truncated: '{label}' exceeded {max_tokens}-token budget]"
    )


def log_prompt_size(
    prompt: str,
    label: str,
    sections: dict[str, str] | None = None,
) -> None:
    """Log the assembled prompt size with a per-section breakdown.

    DEBUG at normal sizes, WARNING above ``PROMPT_WARN_TOKENS``, ERROR
    above ``PROMPT_ERROR_TOKENS``. The breakdown turns a future
    'mystery 400 from Anthropic' into a single grep-able log line.
    """
    total_chars = len(prompt)
    total_tokens = total_chars // 4

    if total_tokens > PROMPT_ERROR_TOKENS:
        level = logging.ERROR
    elif total_tokens > PROMPT_WARN_TOKENS:
        level = logging.WARNING
    else:
        level = logging.DEBUG

    breakdown = ""
    if sections:
        parts = [
            f"{k}=~{estimate_tokens(v)}tok"
            for k, v in sections.items() if v
        ]
        if parts:
            breakdown = f" [{', '.join(parts)}]"

    log.log(
        level,
        "%s prompt: ~%d tokens (%d chars)%s",
        label, total_tokens, total_chars, breakdown,
    )


_TIME_COLUMN_SUFFIXES = (
    "_at", "_date", "_time", "_ts", "day", "hour", "bucket", "month",
)


# Stage 8: unicode control-category codes that never belong in a
# prompt. ``Cc``=control, ``Cf``=format, ``Cs``=surrogate, ``Co``=
# private-use, ``Cn``=unassigned. ``\n`` and ``\t`` are Cc but
# deliberately allowed (preserves readable multi-line commit
# messages) — handled by the explicit carve-out below.
_UNICODE_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

# Collapse runs of inline whitespace to a single space but keep
# newlines intact. ``[^\\S\\n]+`` matches non-newline whitespace.
_INLINE_WHITESPACE_RE = re.compile(r"[^\S\n]+")


def sanitize_untrusted_text(
    text: str | None,
    max_length: int = 500,
) -> str:
    """Strip control characters and cap length on text from external sources.

    Used for change-event summaries / details (GitHub commit messages,
    Railway deploy notes, etc.) before they enter any LLM prompt.
    Sanitization is about *shape* (encoding, length) — not semantics.
    The LLM-level guardrail in the hypothesis prompt is what tells the
    model to treat change text as data rather than instruction.

    Rules:

    - ``None`` input returns ``""``.
    - ASCII control characters except ``\\n`` (newline) and ``\\t``
      (tab) are removed — carriage returns, bells, form feeds, etc.
    - Unicode characters in the Cc/Cf/Cs/Co/Cn categories are removed
      (bidi overrides, zero-width joiners, private-use codepoints).
    - Runs of inline whitespace collapse to a single space; newlines
      are preserved so paragraph structure survives.
    - Truncates to ``max_length`` with a trailing ``"..."`` when the
      limit is exceeded. The ellipsis counts against the budget so
      the returned string is never longer than ``max_length``.
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in str(text):
        if ch in ("\n", "\t"):
            out.append(ch)
            continue
        # Strip ASCII control chars outside \n/\t.
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            continue
        # Strip Unicode control-category codepoints.
        if unicodedata.category(ch) in _UNICODE_CONTROL_CATEGORIES:
            continue
        out.append(ch)
    cleaned = "".join(out)
    cleaned = _INLINE_WHITESPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip()
    if max_length <= 0:
        return ""
    # Hotfix item 4: below 3 the ellipsis itself breaks the length
    # contract ("..." is 3 chars). Prefer a straight slice so the
    # return value is never longer than ``max_length``. Production
    # callers use 200+; this edge case exists so the contract holds
    # end-to-end rather than "almost always."
    if max_length < 3:
        return cleaned[:max_length]
    if len(cleaned) > max_length:
        cut = max(0, max_length - 3)
        cleaned = cleaned[:cut] + "..."
    return cleaned


def sample_rows(rows: list[dict], max_rows: int = 50) -> tuple[list[dict], str]:
    """Smart sampling based on result shape.

    Pipeline-audit Fix 5: replace the head-only 20-row sample, which
    silently deceived the LLM on time-series and distribution
    questions (it would narrate from 2-4% of the data as if it were
    the full picture). Returns ``(sample, description)`` so the LLM
    knows what kind of slice it's seeing.
    """
    n = len(rows)
    if n <= max_rows:
        return rows, f"{n} rows (complete)"

    cols = list(rows[0].keys()) if rows else []
    time_cols = [
        c for c in cols
        if any(c.lower().endswith(s) for s in _TIME_COLUMN_SUFFIXES)
    ]

    if time_cols:
        # Stratified time-series sample: first 10, last 10, ~10 evenly from the
        # middle. The LLM can see start, end, and trend without us paying for
        # the entire result set.
        head = 10
        tail = 10
        indices: set[int] = set(range(min(head, n)))
        indices |= set(range(max(0, n - tail), n))
        middle_lo, middle_hi = head, max(head, n - tail)
        if middle_hi > middle_lo:
            step = max(1, (middle_hi - middle_lo) // 10)
            indices |= set(range(middle_lo, middle_hi, step))
        sample = [rows[i] for i in sorted(indices) if i < n]
        return (
            sample,
            f"{len(sample)} of {n} rows (time-series sample: first/last/middle)",
        )

    numeric_cols = [
        c for c in cols if isinstance(rows[0].get(c), (int, float))
        and not isinstance(rows[0].get(c), bool)
    ]
    if numeric_cols:
        sample = rows[:20]
        stats: dict[str, dict[str, Any]] = {}
        for col in numeric_cols[:3]:
            values = [
                r[col] for r in rows
                if isinstance(r.get(col), (int, float))
                and not isinstance(r.get(col), bool)
            ]
            if values:
                stats[col] = {
                    "min": min(values),
                    "max": max(values),
                    "count": len(values),
                }
        return (
            sample,
            f"20 of {n} rows (head sample). Stats: {json.dumps(stats, default=str)}",
        )

    # Default: head + tail so the LLM at least sees both ends of the range.
    sample = rows[:15] + rows[-5:]
    return sample, f"20 of {n} rows (first 15 + last 5)"
