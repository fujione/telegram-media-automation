"""Media-specific subscriptions; never generic channel mirroring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(slots=True)
class Subscription:
    title: str
    season: int
    next_episode: int
    query: str
    channels: tuple[str, ...]
    pending_episodes: tuple[int, ...] = ()
    continue_tracking: bool = True
    id: UUID = field(default_factory=uuid4)
    enabled: bool = True
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        self.query = self.query.strip()
        self.channels = tuple(
            dict.fromkeys(value.strip() for value in self.channels if value.strip())
        )
        if not self.title or not self.query:
            raise ValueError("subscription title and query are required")
        if self.season < 0 or self.next_episode <= 0:
            raise ValueError("subscription season or next episode is invalid")
        # An empty scope follows the currently enabled default channel list.
        self.pending_episodes = tuple(sorted(set(self.pending_episodes)))
        if any(episode <= 0 for episode in self.pending_episodes):
            raise ValueError("pending episodes must be positive")
        if self.enabled and not self.continue_tracking and not self.pending_episodes:
            raise ValueError("a fill-only subscription requires pending episodes")

    @property
    def target_episode(self) -> int | None:
        if self.pending_episodes:
            return self.pending_episodes[0]
        return self.next_episode if self.continue_tracking else None

    def accepts_range(self, start: int, end: int) -> bool:
        if any(start <= episode <= end for episode in self.pending_episodes):
            return True
        return self.continue_tracking and end >= self.next_episode

    def complete_range(self, start: int, end: int) -> None:
        if start <= 0 or end < start:
            raise ValueError("completed episode range is invalid")
        pending = tuple(episode for episode in self.pending_episodes if not start <= episode <= end)
        next_episode = self.next_episode
        if self.continue_tracking and start <= next_episode <= end:
            next_episode = end + 1
        enabled = self.enabled
        if not self.continue_tracking and not pending:
            enabled = False
        if (
            pending != self.pending_episodes
            or next_episode != self.next_episode
            or enabled != self.enabled
        ):
            self.pending_episodes = pending
            self.next_episode = next_episode
            self.enabled = enabled
            self.version += 1
            self.updated_at = datetime.now(UTC)

    def advance(self) -> int:
        previous = self.next_episode
        self.next_episode += 1
        self.version += 1
        self.updated_at = datetime.now(UTC)
        return previous

    def advance_to(self, next_episode: int) -> int:
        if next_episode <= self.next_episode:
            raise ValueError("next episode must move forward")
        previous = self.next_episode
        self.next_episode = next_episode
        self.version += 1
        self.updated_at = datetime.now(UTC)
        return previous

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self.continue_tracking and not self.pending_episodes:
            raise ValueError("fill-only subscription plan is complete")
        if self.enabled != enabled:
            self.enabled = enabled
            self.version += 1
            self.updated_at = datetime.now(UTC)
