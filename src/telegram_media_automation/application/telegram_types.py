"""Provider-neutral values returned by Telegram application ports."""

from __future__ import annotations

from dataclasses import dataclass

from telegram_media_automation.domain.resources import MediaResource


@dataclass(frozen=True, slots=True)
class ChannelScan:
    resources: list[MediaResource]
    last_message_id: int
