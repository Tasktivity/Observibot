"""Unit tests for :mod:`observibot.core.redaction`.

The module is the single source of truth for sensitive-column
classification. Every caller (analyzer ``_redact_row``,
``schema_catalog``, chat ``_exec_application``) resolves through
these helpers, so the tests must cover both positive and negative
cases across schema styles that have nothing to do with TaskGator
(Tier 0).
"""
from __future__ import annotations

import pytest

from observibot.core.redaction import (
    SENSITIVE_COLUMN_PATTERNS,
    is_sensitive_column,
    redact_reason,
    scrub_dsn,
)


# -------------------------------------------------------------------
# Positive matches — each canonical pattern and a representative name
# -------------------------------------------------------------------

_POSITIVE_CASES: list[tuple[str, str]] = [
    # (column_name, expected_substring_in_match_reason)
    ("api_key", "api_key"),
    ("api_token", "api_token"),
    ("user_secret_id", "secret"),
    ("password_hash", "password"),
    ("bcrypt_hash", "hash"),
    ("session_token", "token"),
    ("refresh_token", "token"),
    ("stripe_credential", "credential"),
    ("private_key_pem", "private_key"),
    ("user_embedding", "embedding"),
    ("openai_api_key", "openai_api_key"),
    ("service_role_key", "service_role_key"),
    # New patterns added in Stage 1
    ("jwt", "jwt"),
    ("jwt_secret", "jwt"),
    ("one_time_auth_code", "auth_code"),
    ("gitlab_oauth_id", "oauth_"),
    ("doc_signing_key", "signing_key"),
    ("bearer", "bearer"),
    ("authorization_bearer", "bearer"),
]


@pytest.mark.parametrize("name,expected", _POSITIVE_CASES)
def test_is_sensitive_column_matches(name: str, expected: str) -> None:
    assert is_sensitive_column(name) is True
    reason = redact_reason(name)
    assert reason is not None
    # Some names match multiple patterns (jwt_secret → jwt AND secret).
    # redact_reason returns the first in iteration order; the test just
    # confirms the returned reason is indeed a substring match.
    assert reason in name.lower()


# -------------------------------------------------------------------
# Negative cases — ordinary columns must NOT be flagged
# -------------------------------------------------------------------

_NEGATIVE_CASES: list[str] = [
    "id",
    "name",
    "email",
    "created_at",
    "order_status",
    "customer_id",      # explicitly NOT sensitive by default policy
    "patient_id",
    "user_id",
    "mrn",
    "icd10_code",
    "event_count",
    "numbackends",
    "order_count",
    "duration_ms",
    "bytes_transferred",
]


@pytest.mark.parametrize("name", _NEGATIVE_CASES)
def test_redact_reason_returns_none_for_ordinary_columns(name: str) -> None:
    assert is_sensitive_column(name) is False
    assert redact_reason(name) is None


def test_is_sensitive_column_handles_empty_and_none() -> None:
    assert is_sensitive_column("") is False
    assert is_sensitive_column(None) is False  # type: ignore[arg-type]
    assert redact_reason("") is None
    assert redact_reason(None) is None  # type: ignore[arg-type]


def test_is_sensitive_column_is_case_insensitive() -> None:
    assert is_sensitive_column("API_KEY") is True
    assert is_sensitive_column("UserPassword") is True
    assert is_sensitive_column("JwtSecret") is True


def test_jwt_pattern_added() -> None:
    """Stage 1 additive change: jwt pattern must be present."""
    assert "jwt" in SENSITIVE_COLUMN_PATTERNS
    assert is_sensitive_column("jwt") is True


def test_bearer_pattern_added() -> None:
    """Stage 1 additive change: bearer pattern must be present."""
    assert "bearer" in SENSITIVE_COLUMN_PATTERNS
    assert is_sensitive_column("bearer_prefix") is True


def test_session_token_covered_by_token_pattern() -> None:
    """Stage 1: session_token must NOT be added as its own pattern
    because ``token`` already substring-matches it. Adding would
    create a duplicate with no behavioral difference.
    """
    assert "session_token" not in SENSITIVE_COLUMN_PATTERNS
    assert is_sensitive_column("session_token") is True  # via "token"


def test_refresh_token_covered_by_token_pattern() -> None:
    assert "refresh_token" not in SENSITIVE_COLUMN_PATTERNS
    assert is_sensitive_column("refresh_token") is True


def test_no_pattern_overlap_regression() -> None:
    """Every previously-existing pattern still matches its canonical
    example. Stage 1 is additive; behavior must not change for older
    patterns.
    """
    for pat in [
        "api_key", "api_token", "secret", "password", "hash",
        "token", "credential", "private_key", "embedding",
        "openai_api_key", "service_role_key",
    ]:
        assert is_sensitive_column(pat) is True, pat


# -------------------------------------------------------------------
# Tier 0 synthetic fixture coverage
# -------------------------------------------------------------------

from tests.fixtures.synthetic_schemas import (  # noqa: E402
    ecommerce_schema,
    event_stream_schema,
    medical_records_schema,
)


# -------------------------------------------------------------------
# scrub_dsn — inline-password masking for postgres connection strings
# -------------------------------------------------------------------


_DSN_SCRUB_CASES: list[tuple[str, str]] = [
    (
        "postgresql://observibot_reader:hunter2@db.proj.supabase.co:5432/postgres",
        "postgresql://observibot_reader:***@db.proj.supabase.co:5432/postgres",
    ),
    (
        "postgres://user:p%40ss@host/db",
        "postgres://user:***@host/db",
    ),
    (
        "postgresql+asyncpg://u:s3cret@h:5432/d",
        "postgresql+asyncpg://u:***@h:5432/d",
    ),
    (
        # Embedded inside a larger exception message.
        "invalid dsn: postgresql://u:pw@h/d — malformed",
        "invalid dsn: postgresql://u:***@h/d — malformed",
    ),
    (
        # Tenant-style user with a dot (Supabase pooler format).
        "postgresql://observibot_reader.abcd1234:leaky@aws-0-x.pooler.supabase.com:5432/postgres",
        "postgresql://observibot_reader.abcd1234:***@aws-0-x.pooler.supabase.com:5432/postgres",
    ),
]


@pytest.mark.parametrize("raw,expected", _DSN_SCRUB_CASES)
def test_scrub_dsn_masks_inline_password(raw: str, expected: str) -> None:
    assert scrub_dsn(raw) == expected


def test_scrub_dsn_noop_without_password() -> None:
    # No ``:password@`` section — nothing to scrub.
    assert scrub_dsn("postgresql://user@host/db") == "postgresql://user@host/db"


def test_scrub_dsn_noop_for_unrelated_text() -> None:
    text = "connector supabase_taskgator could not connect: timeout after 30s"
    assert scrub_dsn(text) == text


def test_scrub_dsn_handles_empty_and_none() -> None:
    assert scrub_dsn("") == ""
    assert scrub_dsn(None) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "schema_fn",
    [ecommerce_schema, medical_records_schema, event_stream_schema],
)
def test_no_false_positives_on_synthetic_schemas(schema_fn) -> None:  # noqa: ANN001
    """Tier 0: no column in any of the three reference synthetic
    schemas should be incorrectly flagged as sensitive. The fixtures
    deliberately use domain-appropriate column names (orders,
    patients, events); none should collide with the credential
    patterns.
    """
    model = schema_fn()
    for table in model.tables:
        for col in table.columns:
            name = col.get("name", "") if isinstance(col, dict) else ""
            assert not is_sensitive_column(name), (
                f"{table.name}.{name} unexpectedly flagged as sensitive"
            )
