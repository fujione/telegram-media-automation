"""Poll user-defined subscriptions and enqueue missing or upcoming episodes."""

# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from telegram_media_automation.application.resource_service import ResourceRepository
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.application.telegram_types import ChannelScan
from telegram_media_automation.domain.channels import telegram_peer
from telegram_media_automation.domain.resources import (
    EpisodeRange,
    MediaResource,
    QualityPolicy,
    rank_resources,
)
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.domain.subscriptions import Subscription
from telegram_media_automation.domain.tasks import DownloadTask


class SubscriptionRepository(Protocol):
    def get(self, subscription_id: UUID) -> Subscription | None: ...
    def list_enabled(self) -> list[Subscription]: ...

    def cursors(self, subscription_id: UUID) -> dict[str, int]: ...

    def advance_cursor(self, subscription_id: UUID, channel: str, message_id: int) -> None: ...

    def save(self, subscription: Subscription, expected_version: int) -> Subscription: ...

    def add_candidates(
        self, subscription_id: UUID, channel: str, resource_ids: list[UUID]
    ) -> None: ...

    def candidate_ids(
        self, subscription_id: UUID, channels: tuple[str, ...] | None = None
    ) -> list[UUID]: ...

    def ensure_channels(
        self, subscription_id: UUID, channels: tuple[str, ...], expected_version: int | None = None
    ) -> None: ...

    def record_scan(
        self,
        subscription_id: UUID,
        version: int,
        channel: str,
        resource_ids: list[UUID],
        message_id: int,
    ) -> bool: ...

    def enqueue(
        self, subscription: Subscription, selected: MediaResource
    ) -> DownloadTask | None: ...

    def remove_candidate(
        self, subscription_id: UUID, resource_id: UUID, expected_version: int | None = None
    ) -> None: ...


class SubscriptionGateway(Protocol):
    async def scan_channel(
        self, channel: str | int, query: str, min_message_id: int, limit: int = 100
    ) -> ChannelScan: ...


