"""Secret detection and redaction.

Used before anything is logged, persisted, sent to a model, or returned to the
UI (AGENTS.md security rules; FR-10-03 redact). Also powers the "no real
secret material" fixture scan. Patterns are intentionally broad — false
positives are redacted, never leaked.
"""

from __future__ import annotations

import re

_PLACEHOLDER = "[REDACTED]"

# Each entry: (finding label, compiled pattern). Order does not matter; every
# match is redacted. Patterns target *real* secret material — obvious
# placeholders (containing PLACEHOLDER/EXAMPLE/XXXX) are deliberately not
# matched so test fixtures stay clean.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_access_key", re.compile(r"\b[A-Za-z0-9/+=]{40}\b(?=.*secret)", re.IGNORECASE)),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("basic_auth_url", re.compile(r"\b[a-z]+://[^\s:/@]+:[^\s:/@]+@[^\s/]+", re.IGNORECASE)),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?key|"
            r"token|credential)s?\b\s*[:=]\s*(?!(?:[\"']?(?:PLACEHOLDER|EXAMPLE|"
            r"REDACTED|CHANGEME|XXXX|<[^>]+>)))[\"']?[A-Za-z0-9/+=._-]{6,}"
        ),
    ),
]


def scan_secrets(text: str) -> list[str]:
    """Return the distinct secret-finding labels present in ``text``."""

    if not text:
        return []
    found: list[str] = []
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            found.append(label)
    return found


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Redact every secret match. Returns ``(redacted_text, labels)``."""

    if not text:
        return text, []
    found: list[str] = []
    redacted = text
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(redacted):
            found.append(label)
            redacted = pattern.sub(_PLACEHOLDER, redacted)
    return redacted, found


def contains_secret(text: str) -> bool:
    return bool(scan_secrets(text))
