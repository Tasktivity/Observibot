"""Post-synthesis guardrail: narratives that cite numbers not in the tool
results get flagged. Prevents the "117 jobs and 68 files stuck" class of
hallucination where the LLM invents plausible-looking figures.
"""
from __future__ import annotations

from observibot.agent.chat_agent import ToolResult, _find_unsupported_numbers


def _tr(rows: list[dict]) -> ToolResult:
    return ToolResult(domain="application", rows=rows, sql="SELECT 1", success=True)


def test_numbers_from_row_values_are_accepted() -> None:
    results = [_tr([
        {"status": "complete", "count": 796},
        {"status": "cancelled", "count": 32},
    ])]
    narrative = "Observed 796 complete jobs and 32 cancelled jobs."
    assert _find_unsupported_numbers(narrative, results) == []


def test_made_up_number_is_flagged() -> None:
    """The core regression: 117/68 don't appear in any row yet the LLM
    cites them as if they did."""
    results = [_tr([
        {"date": "2026-04-10", "extraction_jobs_count": 15,
         "complete_jobs": 0, "failed_jobs": 0},
        {"date": "2026-04-11", "extraction_jobs_count": 22,
         "complete_jobs": 0, "failed_jobs": 0},
    ])]
    narrative = (
        "Critical extraction pipeline failure detected: 0% completion rate "
        "across all jobs and files over the past 30 days, with 117 jobs "
        "and 68 files stuck in incomplete states."
    )
    unsupported = _find_unsupported_numbers(narrative, results)
    assert "117" in unsupported
    assert "68" in unsupported


def test_row_count_is_treated_as_supported() -> None:
    """Narratives often say 'found 3 services' — the row count itself."""
    results = [_tr([{"name": "web"}, {"name": "api"}, {"name": "worker"}])]
    narrative = "Found 3 services matching the query."
    assert _find_unsupported_numbers(narrative, results) == []


def test_small_integers_treated_as_structural() -> None:
    """'2-3 sentence answer' and '5 days ago' should not be flagged."""
    results = [_tr([{"count": 1000}])]
    narrative = "Over the past 5 days there have been 1000 events."
    # 5 is small, 1000 is in results
    assert _find_unsupported_numbers(narrative, results) == []


def test_rounding_slop_is_tolerated() -> None:
    """The LLM rounds 836 to 840 or 99.3% to 99% — this is fine."""
    results = [_tr([{"total": 836, "ratio": 0.993}])]
    narrative = "Approximately 840 total rows with a 99% success ratio."
    assert _find_unsupported_numbers(narrative, results) == []


def test_fraction_to_percent_is_tolerated() -> None:
    """Row has frequency=0.95, narrative says '95%' — same value."""
    results = [_tr([{"status": "complete", "frequency": 0.95}])]
    narrative = "Complete status accounts for 95% of rows."
    assert _find_unsupported_numbers(narrative, results) == []


def test_year_like_integers_not_flagged() -> None:
    """'in 2026' and 'since 2020' are structural, not data claims."""
    results = [_tr([{"count": 42}])]
    narrative = "Since 2020 the system has processed 42 events (as of 2026)."
    assert _find_unsupported_numbers(narrative, results) == []


def test_column_sum_is_accepted() -> None:
    """The LLM is allowed to sum a column: rows of 50+50+50 → claim 150."""
    results = [_tr([
        {"day": "mon", "events": 50},
        {"day": "tue", "events": 50},
        {"day": "wed", "events": 50},
    ])]
    narrative = "Total of 150 events across the 3 days."
    assert _find_unsupported_numbers(narrative, results) == []


def test_empty_results_does_not_explode() -> None:
    assert _find_unsupported_numbers("Claim with 117 items.", []) == []
    assert _find_unsupported_numbers("", [_tr([{"x": 1}])]) == []


def test_string_cell_values_match_narrative() -> None:
    """If a cell contains '2026-04-13' and the narrative mentions '13',
    the number should be supported via the string match."""
    results = [_tr([{"date": "2026-04-13", "count": 4}])]
    narrative = "4 cancelled jobs on April 13th."
    # '4' is small so skipped; '13' is in the date string
    assert _find_unsupported_numbers(narrative, results) == []
