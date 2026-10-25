from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from telegram_media_automation.application.subscription_worker import SubscriptionWorker
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.application.telegram_types import ChannelScan
from telegram_media_automation.domain.resources import EpisodeRange, MediaResource, ResourceQuality
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.domain.subscriptions import Subscription
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.resource_repository import SqlResourceRepository
from telegram_media_automation.infrastructure.subscription_repository import (
    SqlSubscriptionRepository,
)
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository


class Gateway:
    async def scan_channel(
        self, channel: str | int, query: str, min_message_id: int, limit: int = 100
    ) -> ChannelScan:
        assert channel == "show-channel" and query == "A Show"
        if min_message_id:
            return ChannelScan([], min_message_id)
        resource = MediaResource(
            provider="telegram",
            locator="episode-two",
            file_name="A.Show.S01E02.1080p.mkv",
            file_size=100,
            identity_confidence=90,
            episode_range=EpisodeRange(1, 2, 2),
            quality=ResourceQuality(resolution=1080),
        )
        return ChannelScan([resource], 456)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_channel", [False, True])
async def test_waiting_report_explains_candidates_and_only_warns_on_success(
    tmp_path: Path, failed_channel: bool
) -> None:
    database = Database(f"sqlite:///{tmp_path / 'feedback.sqlite3'}")
    database.migrate()
    subscriptions = SqlSubscriptionRepository(database)
    channels = ("show-channel", "bad-channel") if failed_channel else ("show-channel",)
    subscription = subscriptions.create(
        Subscription(
            "A Show",
            1,
            2,
            "A Show",
            channels,
            updated_at=datetime.now(UTC) - timedelta(days=10),
        )
    )
    reports = {}
    tasks = SqlTaskRepository(database)

    class CandidateGateway:
        async def scan_channel(self, channel, query, min_message_id, limit=100):
            if channel == "bad-channel":
                raise TimeoutError
            if min_message_id:
                return ChannelScan([], min_message_id)
            return ChannelScan(
                [
                    MediaResource(
                        "telegram", "conflict", "conflict.mp4", 100, 40, episode_conflict=True
                    ),
                    MediaResource("telegram", "unknown", "unknown.mp4", 100, 80),
                    MediaResource(
                        "telegram", "wrong-season", "wrong.mp4", 100, 80, EpisodeRange(2, 2, 2)
                    ),
                    MediaResource("telegram", "past", "past.mp4", 100, 80, EpisodeRange(1, 1, 1)),
                    MediaResource(
                        "telegram", "future", "future.mp4", 100, 80, EpisodeRange(1, 3, 3)
                    ),
                    MediaResource(
                        "telegram", "quality", "quality.mp4", 100, 80, EpisodeRange(1, 2, 2)
                    ),
                ],
                100,
            )

    from telegram_media_automation.domain.resources import QualityPolicy

    worker = SubscriptionWorker(
        subscriptions,
        SqlResourceRepository(database),
        TaskService(tasks),
        CandidateGateway(),
        QualityPolicy(allowed_resolutions=(1080,)),
        report_status=lambda identifier, report: reports.__setitem__(identifier, report),
    )
    assert await worker.run_once() == 0
    report = reports[subscription.id]
    assert report["outcome"] == "waiting"
    assert report["target_episode"] == 2
    assert report["scanned_media_count"] == 6
    assert report["rejection_counts"] == dict.fromkeys(
        (
            "episode_conflict",
            "unparsed_episode",
            "wrong_season",
            "past_episode",
            "future_episode",
            "quality_filtered",
        ),
        1,
    )
    assert report["stale"] is (not failed_channel)
    assert report["plan_idle_days"] == 10
    assert subscriptions.get(subscription.id).enabled
    assert not tasks.list()
    assert await worker.run_once() == 0
    assert reports[subscription.id]["scanned_media_count"] == 0
    # Future/quality candidates remain available without replaying rejected media forever.
    assert len(subscriptions.candidate_ids(subscription.id)) == 2


@pytest.mark.asyncio
async def test_enqueue_report_clears_stale_hint_and_does_not_duplicate_on_rescan(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'enqueue-feedback.sqlite3'}")
    database.migrate()
    subscriptions = SqlSubscriptionRepository(database)
    subscription = subscriptions.create(
        Subscription(
            "A Show",
            1,
            2,
            "A Show",
            ("show-channel",),
            updated_at=datetime.now(UTC) - timedelta(days=10),
        )
    )
    tasks = SqlTaskRepository(database)
    reports = {}
    worker = SubscriptionWorker(
        subscriptions,
        SqlResourceRepository(database),
        TaskService(tasks),
        Gateway(),
        report_status=lambda identifier, report: reports.__setitem__(identifier, report),
    )
    assert await worker.run_once() == 1
    report = reports[subscription.id]
    assert report["outcome"] == "enqueued"
    assert report["target_episode"] == 2
    assert report["task_id"] == str(tasks.list()[0].id)
    assert not report["stale"]
    assert await worker.run_once() == 0
    assert reports[subscription.id]["target_episode"] == 3
    assert not reports[subscription.id]["stale"]
    assert len(tasks.list()) == 1


