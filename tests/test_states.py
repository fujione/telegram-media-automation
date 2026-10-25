from itertools import pairwise

import pytest

from telegram_media_automation.domain.states import (
    InvalidStateTransition,
    TaskState,
    allowed_transitions,
    ensure_transition,
)


def test_happy_path_contains_every_required_pipeline_state() -> None:
    path = [
        TaskState.PENDING,
        TaskState.SEARCHING,
        TaskState.CANDIDATE_SELECTED,
        TaskState.QUEUED,
        TaskState.DOWNLOADING,
        TaskState.DOWNLOADED,
        TaskState.IMPORTING,
        TaskState.IMPORTED,
    ]
    for current, target in pairwise(path):
        ensure_transition(current, target)


def test_terminal_imported_task_cannot_restart_in_place() -> None:
    assert not allowed_transitions(TaskState.IMPORTED)
    with pytest.raises(InvalidStateTransition):
        ensure_transition(TaskState.IMPORTED, TaskState.QUEUED)


def test_cancelled_task_never_implicitly_revives() -> None:
    with pytest.raises(InvalidStateTransition):
        ensure_transition(TaskState.CANCELLED, TaskState.RETRYING)


def test_provisional_can_be_adopted_by_explicit_import() -> None:
    ensure_transition(TaskState.PROVISIONAL, TaskState.IMPORTING)
