"""Parse explicit season and episode markers without guessing bare numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from telegram_media_automation.domain.resources import EpisodeRange

_DIGITS = {
    "零": 0,
    "〇": 0,  # noqa: RUF001
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


@dataclass(frozen=True, slots=True)
class EpisodeParseResult:
    value: EpisodeRange
    evidence: str
    confidence: int


def parse_chinese_number(text: str) -> int | None:
    value = text.strip()
    if not value:
        return None
    if value.isdecimal():
        return int(value)
    if all(character in _DIGITS for character in value):
        result = 0
        for character in value:
            result = result * 10 + _DIGITS[character]
        return result

    total = 0
    current = 0
    for character in value:
        if character in _DIGITS:
            current = _DIGITS[character]
        elif character == "十":
            total += (current or 1) * 10
            current = 0
        elif character == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current or None


def _result(
    season: str | int, start: str | int, end: str | int | None, evidence: str, confidence: int
) -> EpisodeParseResult | None:
    first = int(start)
    last = int(end) if end is not None else first
    # Untrusted filenames must not break a search or the subscription queue.
    # Reject invalid markers rather than guessing a different episode.
    if int(season) < 0 or first <= 0 or last < first:
        return None
    return EpisodeParseResult(EpisodeRange(int(season), first, last), evidence, confidence)


def parse_episode(text: str, default_season: int = 1) -> EpisodeParseResult | None:
    if not text.strip():
        return None
    normalized = text.replace("_", " ").replace("＿", " ")  # noqa: RUF001

    season_match = re.search(
        r"(?i)(?<![A-Z0-9])S(?P<s>\d{1,2})[ ._-]*E(?P<a>\d{1,4})"
        r"(?:[ ]*(?:-|~|～|至|到)[ ]*(?:S\d{1,2}[ ._-]*)?E?(?P<b>\d{1,4}))?"  # noqa: RUF001
        r"(?!\d)",
        normalized,
    )
    if season_match:
        return _result(
            season_match["s"],
            season_match["a"],
            season_match["b"],
            season_match.group(0),
            100,
        )

    english_match = re.search(
        r"(?i)(?<![A-Z])(?:EP(?:ISODE)?|E)[ ._-]*(?P<a>\d{1,4})"
        r"(?:[ ]*(?:-|~|～|至|到)[ ]*(?:EP?(?:ISODE)?|E)?[ ._-]*(?P<b>\d{1,4}))?"  # noqa: RUF001
        r"(?!\d)",
        normalized,
    )
    if english_match:
        return _result(
            default_season, english_match["a"], english_match["b"], english_match.group(0), 95
        )

    if re.search(r"(?:全|共)\s*\d{1,4}\s*[集话話期]|\d{1,4}\s*[集话話期]\s*(?:全|完)", normalized):
        return None

    suffix_range_match = re.search(
        r"第?\s*(?P<a>[零〇一二两三四五六七八九十百\d]{1,8})\s*"
        r"(?:-|~|～|至|到)\s*第?\s*"  # noqa: RUF001
        r"(?P<b>[零〇一二两三四五六七八九十百\d]{1,8})\s*[集话話期]",
        normalized,
    )
    if suffix_range_match:
        start = parse_chinese_number(suffix_range_match["a"])
        end = parse_chinese_number(suffix_range_match["b"])
        if start is not None and end is not None:
            return _result(default_season, start, end, suffix_range_match.group(0), 90)

    chinese_match = re.search(
        r"第?\s*(?P<a>[零〇一二两三四五六七八九十百\d]{1,8})\s*[集话話期]"
        r"(?:\s*(?:-|~|～|至|到)\s*第?\s*"  # noqa: RUF001
        r"(?P<b>[零〇一二两三四五六七八九十百\d]{1,8})\s*[集话話期]?)?",
        normalized,
    )
    if chinese_match:
        start = parse_chinese_number(chinese_match["a"])
        end = parse_chinese_number(chinese_match["b"]) if chinese_match["b"] else start
        if start is not None and end is not None:
            return _result(default_season, start, end, chinese_match.group(0), 90)
    return None
