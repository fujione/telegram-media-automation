import json
import logging

from telegram_media_automation.infrastructure.logging import JsonFormatter, configure_logging
from telegram_media_automation.security import redact_sensitive


def test_sensitive_values_are_redacted_from_text() -> None:
    value = (
        "connect socks5://alice:secret@proxy.example:1080 "
        "api_hash=abcdef password=hunter2 Bearer token-value phone +8613800000000"
    )

    redacted = redact_sensitive(value)

    assert "secret" not in redacted
    assert "abcdef" not in redacted
    assert "hunter2" not in redacted
    assert "token-value" not in redacted
    assert "13800000000" not in redacted
    assert "proxy.example" in redacted


def test_json_formatter_redacts_message_and_exception() -> None:
    try:
        raise RuntimeError("password=do-not-log +8613800000000")
    except RuntimeError:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "proxy socks5://user:pass@localhost:1080",
            (),
            exc_info=__import__("sys").exc_info(),
        )

    payload = json.loads(JsonFormatter().format(record))
    serialized = json.dumps(payload)
    assert "do-not-log" not in serialized
    assert "13800000000" not in serialized
    assert "user:pass" not in serialized


def test_optional_telethon_accelerator_messages_are_not_logged_as_info() -> None:
    configure_logging(json_output=False)

    assert logging.getLogger("telethon.crypto.libssl").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("telethon.crypto.aes").getEffectiveLevel() == logging.WARNING
