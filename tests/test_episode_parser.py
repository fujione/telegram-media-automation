import pytest

from telegram_media_automation.domain.rules.episode_parser import (
    parse_chinese_number,
    parse_episode,
)


@pytest.mark.parametrize(
    ("text", "season", "start", "end"),
    [
        ("Show.S01E02.1080p.mkv", 1, 2, 2),
        ("Show S02E03-E05", 2, 3, 5),
        ("Show EP07", 1, 7, 7),
        ("Show Episode 9", 1, 9, 9),
        ("第十二集", 1, 12, 12),
        ("第1集至第3集", 1, 1, 3),
        ("01集-02集", 1, 1, 2),
        ("1-2集", 1, 1, 2),
        ("第一至第三集", 1, 1, 3),
    ],
)
def test_supported_episode_formats(text: str, season: int, start: int, end: int) -> None:
    result = parse_episode(text)
    assert result is not None
    assert (result.value.season, result.value.start, result.value.end) == (season, start, end)
    assert result.evidence
    assert result.confidence >= 90


@pytest.mark.parametrize("text", ["全24集", "共 12 集", "Movie 2026 2160p", "Release 1080p"])
def test_does_not_guess_unmarked_or_total_numbers(text: str) -> None:
    assert parse_episode(text) is None


@pytest.mark.parametrize(("text", "expected"), [("十二", 12), ("二十六", 26), ("一百零二", 102)])
def test_chinese_numbers(text: str, expected: int) -> None:
    assert parse_chinese_number(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "example.S01E00.mp4",
        "S01E12-E01",
        "EP00",
        "EP12-EP02",
        "第零集",
        "第0集",
        "第十二至第三集",
        "第12集至第3集",
    ],
)
def test_invalid_episode_markers_are_rejected_without_crashing(text: str) -> None:
    assert parse_episode(text) is None
