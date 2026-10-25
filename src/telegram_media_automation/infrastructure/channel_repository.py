"""Persistence for configured Telegram search scopes."""

from __future__ import annotations

import builtins
from datetime import UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from telegram_media_automation.domain.channels import SearchChannel
from telegram_media_automation.infrastructure.database import Database, SearchChannelRow


class DuplicateSearchChannel(ValueError):
    pass


class SqlSearchChannelRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _domain(row: SearchChannelRow) -> SearchChannel:
        return SearchChannel(
            id=UUID(row.id),
            identifier=row.identifier,
            name=row.name,
            enabled=row.enabled,
            created_at=row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC),
        )

    def create(self, channel: SearchChannel) -> SearchChannel:
        if any(existing.identifier == channel.identifier for existing in self.list()):
            raise DuplicateSearchChannel(channel.identifier)
        try:
            with self.database.session_factory.begin() as session:
                session.add(
                    SearchChannelRow(
                        id=str(channel.id),
                        identifier=channel.identifier,
                        name=channel.name,
                        enabled=channel.enabled,
                        created_at=channel.created_at,
                    )
                )
        except IntegrityError as error:
            raise DuplicateSearchChannel(channel.identifier) from error
        return channel

    def list(self) -> builtins.list[SearchChannel]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(SearchChannelRow).order_by(SearchChannelRow.created_at)
            ).all()
            return [self._domain(row) for row in rows]

    def enabled_identifiers(self) -> builtins.list[str]:
        with self.database.session_factory() as session:
            return list(
                session.scalars(
                    select(SearchChannelRow.identifier)
                    .where(SearchChannelRow.enabled.is_(True))
                    .order_by(SearchChannelRow.created_at)
                ).all()
            )

    def set_enabled(self, channel_id: UUID, enabled: bool) -> SearchChannel | None:
        with self.database.session_factory.begin() as session:
            row = session.get(SearchChannelRow, str(channel_id))
            if row is None:
                return None
            row.enabled = enabled
            session.flush()
            return self._domain(row)

    def delete(self, channel_id: UUID) -> bool:
        with self.database.session_factory.begin() as session:
            row = session.get(SearchChannelRow, str(channel_id))
            if row is None:
                return False
            session.delete(row)
            return True
