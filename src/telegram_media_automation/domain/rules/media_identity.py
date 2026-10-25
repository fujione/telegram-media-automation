"""Conservative media identity suggestions for editable Web form defaults."""

from __future__ import annotations

import re
from dataclasses import dataclass

from telegram_media_automation.domain.media import MediaKind
from telegram_media_automation.domain.rules.episode_parser import parse_episode

_BRACKETED_YEAR = re.compile(
    r"(?:\(|\[)\s*(?P<year>18[7-9]\d|19\d{2}|20\d{2}|21\d{2}|2200)\s*(?:\)|\])"
)
_QUALITY_TOKEN = re.compile(
    r"(?i)(?<![A-Z0-9])(?:2160P|1080P|720P|576P|480P|4K|UHD|HDR10[+]?|HDR|"
    r"DOLBY[ ._-]?VISION|DV|WEB[ ._-]?DL|WEBRIP|BLU[ ._-]?RAY|BDRIP|"
    r"X26[45]|H[.]?26[45]|HEVC|AV1)(?![A-Z0-9])"
)
_EMPTY_BRACKETS = re.compile(r"(?:\(\s*\)|\[\s*\])")


@dataclass(frozen=True, slots=True)
class MediaIdentitySuggestion:
    title: str
    year: int | None
    kind: MediaKind
    season: int | None
    episode: int | None
    episode_end: int | None


def suggest_media_identity(text: str) -> MediaIdentitySuggestion:
    """Suggest editable fields without claiming that media identity is confirmed."""
    original = " ".join(text.strip().split())
    if not original:
        raise ValueError("media identity source cannot be empty")
    working = original.replace("_", " ").replace("＿", " ")  # noqa: RUF001
    parsed_episode = parse_episode(working)
    if parsed_episode is not None:
        working = re.sub(re.escape(parsed_episode.evidence), " ", working, count=1, flags=re.I)

    year_match = _BRACKETED_YEAR.search(working)
    year = int(year_match.group("year")) if year_match else None
    if year_match:
        working = f"{working[: year_match.start()]} {working[year_match.end() :]}"

    working = _QUALITY_TOKEN.sub(" ", working)
    working = _EMPTY_BRACKETS.sub(" ", working)
    title = re.sub(r"\s+", " ", working).strip(" ._-\N{EN DASH}\N{EM DASH}|\N{MIDDLE DOT}")
    if not title:
        title = original

    episode = parsed_episode.value if parsed_episode is not None else None
    return MediaIdentitySuggestion(
        title=title,
        year=year,
        kind=MediaKind.SERIES if episode is not None else MediaKind.MOVIE,
        season=episode.season if episode is not None else None,
        episode=episode.start if episode is not None else None,
        episode_end=(episode.end if episode is not None and episode.end > episode.start else None),
    )
