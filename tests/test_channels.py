import pytest

from telegram_media_automation.domain.channels import (
    SearchChannel,
    normalize_channel_identifier,
    telegram_peer,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://t.me/example_channel", "example_channel"),
        ("https://t.me/s/example_channel", "example_channel"),
        ("t.me/example_channel/123", "example_channel"),
        ("@example_channel", "example_channel"),
        ("example_channel", "example_channel"),
        ("-1001234567890", "-1001234567890"),
        ("https://t.me/c/1234567890/55", "-1001234567890"),
    ],
)
def test_channel_identifier_accepts_common_telegram_formats(value: str, expected: str) -> None:
    assert normalize_channel_identifier(value) == expected
    assert SearchChannel(value).identifier == expected


def test_numeric_channel_identifier_becomes_integer_peer() -> None:
    assert telegram_peer("https://t.me/c/1234567890/55") == -1001234567890


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/channel",
        "https://t.me/+private-invite",
        "https://t.me/joinchat/private-invite",
        "channel/name",
    ],
)
def test_channel_identifier_rejects_ambiguous_or_unrelated_links(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_channel_identifier(value)
