"""Shared sensitive-column detection and redaction helpers.

Central policy used by every surface that renders customer columns
into LLM prompts or diagnostic evidence: the autonomous diagnostic
path, the schema catalog that the planning prompt reads, and the chat
agent's application-query executor. Before this module existed, the
patterns were duplicated in ``agent/schema_catalog.py`` and the logic
was re-implemented across callers — a drift risk every time the list
grew.

The policy here is deliberately narrow: substring matches against
credential/authentication material. Customer-identifier columns
(``user_id``, ``patient_id``, ``customer_id``, etc.) are *not*
sensitive by this default — see the Strategy Notes entry "Customer-
identifier redaction deferred until customer need arises" for why.
The patterns below can be extended additively without changing any
caller.
"""
from __future__ import annotations

import re

# Substring patterns (lowercased) that mark a column as holding
# authentication or credential material. Matching is case-insensitive
# substring; e.g. ``password_hash`` matches on both ``password`` and
# ``hash``. Any caller that needs a different classification should
# layer its own rules on top, not mutate this set at runtime.
SENSITIVE_COLUMN_PATTERNS: frozenset[str] = frozenset({
    # Base credential / authentication material
    "api_key",
    "api_token",
    "secret",
    "password",
    "hash",
    "token",          # covers session_token, refresh_token, bearer_token, etc.
    "credential",
    "private_key",
    "embedding",
    "openai_api_key",
    "service_role_key",
    # S0.4 / Stage 1: additional auth material explicitly flagged.
    # "token" already covers session_token / refresh_token; listed here
    # only where the intuitive pattern would NOT substring-match an
    # existing entry. Duplicates are suppressed below and justified
    # with a comment rather than added to the set.
    "jwt",
    "auth_code",
    "signing_key",
    "bearer",
    "oauth_",         # oauth_id, oauth_state, oauth_refresh_token, etc.
    # NOT added (already covered by existing substrings):
    #   "session_token"  — covered by "token"
    #   "refresh_token"  — covered by "token"
})


def is_sensitive_column(col_name: str) -> bool:
    """Return True if ``col_name`` matches any sensitive substring.

    Case-insensitive. The column name need not be schema-qualified;
    callers typically pass the bare column identifier.
    """
    if not col_name:
        return False
    name_lower = col_name.lower()
    return any(pat in name_lower for pat in SENSITIVE_COLUMN_PATTERNS)


def redact_reason(col_name: str) -> str | None:
    """Return the first sensitive substring that matched, or ``None``.

    Used for audit-trail rendering in redacted diagnostic rows — the
    operator should be able to see *why* a column was redacted without
    having to run grep against the pattern list. When multiple
    substrings match (e.g. ``jwt_secret`` matches both ``jwt`` and
    ``secret``), the first match in iteration order is returned; the
    set is a frozenset so this is stable across runs within a
    single process but is not a stable UI-facing identifier. That's
    fine: the tag is for the operator's eye, not for downstream code.
    """
    if not col_name:
        return None
    name_lower = col_name.lower()
    for pattern in SENSITIVE_COLUMN_PATTERNS:
        if pattern in name_lower:
            return pattern
    return None


# Matches postgres/postgresql(+driver) URLs with an inline password.
# Captures the scheme + userinfo up to (but not including) the ``:`` that
# precedes the password, so the replacement can restore the user portion
# verbatim and only mask the credential.
_DSN_PASSWORD_RE = re.compile(
    r"(postgres(?:ql)?(?:\+[a-zA-Z0-9_]+)?://[^:@/\s]+):[^@\s]+@"
)


def scrub_dsn(text: str) -> str:
    """Replace inline passwords in any postgres DSN found in ``text``.

    Intended for log messages and exception strings. asyncpg and the DSN
    parser can echo the full connection string in error output when a
    URL is malformed, which would leak the credential embedded in
    ``SUPABASE_DB_URL`` into log aggregators. Callers that serialize
    untrusted-looking text (including ``str(exc)``) should pipe it
    through this helper first. The replacement preserves the user and
    host portions so operators retain enough signal to diagnose.
    """
    if not text:
        return text
    return _DSN_PASSWORD_RE.sub(r"\1:***@", text)


__all__ = [
    "SENSITIVE_COLUMN_PATTERNS",
    "is_sensitive_column",
    "redact_reason",
    "scrub_dsn",
]
