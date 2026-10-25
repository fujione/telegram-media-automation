"""SQLAlchemy task repository."""

from __future__ import annotations

import builtins
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from telegram_media_automation.application.task_service import IdempotencyConflict
from telegram_media_automation.domain.events import TaskEvent
from telegram_media_automation.domain.media import ArchivePlan, MediaKind
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.domain.tasks import DownloadTask
from telegram_media_automation.infrastructure.database import (
    Database,
    DownloadTaskRow,
    IdempotencyRow,
    TaskEventRow,
)


class ConcurrentTaskUpdate(RuntimeError):
    """A task was changed after it was read."""


class SqlTaskRepository:
    def __init__(self, database: Database, *, session: Session | None = None) -> None:
        self.database = database
        self.session = session

    def _scope(self, *, write: bool = False) -> AbstractContextManager[Session]:
        if self.session is not None:
            return nullcontext(self.session)
        return self.database.session_factory.begin() if write else self.database.session_factory()

    def get_by_key(self, key: str) -> DownloadTask | None:
        with self._scope() as session:
            record = session.get(IdempotencyRow, key)
            row = session.get(DownloadTaskRow, record.task_id) if record else None
            return self._to_domain(row) if row else None

    def subscription_episode(
        self, subscription_id: UUID, season: int, episode: int
    ) -> DownloadTask | None:
        with self._scope() as session:
            row = session.scalar(
                select(DownloadTaskRow)
                .where(
                    DownloadTaskRow.request_id == str(subscription_id),
                    DownloadTaskRow.archive_kind == MediaKind.SERIES.value,
                    DownloadTaskRow.archive_season == season,
                    DownloadTaskRow.archive_episode <= episode,
                    func.coalesce(
                        DownloadTaskRow.archive_episode_end, DownloadTaskRow.archive_episode
                    )
                    >= episode,
                )
                .order_by(DownloadTaskRow.created_at)
                .limit(1)
            )
            return self._to_domain(row) if row else None

    @staticmethod
    def _to_domain(row: DownloadTaskRow) -> DownloadTask:
        return DownloadTask(
            id=UUID(row.id),
            request_id=UUID(row.request_id),
            resource_id=UUID(row.resource_id),
            state=TaskState(row.state),
            desired_state=TaskState(row.desired_state) if row.desired_state else None,
            expected_size=row.expected_size,
            downloaded_bytes=row.downloaded_bytes,
            attempt_count=row.attempt_count,
            version=row.version,
            error_code=row.error_code,
            error_message=row.error_message,
            output_path=row.output_path,
            next_attempt_at=(
                row.next_attempt_at.replace(tzinfo=row.next_attempt_at.tzinfo or UTC)
                if row.next_attempt_at
                else None
            ),
            lease_owner=row.lease_owner,
            lease_until=(
                row.lease_until.replace(tzinfo=row.lease_until.tzinfo or UTC)
                if row.lease_until
                else None
            ),
            archive_plan=(
                ArchivePlan(
                    kind=MediaKind(row.archive_kind),
                    title=row.archive_title or "",
                    year=row.archive_year,
                    season=row.archive_season,
                    episode=row.archive_episode,
                    episode_end=row.archive_episode_end,
                    confirmed_identity=bool(row.archive_confirmed),
                    replace_existing=bool(row.archive_replace_existing),
                    target_path=row.archive_target_path,
                )
                if row.archive_kind
                else None
            ),
            created_at=row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC),
            updated_at=row.updated_at.replace(tzinfo=row.updated_at.tzinfo or UTC),
        )

    @staticmethod
    def _event_row(event: TaskEvent) -> TaskEventRow:
        return TaskEventRow(
            id=str(event.id),
            task_id=str(event.task_id),
            event_type=event.event_type,
            previous_state=event.previous_state.value if event.previous_state else None,
            new_state=event.new_state.value,
            reason=event.reason,
            actor=event.actor,
            correlation_id=event.correlation_id,
            occurred_at=event.occurred_at,
        )

    def create(
        self,
        task: DownloadTask,
        event: TaskEvent,
        idempotency_key: str | None = None,
        payload_hash: str | None = None,
    ) -> DownloadTask:
        with self._scope(write=True) as session:
            if idempotency_key:
                existing = session.get(IdempotencyRow, idempotency_key)
                if existing:
                    if existing.payload_hash != payload_hash:
                        raise IdempotencyConflict(
                            "idempotency key was reused with a different payload"
                        )
                    row = session.get(DownloadTaskRow, existing.task_id)
                    if row is None:
                        raise RuntimeError("idempotency record references a missing task")
                    return self._to_domain(row)

            row = DownloadTaskRow(
                id=str(task.id),
                request_id=str(task.request_id),
                resource_id=str(task.resource_id),
                state=task.state.value,
                desired_state=None,
                expected_size=task.expected_size,
                downloaded_bytes=task.downloaded_bytes,
                attempt_count=task.attempt_count,
                version=task.version,
                error_code=None,
                error_message=None,
                output_path=None,
                next_attempt_at=None,
                lease_owner=None,
                lease_until=None,
                archive_kind=(task.archive_plan.kind.value if task.archive_plan else None),
                archive_title=(task.archive_plan.title if task.archive_plan else None),
                archive_year=(task.archive_plan.year if task.archive_plan else None),
                archive_season=(task.archive_plan.season if task.archive_plan else None),
                archive_episode=(task.archive_plan.episode if task.archive_plan else None),
                archive_episode_end=(task.archive_plan.episode_end if task.archive_plan else None),
                archive_replace_existing=(
                    task.archive_plan.replace_existing if task.archive_plan else None
                ),
                archive_target_path=(task.archive_plan.target_path if task.archive_plan else None),
                archive_confirmed=(
                    task.archive_plan.confirmed_identity if task.archive_plan else None
                ),
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            session.add(row)
            session.add(self._event_row(event))
            if idempotency_key:
                session.add(
                    IdempotencyRow(
                        key=idempotency_key,
                        payload_hash=payload_hash or "",
                        task_id=str(task.id),
                        created_at=datetime.now(UTC),
                    )
                )
            try:
                session.flush()
            except IntegrityError as error:
                raise ConcurrentTaskUpdate("task creation conflicted") from error
        return task

    def get(self, task_id: UUID) -> DownloadTask | None:
        with self._scope() as session:
            row = session.get(DownloadTaskRow, str(task_id))
            return self._to_domain(row) if row else None

    def list(self, limit: int = 100) -> builtins.list[DownloadTask]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(DownloadTaskRow).order_by(DownloadTaskRow.created_at.desc()).limit(limit)
            ).all()
            return [self._to_domain(row) for row in rows]

    def page(
        self,
        *,
        limit: int,
        offset: int = 0,
        states: tuple[TaskState, ...] = (),
    ) -> tuple[builtins.list[DownloadTask], int]:
        if limit <= 0 or offset < 0:
            raise ValueError("task page bounds are invalid")
        with self.database.session_factory() as session:
            filters = (
                (DownloadTaskRow.state.in_([state.value for state in states]),) if states else ()
            )
            total = int(
                session.scalar(select(func.count()).select_from(DownloadTaskRow).where(*filters))
                or 0
            )
            rows = session.scalars(
                select(DownloadTaskRow)
                .where(*filters)
                .order_by(DownloadTaskRow.created_at.desc(), DownloadTaskRow.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._to_domain(row) for row in rows], total

    def counts_by_state(self) -> dict[TaskState, int]:
        """Return complete queue counts without loading task rows into memory."""
        with self.database.session_factory() as session:
            rows = session.execute(
                select(DownloadTaskRow.state, func.count()).group_by(DownloadTaskRow.state)
            ).all()
            return {TaskState(state): int(count) for state, count in rows}

    def claim_next(self, worker_id: str, lease_seconds: float) -> DownloadTask | None:
        if not worker_id or len(worker_id) > 100:
            raise ValueError("worker ID must contain 1 to 100 characters")
        if lease_seconds <= 0:
            raise ValueError("lease duration must be positive")
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self.database.session_factory.begin() as session:
            expired = session.scalars(
                select(DownloadTaskRow).where(
                    DownloadTaskRow.state == TaskState.DOWNLOADING.value,
                    DownloadTaskRow.lease_until.is_not(None),
                    DownloadTaskRow.lease_until <= now,
                )
            ).all()
            for row in expired:
                previous_version = row.version
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(DownloadTaskRow)
                        .where(
                            DownloadTaskRow.id == row.id,
                            DownloadTaskRow.version == previous_version,
                            DownloadTaskRow.state == TaskState.DOWNLOADING.value,
                        )
                        .values(
                            state=TaskState.RETRYING.value,
                            version=previous_version + 1,
                            error_code="worker_lease_expired",
                            error_message="download worker lease expired",
                            next_attempt_at=now,
                            lease_owner=None,
                            lease_until=None,
                            updated_at=now,
                        )
                    ),
                )
                if result.rowcount == 1:
                    session.add(
                        self._event_row(
                            TaskEvent(
                                task_id=UUID(row.id),
                                event_type="task.state_changed",
                                previous_state=TaskState.DOWNLOADING,
                                new_state=TaskState.RETRYING,
                                reason="download worker lease expired",
                                actor="lease-reconciliation",
                            )
                        )
                    )

            candidates = session.scalars(
                select(DownloadTaskRow)
                .where(
                    (DownloadTaskRow.state.in_([TaskState.QUEUED.value, TaskState.RETRYING.value]))
                    | (
                        (DownloadTaskRow.state == TaskState.DOWNLOADED.value)
                        & DownloadTaskRow.archive_kind.is_not(None)
                    ),
                    (DownloadTaskRow.next_attempt_at.is_(None))
                    | (DownloadTaskRow.next_attempt_at <= now),
                )
                .order_by(DownloadTaskRow.created_at)
            ).all()
            for row in candidates:
                task = self._to_domain(row)
                expected_version = task.version
                previous = task.state
                if task.state == TaskState.DOWNLOADED:
                    task.transition(TaskState.RETRYING)
                task.transition(TaskState.DOWNLOADING)
                task.lease_owner = worker_id
                task.lease_until = lease_until
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(DownloadTaskRow)
                        .where(
                            DownloadTaskRow.id == row.id,
                            DownloadTaskRow.version == expected_version,
                            DownloadTaskRow.state == previous.value,
                        )
                        .values(
                            state=task.state.value,
                            attempt_count=task.attempt_count,
                            version=task.version,
                            error_code=None,
                            error_message=None,
                            next_attempt_at=None,
                            lease_owner=worker_id,
                            lease_until=lease_until,
                            updated_at=task.updated_at,
                        )
                    ),
                )
                if result.rowcount == 1:
                    session.add(
                        self._event_row(
                            TaskEvent(
                                task_id=task.id,
                                event_type="task.state_changed",
                                previous_state=previous,
                                new_state=TaskState.DOWNLOADING,
                                actor="download-worker",
                            )
                        )
                    )
                    return task
        return None

    def renew_lease(self, task_id: UUID, worker_id: str, lease_seconds: float) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease duration must be positive")
        with self.database.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(DownloadTaskRow)
                    .where(
                        DownloadTaskRow.id == str(task_id),
                        DownloadTaskRow.state == TaskState.DOWNLOADING.value,
                        DownloadTaskRow.lease_owner == worker_id,
                    )
                    .values(lease_until=datetime.now(UTC) + timedelta(seconds=lease_seconds))
                ),
            )
            return result.rowcount == 1

    def save_transition(
        self, task: DownloadTask, expected_version: int, event: TaskEvent
    ) -> DownloadTask:
        with self._scope(write=True) as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(DownloadTaskRow)
                    .where(
                        DownloadTaskRow.id == str(task.id),
                        DownloadTaskRow.version == expected_version,
                    )
                    .values(
                        state=task.state.value,
                        desired_state=(task.desired_state.value if task.desired_state else None),
                        downloaded_bytes=task.downloaded_bytes,
                        attempt_count=task.attempt_count,
                        version=task.version,
                        updated_at=task.updated_at,
                        error_code=task.error_code,
                        error_message=task.error_message,
                        output_path=task.output_path,
                        archive_kind=task.archive_plan.kind.value if task.archive_plan else None,
                        archive_title=task.archive_plan.title if task.archive_plan else None,
                        archive_year=task.archive_plan.year if task.archive_plan else None,
                        archive_season=task.archive_plan.season if task.archive_plan else None,
                        archive_episode=task.archive_plan.episode if task.archive_plan else None,
                        archive_episode_end=task.archive_plan.episode_end
                        if task.archive_plan
                        else None,
                        archive_confirmed=task.archive_plan.confirmed_identity
                        if task.archive_plan
                        else None,
                        archive_replace_existing=task.archive_plan.replace_existing
                        if task.archive_plan
                        else None,
                        archive_target_path=task.archive_plan.target_path
                        if task.archive_plan
                        else None,
                        next_attempt_at=task.next_attempt_at,
                        lease_owner=task.lease_owner,
                        lease_until=task.lease_until,
                    )
                ),
            )
            if result.rowcount != 1:
                raise ConcurrentTaskUpdate(str(task.id))
            session.add(self._event_row(event))
        return task

    def save_progress(self, task: DownloadTask, expected_version: int) -> DownloadTask:
        with self.database.session_factory.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(DownloadTaskRow)
                    .where(
                        DownloadTaskRow.id == str(task.id),
                        DownloadTaskRow.version == expected_version,
                    )
                    .values(
                        downloaded_bytes=task.downloaded_bytes,
                        version=task.version,
                        updated_at=task.updated_at,
                        lease_owner=task.lease_owner,
                        lease_until=task.lease_until,
                    )
                ),
            )
            if result.rowcount != 1:
                raise ConcurrentTaskUpdate(str(task.id))
        return task

    def events(self, task_id: UUID) -> builtins.list[TaskEvent]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(TaskEventRow)
                .where(TaskEventRow.task_id == str(task_id))
                .order_by(TaskEventRow.occurred_at, literal_column("task_events.rowid"))
            ).all()
            return [
                TaskEvent(
                    id=UUID(row.id),
                    task_id=UUID(row.task_id),
                    event_type=row.event_type,
                    previous_state=TaskState(row.previous_state) if row.previous_state else None,
                    new_state=TaskState(row.new_state),
                    reason=row.reason,
                    actor=row.actor,
                    correlation_id=row.correlation_id,
                    occurred_at=row.occurred_at.replace(tzinfo=row.occurred_at.tzinfo or UTC),
                )
                for row in rows
            ]

    def ping(self) -> bool:
        return self.database.ping()
