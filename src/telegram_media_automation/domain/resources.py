"""Downloadable media resources and deterministic candidate scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(frozen=True, order=True, slots=True)
class EpisodeRange:
    season: int
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.season < 0 or self.start <= 0 or self.end < self.start:
            raise ValueError("invalid episode range")

    def covers(self, other: EpisodeRange) -> bool:
        return self.season == other.season and self.start <= other.start and self.end >= other.end


@dataclass(frozen=True, slots=True)
class ResourceQuality:
    resolution: int | None = None
    codec: str | None = None
    hdr: bool = False
    languages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceSource:
    """Human-auditable source metadata captured when a resource is discovered."""

    channel_id: int
    message_id: int
    channel_title: str | None = None
    channel_username: str | None = None
    message_link: str | None = None
    forwarded_from_channel_id: int | None = None
    forwarded_from_message_id: int | None = None
    forwarded_from_title: str | None = None
    forwarded_from_username: str | None = None
    forwarded_from_link: str | None = None

    def __post_init__(self) -> None:
        if self.channel_id == 0 or self.message_id <= 0:
            raise ValueError("resource source requires a channel and message ID")


@dataclass(frozen=True, slots=True)
class MediaResource:
    provider: str
    locator: str
    file_name: str
    file_size: int
    identity_confidence: int
    episode_range: EpisodeRange | None = None
    episode_conflict: bool = False
    quality: ResourceQuality = field(default_factory=ResourceQuality)
    source: ResourceSource | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.provider or not self.locator or not self.file_name:
            raise ValueError("resource provider, locator and file name are required")
        if self.file_size < 0:
            raise ValueError("resource size cannot be negative")
        if not 0 <= self.identity_confidence <= 100:
            raise ValueError("identity confidence must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    preferred_resolutions: tuple[int, ...] = (2160, 1080, 720)
    allowed_resolutions: tuple[int, ...] = ()
    preferred_languages: tuple[str, ...] = ("zh", "en")
    allowed_languages: tuple[str, ...] = ()
    min_file_size: int = 0
    max_file_size: int | None = None
    prefer_hdr: bool = False
    preferred_codecs: tuple[str, ...] = ("av1", "hevc", "h265", "h264")
    allow_episode_conflicts: bool = False

    def __post_init__(self) -> None:
        if any(value <= 0 for value in (*self.preferred_resolutions, *self.allowed_resolutions)):
            raise ValueError("quality policy resolutions must be positive")
        if self.min_file_size < 0 or (
            self.max_file_size is not None and self.max_file_size < self.min_file_size
        ):
            raise ValueError("quality policy file size range is invalid")


@dataclass(frozen=True, slots=True)
class ScoredResource:
    resource: MediaResource
    score: tuple[int, ...]
    reasons: tuple[str, ...]


def score_resource(
    resource: MediaResource,
    policy: QualityPolicy,
    requested_episode: EpisodeRange | None = None,
) -> ScoredResource | None:
    if resource.episode_conflict and not policy.allow_episode_conflicts:
        return None
    if (
        requested_episode is not None
        and not resource.episode_conflict
        and (resource.episode_range is None or not resource.episode_range.covers(requested_episode))
    ):
        return None
    if resource.file_size < policy.min_file_size:
        return None
    if policy.max_file_size is not None and resource.file_size > policy.max_file_size:
        return None

    normalized_languages = {value.casefold() for value in resource.quality.languages}
    required_languages = {value.casefold() for value in policy.allowed_languages}
    if required_languages and not normalized_languages.intersection(required_languages):
        return None
    preferred_languages = tuple(value.casefold() for value in policy.preferred_languages)
    language_rank = max(
        (
            len(preferred_languages) - preferred_languages.index(language)
            for language in normalized_languages
            if language in preferred_languages
        ),
        default=0,
    )

    resolution = resource.quality.resolution or 0
    if policy.allowed_resolutions and resolution not in policy.allowed_resolutions:
        return None
    try:
        resolution_rank = len(policy.preferred_resolutions) - policy.preferred_resolutions.index(
            resolution
        )
    except ValueError:
        resolution_rank = 0

    codec = (resource.quality.codec or "").casefold()
    try:
        codec_rank = len(policy.preferred_codecs) - policy.preferred_codecs.index(codec)
    except ValueError:
        codec_rank = 0

    coverage_width = (
        resource.episode_range.end - resource.episode_range.start + 1
        if resource.episode_range is not None
        else 0
    )
    hdr_rank = int(resource.quality.hdr and policy.prefer_hdr)
    score = (
        resource.identity_confidence,
        int(not resource.episode_conflict),
        int(requested_episode is None or resource.episode_range is not None),
        language_rank,
        resolution_rank,
        hdr_rank,
        codec_rank,
        -coverage_width,
        -resource.file_size,
    )
    reasons = (
        f"identity={resource.identity_confidence}",
        f"languages={','.join(sorted(normalized_languages)) or 'unknown'}",
        f"resolution={resolution or 'unknown'}",
        f"hdr={resource.quality.hdr}",
        f"codec={codec or 'unknown'}",
        f"size={resource.file_size}",
    )
    return ScoredResource(resource=resource, score=score, reasons=reasons)


def rank_resources(
    resources: list[MediaResource],
    policy: QualityPolicy,
    requested_episode: EpisodeRange | None = None,
) -> list[ScoredResource]:
    scored = [score_resource(item, policy, requested_episode) for item in resources]
    accepted = [item for item in scored if item is not None]
    return sorted(accepted, key=lambda item: (item.score, str(item.resource.id)), reverse=True)