class FutureEpisodeGateway:
    async def scan_channel(
        self, channel: str | int, query: str, min_message_id: int, limit: int = 100
    ) -> ChannelScan:
        if min_message_id:
            return ChannelScan([], min_message_id)
        return ChannelScan(
            [
                MediaResource(
                    provider="telegram",
                    locator=f"episode-{episode}",
                    file_name=f"A.Show.S01E{episode:02d}.1080p.mkv",
                    file_size=100,
                    identity_confidence=90,
                    episode_range=EpisodeRange(1, episode, episode),
                    quality=ResourceQuality(resolution=1080),
                )
                for episode in (2, 3)
            ],
            500,
        )


@pytest.mark.asyncio
async def test_failed_channel_does_not_block_other_channels_or_subscriptions(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{tmp_path / 'isolated.sqlite3'}")
    database.migrate()
    subscriptions = SqlSubscriptionRepository(database)
    tasks = SqlTaskRepository(database)
    first = subscriptions.create(
        Subscription("A Show", 1, 2, "A Show", ("bad-channel", "show-channel"))
    )
    second = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("show-channel",)))
    reports, calls = {}, []

    class IsolatedGateway(Gateway):
        async def scan_channel(self, channel, query, min_message_id, limit=100):
            calls.append(channel)
            if channel == "bad-channel":
                raise ValueError("private provider details")
            return await super().scan_channel(channel, query, min_message_id, limit)

    worker = SubscriptionWorker(
        subscriptions,
        SqlResourceRepository(database),
        TaskService(tasks),
        IsolatedGateway(),
        report_status=lambda identifier, report: reports.__setitem__(identifier, report),
    )
    assert await worker.run_once() == 2
    assert reports[first.id]["state"] == "partial"
    assert reports[second.id]["state"] == "succeeded"
    assert subscriptions.cursors(first.id) == {"bad-channel": 0, "show-channel": 456}
    assert "private provider details" not in str(reports)
    assert await worker.run_once() == 0
    assert calls.count("bad-channel") == 1
    assert len(tasks.list()) == 2


@pytest.mark.asyncio
async def test_channel_timeout_is_bounded(tmp_path: Path) -> None:
    import asyncio

    database = Database(f"sqlite:///{tmp_path / 'timeout.sqlite3'}")
    database.migrate()
    subscriptions = SqlSubscriptionRepository(database)
    first = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("slow-channel",)))
    second = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("show-channel",)))
    reports = {}

    class SlowGateway(Gateway):
        async def scan_channel(self, channel, query, min_message_id, limit=100):
            if channel == "slow-channel":
                await asyncio.Event().wait()
            return await super().scan_channel(channel, query, min_message_id, limit)

    worker = SubscriptionWorker(
        subscriptions,
        SqlResourceRepository(database),
        TaskService(SqlTaskRepository(database)),
        SlowGateway(),
        scan_timeout=0.01,
        report_status=lambda identifier, report: reports.__setitem__(identifier, report),
    )
    assert await worker.run_once() == 1
    assert reports[first.id]["state"] == "failed"
    assert reports[second.id]["state"] == "succeeded"
    assert subscriptions.cursors(first.id)["slow-channel"] == 0


@pytest.mark.asyncio
async def test_shared_flood_wait_is_not_bypassed(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'flood.sqlite3'}")
    database.migrate()
    subscriptions = SqlSubscriptionRepository(database)
    first = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("flood-channel",)))
    subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("show-channel",)))
    calls, reports = [], {}

    class FloodWaitError(RuntimeError):
        seconds = 30

    class FloodGateway:
        async def scan_channel(self, channel, query, min_message_id, limit=100):
            calls.append(channel)
            raise FloodWaitError("private details")

    worker = SubscriptionWorker(
        subscriptions,
        SqlResourceRepository(database),
        TaskService(SqlTaskRepository(database)),
        FloodGateway(),
        report_status=lambda identifier, report: reports.__setitem__(identifier, report),
    )
    with pytest.raises(FloodWaitError):
        await worker.run_once()
    assert calls == ["flood-channel"]
    assert reports[first.id]["state"] == "rate_limited"
    assert reports[first.id]["retry_at"]


