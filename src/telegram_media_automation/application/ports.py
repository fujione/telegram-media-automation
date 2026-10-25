"""Ports owned by the application layer."""

from __future__ import annotations

import builtins
from typing import Protocol
from uuid import UUID

from telegram_media_automation.domain.events import TaskEvent
from telegram_media_automation.domain.tasks import DownloadTask


class TaskRepository(Protocol):
    def create(
        self,
        task: DownloadTask,
        event: TaskEvent,
        idempotency_key: str | None = None,
        payload_hash: str | None = None,
    ) -> DownloadTask: ...

    def get(self, task_id: UUID) -> DownloadTask | None: ...

    def list(self, limit: int = 100) -> builtins.list[DownloadTask]: ...

    def claim_next(self, worker_id: str, lease_seconds: float) -> DownloadTask | None: ...

    def renew_lease(self, task_id: UUID, worker_id: str, lease_seconds: float) -> bool: ...

    def save_transition(
        self, task: DownloadTask, expected_version: int, event: TaskEvent
    ) -> DownloadTask: ...

    def save_progress(self, task: DownloadTask, expected_version: int) -> DownloadTask: ...

    def events(self, task_id: UUID) -> builtins.list[TaskEvent]: ...

    def ping(self) -> bool: ...
