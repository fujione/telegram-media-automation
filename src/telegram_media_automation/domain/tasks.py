"""Task aggregate roots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from telegram_media_automation.domain.media import ArchivePlan
from telegram_media_automation.domain.states import TaskState, ensure_transition


@dataclass(slots=True)
class DownloadTask:
    request_id: UUID
    resource_id: UUID
    expected_size: int
    id: UUID = field(default_factory=uuid4)
    state: TaskState = TaskState.PENDING
    desired_state: TaskState | None = None
    downloaded_bytes: int = 0
    attempt_count: int = 0
    version: int = 1
    error_code: str | None = None
    error_message: str | None = None
    output_path: str | None = None
    next_attempt_at: datetime | None = None
    lease_owner: str | None = None
    lease_until: datetime | None = None
    archive_plan: ArchivePlan | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.expected_size < 0 or self.downloaded_bytes < 0:
            raise ValueError("task byte counts cannot be negative")
        if self.expected_size and self.downloaded_bytes > self.expected_size:
            raise ValueError("downloaded bytes cannot exceed expected size")

    def transition(self, target: TaskState) -> TaskState:
        previous = self.state
        ensure_transition(previous, target)
        if previous != target:
            self.state = target
            if target != TaskState.DOWNLOADING:
                self.lease_owner = None
                self.lease_until = None
            if target == TaskState.DOWNLOADING:
                self.attempt_count += 1
            self.version += 1
            self.updated_at = datetime.now(UTC)
        return previous

    def record_progress(self, downloaded_bytes: int) -> None:
        if downloaded_bytes < self.downloaded_bytes:
            raise ValueError("download progress cannot move backwards")
        if self.expected_size and downloaded_bytes > self.expected_size:
            raise ValueError("download progress cannot exceed expected size")
        if downloaded_bytes != self.downloaded_bytes:
            self.downloaded_bytes = downloaded_bytes
            self.version += 1
            self.updated_at = datetime.now(UTC)

    def reconcile_progress(self, actual_bytes: int) -> None:
        """Trust a verified part-file length after an interrupted process or host."""
        if actual_bytes < 0 or (self.expected_size and actual_bytes > self.expected_size):
            raise ValueError("reconciled progress is outside the expected resource size")
        if actual_bytes != self.downloaded_bytes:
            self.downloaded_bytes = actual_bytes
            self.version += 1
            self.updated_at = datetime.now(UTC)