class MixedBacklogGateway:
    async def scan_channel(
        self, channel: str | int, query: str, min_message_id: int, limit: int = 100
    ) -> ChannelScan:
        if min_message_id:
            return ChannelScan([], min_message_id)
        return ChannelScan(
            [
                MediaResource(
                    provider="telegram",
                    locator=locator,
                    file_name=f"{locator}.mkv",
                    file_size=100,
                    identity_confidence=90,
                    episode_range=episode_range,
                    quality=ResourceQuality(resolution=1080),
                )
                for locator, episode_range in (
                    ("unparsed", None),
                    ("old-season", EpisodeRange(0, 2, 2)),
                    ("past", EpisodeRange(1, 1, 1)),
                    ("current", EpisodeRange(1, 2, 2)),
                    ("future", EpisodeRange(1, 3, 3)),
                )
            ],
            600,
        )


@pytest.mark.asyncio
async def test_subscription_repository_error_is_isolated(tmp_path: Path, monkeypatch) -> None:
    database = Database(f"sqlite:///{tmp_path / 'repository-error.sqlite3'}")
    database.migrate()
    subscriptions = SqlSubscriptionRepository(database)
    first = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("show-channel",)))
    second = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("show-channel",)))
    original = subscriptions.cursors
    reports = {}

    def cursors(identifier):
        if identifier == first.id:
            raise RuntimeError("private detail")
        return original(identifier)

    monkeypatch.setattr(subscriptions, "cursors", cursors)
    worker = SubscriptionWorker(
        subscriptions,
        SqlResourceRepository(database),
        TaskService(SqlTaskRepository(database)),
        Gateway(),
        report_status=lambda identifier, report: reports.__setitem__(identifier, report),
    )
    assert await worker.run_once() == 1
    assert reports[first.id]["state"] == "failed"
    assert reports[second.id]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_pause_during_scan_prevents_enqueue(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'pause.sqlite3'}")
    database.migrate()
    subscriptions = SqlSubscriptionRepository(database)
    subscription = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("show-channel",)))
    tasks = SqlTaskRepository(database)

    class PausingGateway(Gateway):
        async def scan_channel(self, channel, query, min_message_id, limit=100):
            current = subscriptions.get(subscription.id)
            version = current.version
            current.set_enabled(False)
            subscriptions.save(current, version)
            return await super().scan_channel(channel, query, min_message_id, limit)

    worker = SubscriptionWorker(
        subscriptions, SqlResourceRepository(database), TaskService(tasks), PausingGateway()
    )
    assert await worker.run_once() == 0
    assert tasks.list() == []
    assert subscriptions.get(subscription.id).next_episode == 2
    assert subscriptions.get(subscription.id).enabled is False


class EpisodePlanGateway:
    async def scan_channel(
        self, channel: str | int, query: str, min_message_id: int, limit: int = 100
    ) -> ChannelScan:
        if min_message_id:
            return ChannelScan([], min_message_id)
        return ChannelScan(
            [
                MediaResource(
                    provider="telegram",
                    locator=f"planned-episode-{episode}",
                    file_name=f"A.Show.S01E{episode:02d}.1080p.mkv",
                    file_size=100,
                    identity_confidence=90,
                    episode_range=EpisodeRange(1, episode, episode),
                    quality=ResourceQuality(resolution=1080),
                )
                for episode in (2, 4, 8)
            ],
            700,
        )


@pytest.mark.asyncio
async def test_default_scope_stays_dynamic_after_persisting_scan_cursors(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'defaults.sqlite3'}")
    database.migrate()
    tasks, resources = SqlTaskRepository(database), SqlResourceRepository(database)
    subscriptions = SqlSubscriptionRepository(database)
    subscription = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ()))
    defaults = ["first-channel"]
    calls = []

    class RecordingGateway:
        async def scan_channel(self, channel, query, min_message_id, limit=100):
            calls.append((channel, min_message_id))
            return ChannelScan([], 5)

    worker = SubscriptionWorker(
        subscriptions,
        resources,
        TaskService(tasks),
        RecordingGateway(),
        default_channels=lambda: tuple(defaults),
    )
    assert await worker.run_once() == 0
    assert subscriptions.get(subscription.id).channels == ()
    defaults[:] = ["second-channel"]
    assert await worker.run_once() == 0
    defaults[:] = ["first-channel"]
    assert await worker.run_once() == 0
    defaults.clear()
    assert await worker.run_once() == 0
    assert calls == [("first-channel", 0), ("second-channel", 0), ("first-channel", 5)]
    assert subscriptions.get(subscription.id).channels == ()
    explicit = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("chosen-channel",)))
    defaults[:] = ["default-channel"]
    assert await worker.run_once() == 0
    assert calls[-1][0] == "chosen-channel"
    assert subscriptions.get(explicit.id).channels == ("chosen-channel",)


