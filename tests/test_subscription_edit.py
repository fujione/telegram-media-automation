from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import func, select

from telegram_media_automation.application.subscription_worker import SubscriptionWorker
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.application.telegram_types import ChannelScan
from telegram_media_automation.domain.resources import EpisodeRange, MediaResource
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.domain.subscriptions import Subscription
from telegram_media_automation.infrastructure.database import Database, IdempotencyRow, TaskEventRow
from telegram_media_automation.infrastructure.resource_repository import SqlResourceRepository
from telegram_media_automation.infrastructure.subscription_repository import (
    ConcurrentSubscriptionUpdate,
    SqlSubscriptionRepository,
)
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository


def resource(locator: str, end: int = 2) -> MediaResource:
    return MediaResource(
        provider="telegram",
        locator=locator,
        file_name="Fixture.S01E02.mkv",
        file_size=100,
        identity_confidence=90,
        episode_range=EpisodeRange(1, 2, end),
    )


def test_scope_edit_preserves_intersection_and_query_edit_rewinds_history(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'edit.sqlite3'}")
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    resources = SqlResourceRepository(db)
    first, second = resources.upsert_many([resource("first"), resource("second")])
    original = subscriptions.create(Subscription("Fixture", 1, 2, "Fixture", ("keep", "remove")))
    assert subscriptions.record_scan(original.id, original.version, "keep", [first.id], 11)
    assert subscriptions.record_scan(original.id, original.version, "remove", [second.id], 22)
    edited = subscriptions.edit(replace(original, channels=("keep", "new")), original.version)
    assert subscriptions.cursors(original.id) == {"keep": 11, "new": 0}
    assert subscriptions.candidate_ids(original.id) == [first.id]
    with pytest.raises(ConcurrentSubscriptionUpdate):
        subscriptions.edit(replace(original, query="Stale edit"), original.version)
    assert not subscriptions.record_scan(original.id, original.version, "keep", [second.id], 99)
    changed = subscriptions.edit(replace(edited, query="Different query"), edited.version)
    assert subscriptions.cursors(original.id) == {"keep": 0, "new": 0}
    assert subscriptions.candidate_ids(original.id) == []
    assert not subscriptions.record_scan(original.id, edited.version, "keep", [second.id], 99)
    dynamic = subscriptions.edit(replace(changed, channels=()), changed.version)
    subscriptions.ensure_channels(original.id, ("added",), changed.version)
    assert "added" not in subscriptions.cursors(original.id)
    subscriptions.ensure_channels(original.id, ("added",), dynamic.version)
    assert subscriptions.cursors(original.id)["added"] == 0
    restored = subscriptions.get(original.id)
    assert restored is not None and restored.channels == ()


def test_edit_and_enqueue_are_serialized_by_the_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(f"sqlite:///{tmp_path / 'concurrent.sqlite3'}")
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    original = subscriptions.create(Subscription("Fixture", 1, 2, "Fixture", ("channel",)))
    selected = SqlResourceRepository(db).upsert_many([resource("selected")])[0]
    locked, release, editing = Event(), Event(), Event()
    create = TaskService.create_task

    def hold(service: TaskService, *args: object, **kwargs: object) -> object:
        locked.set()
        assert release.wait(5)
        return create(service, *args, **kwargs)  # type: ignore[arg-type]

    def edit() -> Subscription:
        editing.set()
        return subscriptions.edit(replace(original, query="Changed"), 1)

    monkeypatch.setattr(TaskService, "create_task", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        enqueue = pool.submit(subscriptions.enqueue, replace(original), selected)
        try:
            assert locked.wait(5)
            update = pool.submit(edit)
            assert editing.wait(5)
        finally:
            release.set()
        assert enqueue.result(timeout=5) is not None
        with pytest.raises(ConcurrentSubscriptionUpdate):
            update.result(timeout=5)
    current = subscriptions.get(original.id)
    assert current is not None and current.query == "Fixture" and current.next_episode == 3
    assert len(SqlTaskRepository(db).list()) == 1


def test_enqueue_rolls_back_all_state_and_revised_episode_reuses_existing_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(f"sqlite:///{tmp_path / 'atomic.sqlite3'}")
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    tasks = SqlTaskRepository(db)
    first, alternate = SqlResourceRepository(db).upsert_many(
        [resource("first"), resource("alternate", 4)]
    )
    original = subscriptions.create(Subscription("Fixture", 1, 2, "Fixture", ("channel",)))
    transition = TaskService.transition

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Injected transaction failure")

    monkeypatch.setattr(TaskService, "transition", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        subscriptions.enqueue(replace(original), first)
    assert tasks.list() == []
    restored = subscriptions.get(original.id)
    assert restored is not None and restored.next_episode == 2 and restored.version == 1
    with db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TaskEventRow)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyRow)) == 0
    monkeypatch.setattr(TaskService, "transition", transition)
    queued = subscriptions.enqueue(original, first)
    assert queued is not None
    revised = subscriptions.edit(
        replace(original, title="Revised title", next_episode=2), original.version
    )
    assert subscriptions.enqueue(replace(original, version=1), alternate) is None
    reused = subscriptions.enqueue(revised, alternate)
    assert reused is not None and reused.id == queued.id
    assert len(tasks.list()) == 1
    assert reused.resource_id == first.id
    assert reused.archive_plan is not None and reused.archive_plan.title == "Fixture"
    # Progress follows the original task range, not the alternate's larger range.
    assert revised.next_episode == 3
    # A previously queued multi-episode file also covers revised inner episodes.
    current = subscriptions.get(original.id)
    assert current is not None
    multi_plan = subscriptions.edit(replace(current, next_episode=2, season=2), current.version)
    season_two = replace(resource("season-two", 4), episode_range=EpisodeRange(2, 2, 4))
    season_two = SqlResourceRepository(db).upsert_many([season_two])[0]
    multi_task = subscriptions.enqueue(multi_plan, season_two)
    inner = subscriptions.edit(replace(multi_plan, next_episode=3), multi_plan.version)
    reused_multi = subscriptions.enqueue(inner, season_two)
    assert multi_task is not None and reused_multi is not None
    assert reused_multi.id == multi_task.id and len(tasks.list()) == 2
    assert inner.next_episode == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [True, False])
