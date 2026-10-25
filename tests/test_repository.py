import time
from pathlib import Path
from uuid import uuid4

import pytest

from telegram_media_automation.application.task_service import IdempotencyConflict, TaskService
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository


def service(tmp_path: Path) -> tuple[TaskService, SqlTaskRepository]:
    database = Database(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    database.create_schema_for_development()
    repository = SqlTaskRepository(database)
    return TaskService(repository), repository


def test_task_and_events_survive_repository_reopen(tmp_path: Path) -> None:
    task_service, repository = service(tmp_path)
    task = task_service.create_task(uuid4(), uuid4(), 123, "same-request")
    task_service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    task_service.transition(task.id, TaskState.QUEUED)

    loaded = repository.get(task.id)
    assert loaded is not None
    assert loaded.state is TaskState.QUEUED
    assert [event.new_state for event in repository.events(task.id)] == [
        TaskState.PENDING,
        TaskState.CANDIDATE_SELECTED,
        TaskState.QUEUED,
    ]


def test_idempotent_creation_returns_same_task(tmp_path: Path) -> None:
    task_service, _ = service(tmp_path)
    request_id, resource_id = uuid4(), uuid4()
    first = task_service.create_task(request_id, resource_id, 123, "key")
    second = task_service.create_task(request_id, resource_id, 123, "key")
    assert second.id == first.id


def test_idempotency_key_reuse_with_different_payload_is_rejected(tmp_path: Path) -> None:
    task_service, _ = service(tmp_path)
    with pytest.raises(IdempotencyConflict):
        task_service.create_task(uuid4(), uuid4(), 123, "key")
        task_service.create_task(uuid4(), uuid4(), 456, "key")


def test_task_page_filters_states_and_reports_total(tmp_path: Path) -> None:
    task_service, repository = service(tmp_path)
    tasks = [task_service.create_task(uuid4(), uuid4(), 123) for _ in range(5)]
    task_service.transition(tasks[0].id, TaskState.CANDIDATE_SELECTED)
    task_service.transition(tasks[0].id, TaskState.QUEUED)
    task_service.transition(tasks[1].id, TaskState.CANCELLED)

    first_page, total = repository.page(limit=2)
    queued, queued_total = repository.page(limit=10, states=(TaskState.QUEUED,))
    second_page, second_total = repository.page(limit=2, offset=2)

    assert len(first_page) == 2
    assert len(second_page) == 2
    assert total == second_total == 5
    assert queued_total == 1
    assert [task.id for task in queued] == [tasks[0].id]
    assert repository.counts_by_state() == {
        TaskState.PENDING: 3,
        TaskState.QUEUED: 1,
        TaskState.CANCELLED: 1,
    }


def test_expired_download_lease_is_recovered_and_reclaimed(tmp_path: Path) -> None:
    task_service, repository = service(tmp_path)
    task = task_service.create_task(uuid4(), uuid4(), 123)
    task_service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    task_service.transition(task.id, TaskState.QUEUED)

    first = repository.claim_next("worker-one", 0.01)
    assert first is not None and first.attempt_count == 1
    time.sleep(0.03)
    second = repository.claim_next("worker-two", 60)

    assert second is not None
    assert second.id == task.id
    assert second.attempt_count == 2
    assert second.lease_owner == "worker-two"
    assert [event.new_state for event in repository.events(task.id)][-3:] == [
        TaskState.DOWNLOADING,
        TaskState.RETRYING,
        TaskState.DOWNLOADING,
    ]
