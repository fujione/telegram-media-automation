"""Canonical task states and legal transitions."""

from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    PENDING = "pending"
    SEARCHING = "searching"
    NEEDS_SELECTION = "needs_selection"
    CANDIDATE_SELECTED = "candidate_selected"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    RETRYING = "retrying"
    DOWNLOADED = "downloaded"
    IMPORTING = "importing"
    IMPORTED = "imported"
    PROVISIONAL = "provisional"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {
    TaskState.IMPORTED,
    TaskState.PROVISIONAL,
    TaskState.CANCELLED,
}


_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset(
        {TaskState.SEARCHING, TaskState.CANDIDATE_SELECTED, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.SEARCHING: frozenset(
        {
            TaskState.NEEDS_SELECTION,
            TaskState.CANDIDATE_SELECTED,
            TaskState.RETRYING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.NEEDS_SELECTION: frozenset(
        {TaskState.CANDIDATE_SELECTED, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.CANDIDATE_SELECTED: frozenset(
        {TaskState.QUEUED, TaskState.CANCELLED, TaskState.FAILED}
    ),
    TaskState.QUEUED: frozenset(
        {
            TaskState.DOWNLOADING,
            TaskState.PAUSED,
            TaskState.RETRYING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.DOWNLOADING: frozenset(
        {
            TaskState.PAUSED,
            TaskState.RETRYING,
            TaskState.DOWNLOADED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PAUSED: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.RETRYING: frozenset(
        {
            TaskState.SEARCHING,
            TaskState.QUEUED,
            TaskState.DOWNLOADING,
            TaskState.IMPORTING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.DOWNLOADED: frozenset(
        {TaskState.IMPORTING, TaskState.RETRYING, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.IMPORTING: frozenset(
        {
            TaskState.IMPORTED,
            TaskState.PROVISIONAL,
            TaskState.RETRYING,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.IMPORTED: frozenset(),
    TaskState.PROVISIONAL: frozenset({TaskState.IMPORTING}),
    TaskState.FAILED: frozenset({TaskState.RETRYING, TaskState.CANCELLED}),
    TaskState.CANCELLED: frozenset(),
}


class InvalidStateTransition(ValueError):
    """Raised when a caller attempts a transition not defined by the domain."""


def ensure_transition(current: TaskState, target: TaskState) -> None:
    if current == target:
        return
    if target not in _TRANSITIONS[current]:
        raise InvalidStateTransition(f"cannot transition task from {current} to {target}")


def allowed_transitions(current: TaskState) -> frozenset[TaskState]:
    return _TRANSITIONS[current]