class SubscriptionWorker:
    def __init__(
        self,
        subscriptions: SubscriptionRepository,
        resources: ResourceRepository,
        tasks: TaskService,
        gateway: SubscriptionGateway,
        policy: QualityPolicy | None = None,
        *,
        default_channels: Callable[[], tuple[str, ...]] | None = None,
        scan_timeout: float = 60,
        report_status: Callable[[UUID, dict[str, Any]], None] | None = None,
    ) -> None:
        self.subscriptions = subscriptions
        self.resources = resources
        self.tasks = tasks
        self.gateway = gateway
        self.policy = policy or QualityPolicy()
        self.default_channels = default_channels or (lambda: ())
        self.scan_timeout = scan_timeout
        self.report_status = report_status or (lambda _id, _status: None)
        self.channel_retries: dict[tuple[UUID, str], tuple[int, float]] = {}

    def _report(self, subscription: Subscription, state: str, **details: Any) -> None:
        self.report_status(
            subscription.id,
            {
                "state": state,
                "version": subscription.version,
                "checked_at": datetime.now(UTC).isoformat(),
                **details,
            },
        )

    async def run_once(self, subscription_ids: set[UUID] | None = None) -> int:
        enqueued = 0
        subscriptions = self.subscriptions.list_enabled()
        enabled_ids = {subscription.id for subscription in subscriptions}
        self.channel_retries = {
            key: retry for key, retry in self.channel_retries.items() if key[0] in enabled_ids
        }
        for subscription in subscriptions:
            if subscription_ids is not None and subscription.id not in subscription_ids:
                continue
            try:
                enqueued += await self._run_subscription(subscription)
            except Exception as error:
                seconds = float(getattr(error, "seconds", 0) or 0)
                self._report(
                    subscription,
                    "rate_limited" if seconds else "failed",
                    message="Telegram 请求限流，等待后重试"
                    if seconds
                    else "订阅检查失败，将自动重试",
                    retry_at=(datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
                    if seconds
                    else None,
                )
                logging.getLogger(__name__).warning(
                    "subscription check failed: subscription=%s error_type=%s",
                    subscription.id,
                    type(error).__name__,
                )
                if seconds:
                    # Respect Telegram's shared rate limit across subscriptions.
                    raise
        return enqueued

    async def _run_subscription(self, subscription: Subscription) -> int:
        target_episode = subscription.target_episode
        if target_episode is None:
            self._report(subscription, "completed")
            return 0
        channels = subscription.channels or self.default_channels()
        if not channels:
            self._report(subscription, "no_channels", message="没有可用频道，请启用默认频道")
            return 0
        self._report(subscription, "scanning", channel_count=len(channels))
        if not subscription.channels:
            self.subscriptions.ensure_channels(subscription.id, channels, subscription.version)
        cursors = self.subscriptions.cursors(subscription.id)
        cursor_updates: dict[str, int] = {}
        failed_channels = []
        scanned_media_count = 0
        advanced_channels = 0
        for channel in channels:
            key = (subscription.id, channel)
            attempts, retry_at = self.channel_retries.get(key, (0, 0))
            if time.monotonic() < retry_at:
                failed_channels.append(channel)
                continue
            try:
                scan = await asyncio.wait_for(
                    self.gateway.scan_channel(
                        telegram_peer(channel), subscription.query, cursors.get(channel, 0)
                    ),
                    timeout=self.scan_timeout,
                )
                stored = self.resources.upsert_many(scan.resources)
                scanned_media_count += len(stored)
                advanced_channels += int(scan.last_message_id > cursors.get(channel, 0))
                if not self.subscriptions.record_scan(
                    subscription.id,
                    subscription.version,
                    channel,
                    [resource.id for resource in stored],
                    scan.last_message_id,
                ):
                    self._report(subscription, "succeeded", outcome="plan_changed")
                    return 0
                # Persist each successful channel independently; never advance failures.
                cursor_updates[channel] = scan.last_message_id
                self.channel_retries.pop(key, None)
            except Exception as error:
                if getattr(error, "seconds", 0):
                    raise
                current_plan = self.subscriptions.get(subscription.id)
                if current_plan is None or current_plan.version != subscription.version:
                    self._report(subscription, "succeeded", outcome="plan_changed")
                    return 0
                delay = min(60 * 2 ** min(attempts, 3), 300)
                self.channel_retries[key] = (attempts + 1, time.monotonic() + delay)
                failed_channels.append(channel)
                logging.getLogger(__name__).warning(
                    "subscription channel failed: subscription=%s channel=%s "
                    "error_type=%s retry_seconds=%s",
                    subscription.id,
                    channel,
                    type(error).__name__,
                    delay,
                )
        scan_state = (
            "partial"
            if cursor_updates and failed_channels
            else "failed"
            if failed_channels
            else "succeeded"
        )
        rejected = dict.fromkeys(
            (
                "episode_conflict",
                "unparsed_episode",
                "wrong_season",
                "past_episode",
                "future_episode",
                "quality_filtered",
            ),
            0,
        )

        def report_result(
            outcome: str, *, task_id: UUID | None = None, task_state: str | None = None
        ) -> None:
            idle_days = max(0, (datetime.now(UTC) - subscription.updated_at).days)
            self._report(
                subscription,
                scan_state,
                channel_count=len(channels),
                successful_channels=len(cursor_updates),
                failed_channels=failed_channels,
                message="部分频道暂不可用，将自动重试"
                if cursor_updates and failed_channels
                else "频道检查失败，将自动重试"
                if failed_channels
                else None,
                outcome=outcome,
                target_episode=target_episode,
                scanned_media_count=scanned_media_count,
                advanced_channels=advanced_channels,
                rejection_counts=rejected,
                task_id=str(task_id) if task_id else None,
                task_state=task_state,
                plan_idle_days=idle_days,
                stale=scan_state == "succeeded" and outcome == "waiting" and idle_days >= 7,
            )

        stored = [
            resource
            for resource_id in self.subscriptions.candidate_ids(subscription.id, channels)
            if (resource := self.resources.get(resource_id)) is not None
        ]
        relevant: list[MediaResource] = []
        for resource in stored:
            resource_episode = resource.episode_range
            reason = (
                "episode_conflict"
                if resource.episode_conflict
                else "unparsed_episode"
                if resource_episode is None
                else "wrong_season"
                if resource_episode.season != subscription.season
                else "past_episode"
                if not subscription.accepts_range(resource_episode.start, resource_episode.end)
                else None
            )
            if reason:
                rejected[reason] += 1
                self.subscriptions.remove_candidate(
                    subscription.id, resource.id, subscription.version
                )
                continue
            if resource_episode is not None and not (
                resource_episode.start <= target_episode <= resource_episode.end
            ):
                rejected["future_episode"] += 1
            relevant.append(resource)
        stored = relevant
        episode = EpisodeRange(
            subscription.season,
            target_episode,
            target_episode,
        )
        ranked = rank_resources(stored, self.policy, episode)
        covering = [
            resource
            for resource in stored
            if resource.episode_range is not None and resource.episode_range.covers(episode)
        ]
        rejected["quality_filtered"] = len(covering) - len(ranked)
        current = self.subscriptions.get(subscription.id)
        if current is None or not current.enabled or current.version != subscription.version:
            report_result("plan_changed")
            return 0
        if not ranked:
            report_result("waiting")
            return 0
        selected = ranked[0].resource
        task = self.subscriptions.enqueue(subscription, selected)
        if task is None:
            report_result("plan_changed")
            return 0
        if task.state in {TaskState.FAILED, TaskState.CANCELLED}:
            report_result("existing_task", task_id=task.id, task_state=task.state.value)
            return 0
        for resource in stored:
            resource_episode = resource.episode_range
            if resource.id == selected.id or (
                resource_episode is not None
                and not subscription.accepts_range(resource_episode.start, resource_episode.end)
            ):
                self.subscriptions.remove_candidate(
                    subscription.id, resource.id, subscription.version
                )
        report_result("enqueued", task_id=task.id)
        return 1
