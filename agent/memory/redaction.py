"""Redaction for trace text: secrets, emails and length limits. Applied to trace fields, not to research content."""

from __future__ import annotations

import re

MAX_TEXT = 500
_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"dapi[0-9a-fA-F]{20,}"), "[REDACTED]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
]


def redact_text(value: str | None, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = value
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit]