async def test_revised_plan_does_not_skip_a_failed_or_cancelled_task(
    tmp_path: Path, cancelled: bool
) -> None:
    db = Database(f"sqlite:///{tmp_path / 'blocked.sqlite3'}")
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    original = subscriptions.create(Subscription("Fixture", 1, 2, "Fixture", ("channel",)))
    tasks = SqlTaskRepository(db)
    service = TaskService(tasks)
    reports = {}

    class Gateway:
        async def scan_channel(
            self, channel: str | int, query: str, min_message_id: int, limit: int = 100
        ) -> ChannelScan:
            return ChannelScan([resource("target")], 7)

    worker = SubscriptionWorker(
        subscriptions,
        SqlResourceRepository(db),
        service,
        Gateway(),
        report_status=lambda key, value: reports.update({key: value}),
    )
    assert await worker.run_once() == 1
    task = tasks.list()[0]
    if cancelled:
        service.transition(task.id, TaskState.CANCELLED)
    else:
        service.transition(task.id, TaskState.DOWNLOADING)
        service.transition(task.id, TaskState.FAILED)
    current = subscriptions.get(original.id)
    assert current is not None
    subscriptions.edit(replace(current, next_episode=2), current.version)
    assert await worker.run_once() == 0
    assert reports[original.id]["outcome"] == "existing_task"
    assert reports[original.id]["task_id"] == str(task.id)
    remaining = subscriptions.get(original.id)
    assert remaining is not None and remaining.next_episode == 2
    assert len(tasks.list()) == 1
    assert len(subscriptions.candidate_ids(original.id)) == 1


@pytest.mark.asyncio
async def test_edit_during_scan_cannot_write_old_candidates_or_cursors(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'scan-edit.sqlite3'}")
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    original = subscriptions.create(Subscription("Fixture", 1, 2, "Old query", ("old",)))
    tasks = SqlTaskRepository(db)

    class Gateway:
        async def scan_channel(
            self, channel: str | int, query: str, min_message_id: int, limit: int = 100
        ) -> ChannelScan:
            if query == "Old query":
                subscriptions.edit(replace(original, query="New query", channels=("new",)), 1)
                return ChannelScan([resource("old-result")], 999)
            assert channel == "new" and query == "New query" and min_message_id == 0
            return ChannelScan([resource("new-result")], 7)

    worker = SubscriptionWorker(
        subscriptions, SqlResourceRepository(db), TaskService(tasks), Gateway()
    )
    assert await worker.run_once() == 0
    assert tasks.list() == []
    assert subscriptions.cursors(original.id) == {"new": 0}
    assert subscriptions.candidate_ids(original.id) == []
    assert await worker.run_once() == 1
    assert len(tasks.list()) == 1
    assert subscriptions.cursors(original.id) == {"new": 7}
