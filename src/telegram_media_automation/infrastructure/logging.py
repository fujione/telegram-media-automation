"""Minimal structured logging with no request bodies or credentials."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from telegram_media_automation.security import redact_sensitive


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
        }
        for name in ("request_id", "method", "path", "status_code", "duration_ms"):
            if hasattr(record, name):
                payload[name] = getattr(record, name)
        if record.exc_info:
            payload["exception"] = redact_sensitive(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(*, json_output: bool, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # These INFO messages only announce an optional native AES accelerator fallback.
    # The Python implementation is functional and the messages otherwise look like failures.
    logging.getLogger("telethon.crypto.libssl").setLevel(logging.WARNING)
    logging.getLogger("telethon.crypto.aes").setLevel(logging.WARNING)
