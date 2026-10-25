"""Configured Telegram channels used only as search scopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4

_TELEGRAM_LINK_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "www.telegram.me"})


def normalize_channel_identifier(identifier: str) -> str:
    """Return a stable peer identifier from a username, numeric ID, or Telegram link."""
    normalized = identifier.strip()
    if not normalized:
        raise ValueError("channel identifier is required")
    link_candidate = normalized
    if normalized.casefold().startswith(("t.me/", "telegram.me/", "www.t.me/")):
        link_candidate = f"https://{normalized}"
    parsed = urlparse(link_candidate)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() not in (
            _TELEGRAM_LINK_HOSTS
        ):
            raise ValueError("channel URL must use t.me")
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0].casefold() == "s":
            parts.pop(0)
        if parts and parts[0].casefold() == "c" and len(parts) >= 2 and parts[1].isdigit():
            return f"-100{parts[1]}"
        if not parts or parts[0].casefold() == "joinchat" or parts[0].startswith("+"):
            raise ValueError("invite links cannot identify a search channel")
        normalized = parts[0]
    normalized = normalized.removeprefix("@").strip()
    if not normalized or "/" in normalized or len(normalized) > 300:
        raise ValueError("channel identifier is invalid")
    return normalized


def telegram_peer(identifier: str) -> str | int:
    """Normalize public links/usernames and convert numeric Telegram IDs."""
    normalized = normalize_channel_identifier(identifier)
    return int(normalized) if normalized.lstrip("-").isdigit() else normalized


@dataclass(slots=True)
class SearchChannel:
    identifier: str
    name: str | None = None
    id: UUID = field(default_factory=uuid4)
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.identifier = normalize_channel_identifier(self.identifier)
        self.name = self.name.strip() if self.name else None
