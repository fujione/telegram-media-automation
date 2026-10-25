"""Task lifecycle application service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from telegram_media_automation.application.ports import TaskRepository
from telegram_media_automation.domain.events import TaskEvent
from telegram_media_automation.domain.media import ArchivePlan
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.domain.tasks import DownloadTask


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different command."""


class TaskNotFound(LookupError):
    """Requested task does not exist."""


class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def create_task(
        self,
        request_id: UUID,
        resource_id: UUID,
        expected_size: int,
        idempotency_key: str | None = None,
        archive_plan: ArchivePlan | None = None,
    ) -> DownloadTask:
        task = DownloadTask(
            request_id=request_id,
            resource_id=resource_id,
            expected_size=expected_size,
            archive_plan=archive_plan,
        )
        payload = {
            "request_id": str(request_id),
            "resource_id": str(resource_id),
            "expected_size": expected_size,
            "archive_plan": (
                {
                    "kind": archive_plan.kind.value,
                    "title": archive_plan.title,
                    "year": archive_plan.year,
                    "season": archive_plan.season,
                    "episode": archive_plan.episode,
                    "episode_end": archive_plan.episode_end,
                    "confirmed_identity": archive_plan.confirmed_identity,
                    **(
                        {"replace_existing": True, "target_path": archive_plan.target_path}
                        if archive_plan.replace_existing
                        else {}
                    ),
                }
                if archive_plan
                else None
            ),
        }
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event = TaskEvent(
            task_id=task.id,
            event_type="task.created",
            new_state=TaskState.PENDING,
            actor="api",
            correlation_id=idempotency_key,
        )
        return self.repository.create(task, event, idempotency_key, payload_hash)

    def transition(
        self,
        task_id: UUID,
        target: TaskState,
        *,
        reason: str | None = None,
        actor: str = "api",
        error_code: str | None = None,
        error_message: str | None = None,
        output_path: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> DownloadTask:
        task = self.repository.get(task_id)
        if task is None:
            raise TaskNotFound(str(task_id))
        expected_version = task.version
        previous = task.transition(target)
        if target in {TaskState.FAILED, TaskState.RETRYING}:
            task.error_code = error_code
            task.error_message = error_message or reason
            task.next_attempt_at = (
                datetime.now(UTC) + timedelta(seconds=retry_after_seconds)
                if target == TaskState.RETRYING and retry_after_seconds is not None
                else None
            )
        elif target == TaskState.DOWNLOADING:
            task.error_code = None
            task.error_message = None
            task.next_attempt_at = None
        if output_path is not None:
            task.output_path = output_path
        event = TaskEvent(
            task_id=task.id,
            event_type="task.state_changed",
            previous_state=previous,
            new_state=task.state,
            reason=reason,
            actor=actor,
        )
        return self.repository.save_transition(task, expected_version, event)

    def set_replacement_plan(self, task_id: UUID, plan: ArchivePlan) -> DownloadTask:
        if not plan.replace_existing or not plan.confirmed_identity:
            raise ValueError("a confirmed replacement plan is required")
        task = self.repository.get(task_id)
        if task is None:
            raise TaskNotFound(str(task_id))
        if task.state not in {
            TaskState.QUEUED,
            TaskState.DOWNLOADING,
            TaskState.PAUSED,
            TaskState.RETRYING,
            TaskState.DOWNLOADED,
        }:
            raise ValueError("task cannot accept a replacement plan in its current state")
        expected_version = task.version
        previous = task.state
        task.archive_plan = plan
        if task.state == TaskState.DOWNLOADED:
            task.transition(TaskState.RETRYING)
            task.next_attempt_at = None
        else:
            task.version += 1
            task.updated_at = datetime.now(UTC)
        return self.repository.save_transition(
            task,
            expected_version,
            TaskEvent(
                task_id=task.id,
                event_type="task.replacement_plan_updated",
                previous_state=previous,
                new_state=task.state,
                reason="replacement scheduled after download",
                actor="api",
            ),
        )

    def record_progress(self, task_id: UUID, downloaded_bytes: int) -> DownloadTask:
        task = self.repository.get(task_id)
        if task is None:
            raise TaskNotFound(str(task_id))
        expected_version = task.version
        task.record_progress(downloaded_bytes)
        if task.version == expected_version:
            return task
        return self.repository.save_progress(task, expected_version)

    def reconcile_progress(self, task_id: UUID, actual_bytes: int) -> DownloadTask:
        task = self.repository.get(task_id)
        if task is None:
            raise TaskNotFound(str(task_id))
        expected_version = task.version
        task.reconcile_progress(actual_bytes)
        if task.version == expected_version:
            return task
        return self.repository.save_progress(task, expected_version)

    def describe(self, task: DownloadTask) -> dict[str, object]:
        data = asdict(task)
        return {
            key: str(value) if isinstance(value, UUID) else value for key, value in data.items()
        }
