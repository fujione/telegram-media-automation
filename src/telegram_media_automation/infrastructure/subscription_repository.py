"""SQLite persistence for media subscriptions and per-channel cursors."""

from __future__ import annotations

import builtins
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select, true, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.domain.media import ArchivePlan, MediaKind
from telegram_media_automation.domain.resources import MediaResource
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.domain.subscriptions import Subscription
from telegram_media_automation.domain.tasks import DownloadTask
from telegram_media_automation.infrastructure.database import (
    Database,
    SubscriptionCandidateRow,
    SubscriptionCursorRow,
    SubscriptionRow,
)
from telegram_media_automation.infrastructure.scan_repository import SqlScanRepository
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository


class ConcurrentSubscriptionUpdate(RuntimeError):
    pass


class SqlSubscriptionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _guard(
        session: Session, subscription_id: UUID, version: int, *, require_enabled: bool = True
    ) -> bool:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(SubscriptionRow)
                .where(
                    SubscriptionRow.id == str(subscription_id),
                    SubscriptionRow.version == version,
                    SubscriptionRow.enabled.is_(True) if require_enabled else true(),
                )
                .values(version=version)
            ),
        )
        return result.rowcount == 1

    def edit(self, replacement: Subscription, expected_version: int) -> Subscription:
        with self.database.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(SubscriptionRow)
                    .where(
                        SubscriptionRow.id == str(replacement.id),
                        SubscriptionRow.version == expected_version,
                    )
                    .values(version=expected_version)
                ),
            )
            if result.rowcount != 1:
                raise ConcurrentSubscriptionUpdate(str(replacement.id))
            replacement.updated_at = datetime.now(UTC)
            previous = session.get(SubscriptionRow, str(replacement.id))
            assert previous is not None
            reread = (
                previous.query != replacement.query
                or previous.season != replacement.season
                or previous.next_episode != replacement.next_episode
                or tuple(json.loads(previous.pending_episodes or "[]"))
                != replacement.pending_episodes
                or previous.continue_tracking != replacement.continue_tracking
            )
            scope = SubscriptionCursorRow.subscription_id == str(replacement.id)
            candidates = SubscriptionCandidateRow.subscription_id == str(replacement.id)
            if reread:
                session.execute(delete(SubscriptionCandidateRow).where(candidates))
                session.execute(delete(SubscriptionCursorRow).where(scope))
            elif replacement.channels:
                # Only a scope change: preserve the intersection and its history.
                session.execute(
                    delete(SubscriptionCandidateRow).where(
                        candidates, SubscriptionCandidateRow.channel.not_in(replacement.channels)
                    )
                )
                session.execute(
                    delete(SubscriptionCursorRow).where(
                        scope, SubscriptionCursorRow.channel.not_in(replacement.channels)
                    )
                )
            for channel in replacement.channels:
                if session.get(SubscriptionCursorRow, (str(replacement.id), channel)) is None:
                    session.add(
                        SubscriptionCursorRow(
                            subscription_id=str(replacement.id),
                            channel=channel,
                            last_message_id=0,
                            updated_at=replacement.updated_at,
                        )
                    )
            session.execute(
                update(SubscriptionRow)
                .where(SubscriptionRow.id == str(replacement.id))
                .values(
                    title=replacement.title,
                    query=replacement.query,
                    season=replacement.season,
                    next_episode=replacement.next_episode,
                    pending_episodes=json.dumps(replacement.pending_episodes),
                    continue_tracking=replacement.continue_tracking,
                    use_default_channels=not replacement.channels,
                    version=expected_version + 1,
                    updated_at=replacement.updated_at,
                )
            )
        replacement.version = expected_version + 1
        return replacement

    def record_scan(
        self,
        subscription_id: UUID,
        version: int,
        channel: str,
        resource_ids: builtins.list[UUID],
        message_id: int,
    ) -> bool:
        with self.database.session_factory.begin() as session:
            if not self._guard(session, subscription_id, version):
                return False
            cursor = session.get(SubscriptionCursorRow, (str(subscription_id), channel))
            if cursor is None:
                return False
            for resource_id in resource_ids:
                if (
                    session.get(SubscriptionCandidateRow, (str(subscription_id), str(resource_id)))
                    is None
                ):
                    session.add(
                        SubscriptionCandidateRow(
                            subscription_id=str(subscription_id),
                            resource_id=str(resource_id),
                            channel=channel,
                            discovered_at=datetime.now(UTC),
                        )
                    )
            cursor.last_message_id = max(cursor.last_message_id, message_id)
            cursor.updated_at = datetime.now(UTC)
            return True

    def enqueue(self, subscription: Subscription, selected: MediaResource) -> DownloadTask | None:
        with self.database.session_factory.begin() as session:
            if not self._guard(session, subscription.id, subscription.version):
                return None
            target = subscription.target_episode
            assert target is not None
            key = f"subscription:{subscription.id}:s{subscription.season}e{target}"
            repository = SqlTaskRepository(self.database, session=session)
            service = TaskService(repository)
            # A deliberately revised plan may include an episode already queued.
            # Reuse its task regardless of the newly selected resource or title.
            task = repository.get_by_key(key) or repository.subscription_episode(
                subscription.id, subscription.season, target
            )
            if task is None:
                task = service.create_task(
                    subscription.id,
                    selected.id,
                    selected.file_size,
                    key,
                    ArchivePlan(
                        kind=MediaKind.SERIES,
                        title=subscription.title,
                        season=subscription.season,
                        episode=target,
                        episode_end=selected.episode_range.end
                        if selected.episode_range
                        else target,
                        confirmed_identity=True,
                    ),
                )
                SqlScanRepository.mark_enqueued(
                    session, subscription.id, subscription.version, task.id, task.created_at
                )
            if task.state == TaskState.PENDING:
                task = service.transition(
                    task.id, TaskState.CANDIDATE_SELECTED, actor="subscription-worker"
                )
            if task.state == TaskState.CANDIDATE_SELECTED:
                task = service.transition(task.id, TaskState.QUEUED, actor="subscription-worker")
            if task.state in {TaskState.FAILED, TaskState.CANCELLED}:
                return task
            plan = task.archive_plan
            subscription.complete_range(
                plan.episode or target if plan else target,
                plan.episode_end or plan.episode or target if plan else target,
            )
            session.execute(
                update(SubscriptionRow)
                .where(SubscriptionRow.id == str(subscription.id))
                .values(
                    next_episode=subscription.next_episode,
                    pending_episodes=json.dumps(subscription.pending_episodes),
                    enabled=subscription.enabled,
                    version=subscription.version,
                    updated_at=subscription.updated_at,
                )
            )
            return task

    @staticmethod
    def _domain(row: SubscriptionRow, channels: tuple[str, ...]) -> Subscription:
        return Subscription(
            id=UUID(row.id),
            title=row.title,
            season=row.season,
            next_episode=row.next_episode,
            pending_episodes=tuple(json.loads(row.pending_episodes or "[]")),
            continue_tracking=row.continue_tracking,
            query=row.query,
            channels=() if row.use_default_channels else channels,
            enabled=row.enabled,
            version=row.version,
            created_at=row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC),
            updated_at=row.updated_at.replace(tzinfo=row.updated_at.tzinfo or UTC),
        )

    def create(self, subscription: Subscription) -> Subscription:
        with self.database.session_factory.begin() as session:
            session.add(
                SubscriptionRow(
                    id=str(subscription.id),
                    title=subscription.title,
                    season=subscription.season,
                    next_episode=subscription.next_episode,
                    pending_episodes=json.dumps(subscription.pending_episodes),
                    continue_tracking=subscription.continue_tracking,
                    query=subscription.query,
                    use_default_channels=not subscription.channels,
                    enabled=subscription.enabled,
                    version=subscription.version,
                    created_at=subscription.created_at,
                    updated_at=subscription.updated_at,
                )
            )
            session.flush()
            session.add_all(
                SubscriptionCursorRow(
                    subscription_id=str(subscription.id),
                    channel=channel,
                    last_message_id=0,
                    updated_at=subscription.created_at,
                )
                for channel in subscription.channels
            )
        return subscription

    def _channels(self, session: Session, subscription_id: UUID) -> tuple[str, ...]:
        rows = session.scalars(
            select(SubscriptionCursorRow.channel)
            .where(SubscriptionCursorRow.subscription_id == str(subscription_id))
            .order_by(SubscriptionCursorRow.channel)
        ).all()
        return tuple(rows)

    def get(self, subscription_id: UUID) -> Subscription | None:
        with self.database.session_factory() as session:
            row = session.get(SubscriptionRow, str(subscription_id))
            return self._domain(row, self._channels(session, subscription_id)) if row else None

    def list_enabled(self) -> list[Subscription]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(SubscriptionRow)
                .where(SubscriptionRow.enabled.is_(True))
                .order_by(SubscriptionRow.created_at)
            ).all()
            return [self._domain(row, self._channels(session, UUID(row.id))) for row in rows]

    def list(self, limit: int = 500) -> list[Subscription]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(SubscriptionRow).order_by(SubscriptionRow.created_at.desc()).limit(limit)
            ).all()
            return [self._domain(row, self._channels(session, UUID(row.id))) for row in rows]

    def cursors(self, subscription_id: UUID) -> dict[str, int]:
        with self.database.session_factory() as session:
            rows = session.execute(
                select(SubscriptionCursorRow.channel, SubscriptionCursorRow.last_message_id).where(
                    SubscriptionCursorRow.subscription_id == str(subscription_id)
                )
            ).all()
            return {row[0]: row[1] for row in rows}

    def advance_cursor(self, subscription_id: UUID, channel: str, message_id: int) -> None:
        with self.database.session_factory.begin() as session:
            row = session.get(SubscriptionCursorRow, (str(subscription_id), channel))
            if row is None:
                raise LookupError(f"subscription channel not found: {channel}")
            if message_id > row.last_message_id:
                row.last_message_id = message_id
                row.updated_at = datetime.now(UTC)

    def ensure_channels(
        self, subscription_id: UUID, channels: tuple[str, ...], expected_version: int | None = None
    ) -> None:
        with self.database.session_factory.begin() as session:
            if expected_version is not None and not self._guard(
                session, subscription_id, expected_version
            ):
                return
            subscription = session.get(SubscriptionRow, str(subscription_id))
            if subscription is None or not subscription.use_default_channels:
                return
            for channel in channels:
                key = (str(subscription_id), channel)
                if session.get(SubscriptionCursorRow, key) is None:
                    session.add(
                        SubscriptionCursorRow(
                            subscription_id=str(subscription_id),
                            channel=channel,
                            last_message_id=0,
                            updated_at=datetime.now(UTC),
                        )
                    )

    def add_candidates(
        self, subscription_id: UUID, channel: str, resource_ids: builtins.list[UUID]
    ) -> None:
        with self.database.session_factory.begin() as session:
            for resource_id in resource_ids:
                key = (str(subscription_id), str(resource_id))
                if session.get(SubscriptionCandidateRow, key) is None:
                    session.add(
                        SubscriptionCandidateRow(
                            subscription_id=str(subscription_id),
                            resource_id=str(resource_id),
                            channel=channel,
                            discovered_at=datetime.now(UTC),
                        )
                    )

    def candidate_ids(
        self, subscription_id: UUID, channels: tuple[str, ...] | None = None
    ) -> builtins.list[UUID]:
        with self.database.session_factory() as session:
            statement = (
                select(SubscriptionCandidateRow.resource_id)
                .where(SubscriptionCandidateRow.subscription_id == str(subscription_id))
                .order_by(SubscriptionCandidateRow.discovered_at)
            )
            if channels is not None:
                statement = statement.where(SubscriptionCandidateRow.channel.in_(channels))
            values = session.scalars(statement).all()
            return [UUID(value) for value in values]

    def remove_candidate(
        self, subscription_id: UUID, resource_id: UUID, expected_version: int | None = None
    ) -> None:
        with self.database.session_factory.begin() as session:
            if expected_version is not None and not self._guard(
                session, subscription_id, expected_version, require_enabled=False
            ):
                return
            row = session.get(SubscriptionCandidateRow, (str(subscription_id), str(resource_id)))
            if row is not None:
                session.delete(row)

    def delete(self, subscription_id: UUID) -> bool:
        with self.database.session_factory.begin() as session:
            row = session.get(SubscriptionRow, str(subscription_id))
            if row is None:
                return False
            session.delete(row)
            return True

    def save(self, subscription: Subscription, expected_version: int) -> Subscription:
        with self.database.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(SubscriptionRow)
                    .where(
                        SubscriptionRow.id == str(subscription.id),
                        SubscriptionRow.version == expected_version,
                    )
                    .values(
                        next_episode=subscription.next_episode,
                        pending_episodes=json.dumps(subscription.pending_episodes),
                        continue_tracking=subscription.continue_tracking,
                        enabled=subscription.enabled,
                        version=subscription.version,
                        updated_at=subscription.updated_at,
                    )
                ),
            )
            if result.rowcount != 1:
                raise ConcurrentSubscriptionUpdate(str(subscription.id))
        return subscription
