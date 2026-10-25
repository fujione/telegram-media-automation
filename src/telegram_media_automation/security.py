"""Shared redaction helpers for logs and externally visible error records."""

from __future__ import annotations

import re

_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/@:\s]+):([^/@\s]+)@")
_NAMED_SECRET = re.compile(
    r"(?i)((?:api[_ -]?(?:hash|token)|password|proxy[_ -]?url)\s*[=:]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)(bearer\s+)([^\s]+)")
_PHONE = re.compile(r"(?<![\w+])\+[0-9][0-9 ()-]{6,}[0-9]")


def redact_sensitive(value: str) -> str:
    redacted = _URL_CREDENTIALS.sub(r"\1***:***@", value)
    redacted = _NAMED_SECRET.sub(r"\1***", redacted)
    redacted = _BEARER.sub(r"\1***", redacted)
    return _PHONE.sub("+[redacted-phone]", redacted)
