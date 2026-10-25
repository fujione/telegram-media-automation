"""Atomic rotation for file-backed local administrator credentials."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from uuid import uuid4

from telegram_media_automation.infrastructure.config import Settings


def rotate_api_token(settings: Settings) -> tuple[str, Path]:
    target = settings.api_token_file
    if target is None:
        raise ValueError("API Token is not file-backed; configure APP_API_TOKEN_FILE first")
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    token = secrets.token_urlsafe(32)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{token}\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return token, target