@pytest.mark.asyncio
async def test_subscription_enqueues_only_requested_next_episode(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'subscriptions.sqlite3'}")
    database.migrate()
    tasks = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    subscriptions = SqlSubscriptionRepository(database)
    subscription = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("show-channel",)))
    worker = SubscriptionWorker(subscriptions, resources, TaskService(tasks), Gateway())

    assert await worker.run_once() == 1

    updated = subscriptions.get(subscription.id)
    assert updated is not None and updated.next_episode == 3
    assert subscriptions.cursors(subscription.id) == {"show-channel": 456}
    queued = tasks.list()
    assert len(queued) == 1 and queued[0].state == TaskState.QUEUED
    assert queued[0].archive_plan is not None
    assert queued[0].archive_plan.title == "A Show"
    assert queued[0].archive_plan.episode == 2

    assert await worker.run_once() == 0
    assert len(tasks.list()) == 1


@pytest.mark.asyncio
async def test_future_episode_seen_before_cursor_advance_is_kept_in_backlog(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{tmp_path / 'future.sqlite3'}")
    database.migrate()
    tasks = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    subscriptions = SqlSubscriptionRepository(database)
    subscription = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("channel",)))
    worker = SubscriptionWorker(
        subscriptions, resources, TaskService(tasks), FutureEpisodeGateway()
    )

    assert await worker.run_once() == 1
    assert subscriptions.cursors(subscription.id) == {"channel": 500}
    assert subscriptions.get(subscription.id).next_episode == 3  # type: ignore[union-attr]
    assert await worker.run_once() == 1

    assert subscriptions.get(subscription.id).next_episode == 4  # type: ignore[union-attr]
    assert len(tasks.list()) == 2
    assert subscriptions.candidate_ids(subscription.id) == []


@pytest.mark.asyncio
async def test_subscription_prunes_unusable_and_consumed_backlog_entries(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'prune.sqlite3'}")
    database.migrate()
    tasks = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    subscriptions = SqlSubscriptionRepository(database)
    subscription = subscriptions.create(Subscription("A Show", 1, 2, "A Show", ("channel",)))
    worker = SubscriptionWorker(subscriptions, resources, TaskService(tasks), MixedBacklogGateway())

    assert await worker.run_once() == 1

    remaining = [resources.get(value) for value in subscriptions.candidate_ids(subscription.id)]
    assert [resource.locator for resource in remaining if resource is not None] == ["future"]


@pytest.mark.asyncio
async def test_fill_only_subscription_queues_each_gap_then_stops(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'fill-only.sqlite3'}")
    database.migrate()
    task_repository = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    subscriptions = SqlSubscriptionRepository(database)
    subscription = subscriptions.create(
        Subscription(
            "A Show",
            1,
            8,
            "A Show",
            ("channel",),
            pending_episodes=(2, 4),
            continue_tracking=False,
        )
    )
    worker = SubscriptionWorker(
        subscriptions,
        resources,
        TaskService(task_repository),
        EpisodePlanGateway(),
    )

    assert await worker.run_once() == 1
    after_first = subscriptions.get(subscription.id)
    assert after_first is not None
    assert after_first.pending_episodes == (4,)
    assert after_first.next_episode == 8
    assert after_first.enabled is True

    assert await worker.run_once() == 1
    completed = subscriptions.get(subscription.id)
    assert completed is not None
    assert completed.pending_episodes == ()
    assert completed.next_episode == 8
    assert completed.enabled is False
    assert await worker.run_once() == 0
    episodes = [
        plan.episode for task in task_repository.list() if (plan := task.archive_plan) is not None
    ]
    assert set(episodes) == {2, 4}


@pytest.mark.asyncio
async def test_subscription_fills_gap_before_continuing_from_next_episode(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'fill-and-follow.sqlite3'}")
    database.migrate()
    task_repository = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    subscriptions = SqlSubscriptionRepository(database)
    subscription = subscriptions.create(
        Subscription(
            "A Show",
            1,
            8,
            "A Show",
            ("channel",),
            pending_episodes=(2,),
        )
    )
    worker = SubscriptionWorker(
        subscriptions,
        resources,
        TaskService(task_repository),
        EpisodePlanGateway(),
    )

    assert await worker.run_once() == 1
    after_gap = subscriptions.get(subscription.id)
    assert after_gap is not None
    assert after_gap.pending_episodes == ()
    assert after_gap.next_episode == 8

    assert await worker.run_once() == 1
    following = subscriptions.get(subscription.id)
    assert following is not None
    assert following.next_episode == 9
    assert following.enabled is True
