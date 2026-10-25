"""Media identity entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4


class MediaKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"


class IdentityStatus(StrEnum):
    CONFIRMED = "confirmed"
    PROVISIONAL = "provisional"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class ExternalId:
    provider: str
    value: str

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.value.strip():
            raise ValueError("external id provider and value are required")


@dataclass(slots=True)
class MediaItem:
    kind: MediaKind
    title: str
    year: int | None = None
    original_title: str | None = None
    aliases: tuple[str, ...] = ()
    external_ids: tuple[ExternalId, ...] = ()
    identity_status: IdentityStatus = IdentityStatus.PROVISIONAL
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("media title is required")
        if self.year is not None and not 1870 <= self.year <= 2200:
            raise ValueError("media year is outside the supported range")


@dataclass(frozen=True, slots=True)
class Season:
    series_id: UUID
    number: int

    def __post_init__(self) -> None:
        if self.number < 0:
            raise ValueError("season number cannot be negative")


@dataclass(frozen=True, slots=True)
class Episode:
    series_id: UUID
    season_number: int
    episode_number: int
    title: str | None = None

    def __post_init__(self) -> None:
        if self.season_number < 0 or self.episode_number <= 0:
            raise ValueError("episode numbers must be positive; season may be zero")


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    kind: MediaKind
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    confirmed_identity: bool = True
    replace_existing: bool = False
    target_path: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("archive title is required")
        if self.replace_existing and not self.confirmed_identity:
            raise ValueError("replacement requires confirmed identity")
        if self.target_path is not None and not self.replace_existing:
            raise ValueError("explicit target paths require replacement")
        if self.year is not None and not 1870 <= self.year <= 2200:
            raise ValueError("archive year is outside the supported range")
        if self.kind == MediaKind.MOVIE and (
            self.season is not None or self.episode is not None or self.episode_end is not None
        ):
            raise ValueError("movie archive plans cannot specify season or episode")
        if self.kind == MediaKind.SERIES and (
            self.season is None or self.episode is None or self.season < 0 or self.episode <= 0
        ):
            raise ValueError("series archive plans require a valid season and episode")
        if self.episode_end is not None and (
            self.episode is None or self.episode_end < self.episode
        ):
            raise ValueError("archive episode range is invalid")
