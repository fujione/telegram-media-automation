from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from telegram_media_automation.domain.resources import EpisodeRange, MediaResource
from telegram_media_automation.domain.subscriptions import Subscription
from telegram_media_automation.infrastructure.database import Database, SubscriptionScanHistoryRow
from telegram_media_automation.infrastructure.resource_repository import SqlResourceRepository
from telegram_media_automation.infrastructure.scan_repository import SqlScanRepository
from telegram_media_automation.infrastructure.subscription_repository import (
    SqlSubscriptionRepository,
)


def test_scan_history_survives_restart_is_bounded_and_rejects_stale_reports(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'scans.sqlite3'}"
    db = Database(url)
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    subscription = subscriptions.create(Subscription("Fixture", 1, 2, "Fixture", ("channel",)))
    scans = SqlScanRepository(db)
    start = datetime.now(UTC)
    report = {
        "state": "failed",
        "version": 1,
        "checked_at": start.isoformat(),
        "channel_count": 1,
        "successful_channels": 0,
    }
    assert scans.record_status(subscription.id, report)
    assert scans.record_status(subscription.id, report)  # Retransmission is idempotent.
    assert scans.health(subscription.id)["consecutive_failures"] == 1
    assert len(scans.history(subscription.id)) == 1
    for index in range(55):
        assert scans.record_status(
            subscription.id,
            {
                **report,
                "state": "succeeded",
                "target_episode": index + 1,
                "checked_at": (start + timedelta(seconds=index + 1)).isoformat(),
            },
        )
    assert len(scans.history(subscription.id, 50)) == 50
    assert scans.history(subscription.id)[0]["target_episode"] == 55
    assert scans.health(subscription.id)["consecutive_failures"] == 0
    before = scans.health(subscription.id)
    changed = subscriptions.edit(replace(subscription, query="Changed"), 1)
    assert not scans.record_status(subscription.id, report)
    assert scans.status(subscription.id, changed.version) is None
    assert scans.health(subscription.id)["last_enqueued_at"] is None
    db.engine.dispose()
    reopened = Database(url)
    assert reopened.migrate() == 14
    restored = SqlScanRepository(reopened)
    assert restored.health(subscription.id)["last_success_at"] == before["last_success_at"]
    assert len(restored.history(subscription.id, 50)) == 50
    SqlSubscriptionRepository(reopened).delete(subscription.id)
    assert restored.history(subscription.id) == []
    reopened.engine.dispose()


def test_manual_scan_requests_coalesce_and_retry_window_survives_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'requests.sqlite3'}"
    db = Database(url)
    db.migrate()
    subscription = SqlSubscriptionRepository(db).create(
        Subscription("Fixture", 1, 2, "Fixture", ("channel",))
    )
    scans = SqlScanRepository(db)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: scans.request(subscription.id, 1), range(8)))
    assert responses.count("queued") == 1
    assert responses.count("already_queued") == 7
    assert scans.pending() == {subscription.id}
    scans.record_status(
        subscription.id,
        {"state": "scanning", "version": 1, "checked_at": datetime.now(UTC).isoformat()},
    )
    assert scans.pending() == set()
    assert scans.request(subscription.id, 1, lambda: True) == "scanning"
    assert scans.pending() == set()
    scans.defer(60)
    original_deadline = scans.retry_at()
    scans.defer(1)
    assert scans.retry_at() == original_deadline
    db.engine.dispose()
    reopened = Database(url)
    restored = SqlScanRepository(reopened)
    assert restored.retry_at() == original_deadline
    assert restored.request(subscription.id, 1) == "queued"
    assert restored.retry_at() == original_deadline  # Clicking does not clear FloodWait.
    reopened.engine.dispose()


def test_edit_and_reused_tasks_do_not_change_the_last_new_task_time(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'activity.sqlite3'}")
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    scans = SqlScanRepository(db)
    subscription = subscriptions.create(Subscription("Fixture", 1, 2, "Fixture", ("channel",)))
    selected = SqlResourceRepository(db).upsert_many(
        [
            MediaResource(
                provider="telegram",
                locator="fixture",
                file_name="Fixture.S01E02.mkv",
                file_size=100,
                identity_confidence=90,
                episode_range=EpisodeRange(1, 2, 2),
            )
        ]
    )[0]
    task = subscriptions.enqueue(subscription, selected)
    assert task is not None
    original = scans.health(subscription.id)["last_enqueued_at"]
    assert original is not None
    revised = subscriptions.edit(
        replace(subscription, next_episode=2, title="Revised"), subscription.version
    )
    reused = subscriptions.enqueue(revised, selected)
    assert reused is not None and reused.id == task.id
    assert scans.health(subscription.id)["last_enqueued_at"] == original
    with db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SubscriptionScanHistoryRow)) == 0


def test_global_history_cap_age_and_finished_requests(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'retention.sqlite3'}")
    db.migrate()
    scans = SqlScanRepository(db)
    scans.HISTORY_TOTAL = 3
    subscriptions = SqlSubscriptionRepository(db)
    start = datetime.now(UTC)
    for index in range(4):
        sub = subscriptions.create(Subscription("Fixture", 1, 2, "Fixture", ("channel",)))
        scans.request(sub.id, sub.version)
        scans.record_status(
            sub.id,
            {
                "state": "no_channels",
                "version": sub.version,
                "checked_at": (start + timedelta(seconds=index)).isoformat(),
            },
        )
        assert scans.health(sub.id)["requested_at"] is None
        assert scans.history(sub.id)[0]["error_code"] == "no_channels"
    with db.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SubscriptionScanHistoryRow)) == 3
    with db.session_factory.begin() as session:
        session.add(
            SubscriptionScanHistoryRow(
                id="expired",
                subscription_id=str(sub.id),
                checked_at=start - timedelta(days=31),
                status_json='{"state":"failed"}',
            )
        )
    assert len(scans.history(sub.id)) == 1
    scans.record_status(
        sub.id,
        {
            "state": "succeeded",
            "version": sub.version,
            "checked_at": (start + timedelta(seconds=10)).isoformat(),
        },
    )
    with db.session_factory() as session:
        assert session.get(SubscriptionScanHistoryRow, "expired") is None
        assert session.scalar(select(func.count()).select_from(SubscriptionScanHistoryRow)) == 3
    db.engine.dispose()


def test_edit_pause_and_success_do_not_reset_resource_idle_days(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'idle.sqlite3'}")
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    scans = SqlScanRepository(db)
    sub = subscriptions.create(
        Subscription(
            "Fixture",
            1,
            2,
            "Fixture",
            ("channel",),
            created_at=datetime.now(UTC) - timedelta(days=17),
        )
    )
    revised = subscriptions.edit(replace(sub, title="Edited"), sub.version)
    revised.set_enabled(False)
    subscriptions.save(revised, revised.version - 1)
    scans.record_status(
        sub.id,
        {
            "state": "succeeded",
            "version": revised.version,
            "checked_at": datetime.now(UTC).isoformat(),
        },
    )
    assert scans.health(sub.id)["resource_idle_days"] == 17
    assert scans.health(sub.id)["last_enqueued_at"] is None
    db.engine.dispose()
