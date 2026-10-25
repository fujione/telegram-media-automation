"""Search orchestration and candidate persistence."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from telegram_media_automation.domain.resources import (
    MediaResource,
    QualityPolicy,
    rank_resources,
)
from telegram_media_automation.domain.rules.episode_parser import parse_episode


class SearchGateway(Protocol):
    async def search(
        self, channels: list[str | int], query: str, limit: int = 100
    ) -> list[MediaResource]: ...


class ResourceRepository(Protocol):
    def upsert_many(self, resources: list[MediaResource]) -> list[MediaResource]: ...

    def get(self, resource_id: UUID) -> MediaResource | None: ...

    def list(self, limit: int = 100) -> list[MediaResource]: ...


class ResourceSearchService:
    def __init__(self, gateway: SearchGateway, repository: ResourceRepository) -> None:
        self.gateway = gateway
        self.repository = repository

    async def search(
        self,
        channels: list[str | int],
        query: str,
        limit: int = 100,
        policy: QualityPolicy | None = None,
    ) -> list[MediaResource]:
        if not channels:
            raise ValueError("at least one Telegram search channel is required")
        resources = await self.gateway.search(channels, query, limit)
        stored = self.repository.upsert_many(resources)
        parsed_episode = parse_episode(query)
        requested_episode = parsed_episode.value if parsed_episode else None
        return [
            item.resource
            for item in rank_resources(
                stored,
                policy or QualityPolicy(allow_episode_conflicts=True),
                requested_episode,
            )
        ]
