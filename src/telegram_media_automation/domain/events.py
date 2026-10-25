"""Immutable task events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from telegram_media_automation.domain.states import TaskState


@dataclass(frozen=True, slots=True)
class TaskEvent:
    task_id: UUID
    event_type: str
    new_state: TaskState
    previous_state: TaskState | None = None
    reason: str | None = None
    actor: str = "system"
    correlation_id: str | None = None
    id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
