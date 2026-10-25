"""SQLAlchemy repository for discovered media resources."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from telegram_media_automation.domain.resources import (
    EpisodeRange,
    MediaResource,
    ResourceQuality,
    ResourceSource,
)
from telegram_media_automation.infrastructure.database import Database, MediaResourceRow


class SqlResourceRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _to_domain(row: MediaResourceRow) -> MediaResource:
        episode_range = (
            EpisodeRange(row.season, row.episode_start, row.episode_end)
            if row.season is not None
            and row.episode_start is not None
            and row.episode_end is not None
            else None
        )
        source = (
            ResourceSource(
                channel_id=row.source_channel_id,
                message_id=row.source_message_id,
                channel_title=row.source_channel_title,
                channel_username=row.source_channel_username,
                message_link=row.source_link,
                forwarded_from_channel_id=row.forwarded_from_channel_id,
                forwarded_from_message_id=row.forwarded_from_message_id,
                forwarded_from_title=row.forwarded_from_title,
                forwarded_from_username=row.forwarded_from_username,
                forwarded_from_link=row.forwarded_from_link,
            )
            if row.source_channel_id is not None and row.source_message_id is not None
            else None
        )
        return MediaResource(
            id=UUID(row.id),
            provider=row.provider,
            locator=row.locator,
            file_name=row.file_name,
            file_size=row.file_size,
            identity_confidence=row.identity_confidence,
            episode_range=episode_range,
            episode_conflict=row.episode_conflict,
            quality=ResourceQuality(
                resolution=row.resolution,
                codec=row.codec,
                hdr=row.hdr,
                languages=tuple(item for item in row.languages.split(",") if item),
            ),
            source=source,
        )

    @staticmethod
    def _apply(row: MediaResourceRow, resource: MediaResource) -> None:
        row.file_name = resource.file_name
        row.file_size = resource.file_size
        row.identity_confidence = resource.identity_confidence
        row.season = resource.episode_range.season if resource.episode_range else None
        row.episode_start = resource.episode_range.start if resource.episode_range else None
        row.episode_end = resource.episode_range.end if resource.episode_range else None
        row.episode_conflict = resource.episode_conflict
        row.resolution = resource.quality.resolution
        row.codec = resource.quality.codec
        row.hdr = resource.quality.hdr
        row.languages = ",".join(resource.quality.languages)
        source = resource.source
        row.source_channel_id = source.channel_id if source else None
        row.source_message_id = source.message_id if source else None
        row.source_channel_title = source.channel_title if source else None
        row.source_channel_username = source.channel_username if source else None
        row.source_link = source.message_link if source else None
        row.forwarded_from_channel_id = source.forwarded_from_channel_id if source else None
        row.forwarded_from_message_id = source.forwarded_from_message_id if source else None
        row.forwarded_from_title = source.forwarded_from_title if source else None
        row.forwarded_from_username = source.forwarded_from_username if source else None
        row.forwarded_from_link = source.forwarded_from_link if source else None
        row.discovered_at = datetime.now(UTC)

    def upsert_many(self, resources: list[MediaResource]) -> list[MediaResource]:
        stored: list[MediaResource] = []
        with self.database.session_factory.begin() as session:
            for resource in resources:
                row = session.scalar(
                    select(MediaResourceRow).where(
                        MediaResourceRow.provider == resource.provider,
                        MediaResourceRow.locator == resource.locator,
                    )
                )
                if row is None:
                    row = MediaResourceRow(
                        id=str(resource.id),
                        provider=resource.provider,
                        locator=resource.locator,
                        file_name=resource.file_name,
                        file_size=resource.file_size,
                        identity_confidence=resource.identity_confidence,
                        season=None,
                        episode_start=None,
                        episode_end=None,
                        episode_conflict=False,
                        resolution=None,
                        codec=None,
                        hdr=False,
                        languages="",
                        source_channel_id=None,
                        source_message_id=None,
                        source_channel_title=None,
                        source_channel_username=None,
                        source_link=None,
                        forwarded_from_channel_id=None,
                        forwarded_from_message_id=None,
                        forwarded_from_title=None,
                        forwarded_from_username=None,
                        forwarded_from_link=None,
                        discovered_at=datetime.now(UTC),
                    )
                    session.add(row)
                self._apply(row, resource)
                session.flush()
                stored.append(self._to_domain(row))
        return stored

    def get(self, resource_id: UUID) -> MediaResource | None:
        with self.database.session_factory() as session:
            row = session.get(MediaResourceRow, str(resource_id))
            return self._to_domain(row) if row else None

    def list(self, limit: int = 100) -> list[MediaResource]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(MediaResourceRow)
                .order_by(MediaResourceRow.discovered_at.desc())
                .limit(limit)
            ).all()
            return [self._to_domain(row) for row in rows]
