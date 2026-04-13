"""Deterministic secret scanning — redacts secrets before LLM submission."""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub PAT", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("GitHub OAuth", re.compile(r"gho_[A-Za-z0-9]{36}")),
    ("Slack Token", re.compile(r"xox[bporas]-[A-Za-z0-9-]+")),
    ("Private Key", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE KEY-----")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Generic API Key", re.compile(
        r"(?i)(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?([A-Za-z0-9_-]{20,})"
    )),
    ("Generic Secret", re.compile(r"(?i)(?:secret|password|passwd)\s*[:=]\s*['\"]?([^\s'\"]{8,})")),
    ("Connection String", re.compile(r"(?:postgres|mysql|redis|mongodb)://\S+")),
    ("Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}")),
    ("Anthropic Key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI Key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Base64 Credential", re.compile(
        r"(?i)(?:auth|credential|token)\s*[:=]\s*['\"]?"
        r"[A-Za-z0-9+/]{40,}={0,2}"
    )),
]


def scan_and_redact(content: str) -> tuple[str, list[str]]:
    """Scan content for secrets and redact them.

    Returns (redacted_content, list_of_warning_messages).
    """
    warnings: list[str] = []
    redacted = content

    for name, pattern in SECRET_PATTERNS:
        matches = pattern.findall(redacted)
        if matches:
            warnings.append(f"Detected potential {name} ({len(matches)} occurrence(s))")
            redacted = pattern.sub(f"[REDACTED:{name}]", redacted)

    if warnings:
        log.warning(
            "Secret scanner found %d potential secret(s) in code chunk",
            len(warnings),
        )

    return redacted, warnings


def has_secrets(content: str) -> bool:
    """Quick check: does this content contain any potential secrets?"""
    return any(pattern.search(content) for _name, pattern in SECRET_PATTERNS)
