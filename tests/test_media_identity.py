import pytest

from telegram_media_automation.domain.media import MediaKind
from telegram_media_automation.domain.rules.media_identity import suggest_media_identity


@pytest.mark.parametrize(
    ("text", "title", "year", "kind", "season", "episode", "episode_end"),
    [
        ("A Show S01E02 1080p", "A Show", None, MediaKind.SERIES, 1, 2, None),
        (
            "葬送的芙莉莲 第十二集 WEB-DL",
            "葬送的芙莉莲",
            None,
            MediaKind.SERIES,
            1,
            12,
            None,
        ),
        ("A_Show_S02E03-E05_HEVC", "A Show", None, MediaKind.SERIES, 2, 3, 5),
        ("Movie Name (2026) 2160p HDR", "Movie Name", 2026, MediaKind.MOVIE, None, None, None),
        (
            "Blade Runner 2049 1080p",
            "Blade Runner 2049",
            None,
            MediaKind.MOVIE,
            None,
            None,
            None,
        ),
        ("1917", "1917", None, MediaKind.MOVIE, None, None, None),
    ],
)
def test_conservative_media_identity_suggestions(
    text: str,
    title: str,
    year: int | None,
    kind: MediaKind,
    season: int | None,
    episode: int | None,
    episode_end: int | None,
) -> None:
    result = suggest_media_identity(text)

    assert result.title == title
    assert result.year == year
    assert result.kind == kind
    assert result.season == season
    assert result.episode == episode
    assert result.episode_end == episode_end


def test_empty_media_identity_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        suggest_media_identity("  ")
