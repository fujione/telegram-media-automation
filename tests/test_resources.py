import pytest

from telegram_media_automation.domain.resources import (
    EpisodeRange,
    MediaResource,
    QualityPolicy,
    ResourceQuality,
    rank_resources,
)


def resource(
    name: str, resolution: int, size: int, *, confidence: int = 100, language: str = "zh"
) -> MediaResource:
    return MediaResource(
        provider="telegram",
        locator=f"chat/1/{name}",
        file_name=name,
        file_size=size,
        identity_confidence=confidence,
        episode_range=EpisodeRange(1, 1, 1),
        quality=ResourceQuality(resolution=resolution, codec="hevc", languages=(language,)),
    )


def test_rank_prefers_policy_resolution_after_identity() -> None:
    values = [resource("720.mkv", 720, 10), resource("4k.mkv", 2160, 30)]
    ranked = rank_resources(values, QualityPolicy(), EpisodeRange(1, 1, 1))
    assert ranked[0].resource.file_name == "4k.mkv"
    assert ranked[0].reasons


def test_hard_filters_language_size_and_episode_coverage() -> None:
    values = [
        resource("english.mkv", 2160, 50, language="en"),
        resource("large.mkv", 2160, 500, language="zh"),
        resource("accepted.mkv", 1080, 100, language="zh"),
    ]
    policy = QualityPolicy(allowed_languages=("zh",), min_file_size=20, max_file_size=200)
    ranked = rank_resources(values, policy, EpisodeRange(1, 1, 1))
    assert [item.resource.file_name for item in ranked] == ["accepted.mkv"]


def test_preferred_language_breaks_an_otherwise_equal_tie() -> None:
    values = [
        resource("english.mkv", 1080, 100, language="en"),
        resource("chinese.mkv", 1080, 100, language="zh"),
    ]

    ranked = rank_resources(values, QualityPolicy(), EpisodeRange(1, 1, 1))

    assert [item.resource.file_name for item in ranked] == ["chinese.mkv", "english.mkv"]


def test_episode_conflicts_require_an_explicit_interactive_policy() -> None:
    conflict = MediaResource(
        provider="telegram",
        locator="conflict",
        file_name="conflict.mkv",
        file_size=100,
        identity_confidence=40,
        episode_conflict=True,
    )

    assert rank_resources([conflict], QualityPolicy(), EpisodeRange(1, 2, 2)) == []
    assert (
        rank_resources(
            [conflict],
            QualityPolicy(allow_episode_conflicts=True),
            EpisodeRange(1, 2, 2),
        )[0].resource
        == conflict
    )


def test_allowed_resolution_is_a_hard_filter() -> None:
    values = [resource("720.mkv", 720, 10), resource("1080.mkv", 1080, 20)]

    ranked = rank_resources(values, QualityPolicy(allowed_resolutions=(1080,)))

    assert [item.resource.file_name for item in ranked] == ["1080.mkv"]


def test_quality_policy_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="resolutions"):
        QualityPolicy(allowed_resolutions=(0,))
    with pytest.raises(ValueError, match="file size"):
        QualityPolicy(min_file_size=20, max_file_size=10)
