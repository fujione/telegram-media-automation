"""Bounded scan history, durable observations and coalesced scan requests."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from telegram_media_automation.infrastructure.database import (
    Database,
    DownloadTaskRow,
    SubscriptionRow,
    SubscriptionScanHistoryRow,
    SubscriptionScanSummaryRow,
    TaskEventRow,
    WorkerGateRow,
)


def timestamp(value: datetime | None) -> str | None:
    return value.replace(tzinfo=value.tzinfo or UTC).isoformat() if value else None


class SqlScanRepository:
    HISTORY_PER_SUBSCRIPTION = 50
    HISTORY_TOTAL = 5000
    HISTORY_DAYS = 30
    STATES = frozenset(
        {"scanning", "succeeded", "partial", "failed", "rate_limited", "no_channels", "completed"}
    )

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _summary(
        session: Session, subscription_id: UUID, version: int
    ) -> SubscriptionScanSummaryRow:
        row = session.get(SubscriptionScanSummaryRow, str(subscription_id))
        if row is None:
            row = SubscriptionScanSummaryRow(
                subscription_id=str(subscription_id),
                plan_version=version,
                status_json="{}",
                consecutive_failures=0,
            )
            session.add(row)
        return row

    @staticmethod
    def mark_enqueued(
        session: Session, subscription_id: UUID, version: int, task_id: UUID, created_at: datetime
    ) -> None:
        row = SqlScanRepository._summary(session, subscription_id, version)
        row.last_enqueued_at = created_at
        row.last_task_id = str(task_id)

    @staticmethod
    def _lock(
        session: Session, subscription_id: UUID, version: int, *, enabled: bool = False
    ) -> bool:
        statement = update(SubscriptionRow).where(
            SubscriptionRow.id == str(subscription_id), SubscriptionRow.version == version
        )
        if enabled:
            statement = statement.where(SubscriptionRow.enabled.is_(True))
        return (
            cast(CursorResult[Any], session.execute(statement.values(version=version))).rowcount
            == 1
        )

    def record_status(self, subscription_id: UUID, report: dict[str, Any]) -> bool:
        state = report.get("state")
        version = report.get("version")
        if (
            state not in self.STATES
            or not isinstance(version, int)
            or report.get("outcome") == "plan_changed"
        ):
            return False
        allowed = {
            "state",
            "version",
            "checked_at",
            "channel_count",
            "successful_channels",
            "failed_channels",
            "message",
            "outcome",
            "target_episode",
            "scanned_media_count",
            "advanced_channels",
            "rejection_counts",
            "task_id",
            "task_state",
            "plan_idle_days",
            "stale",
            "retry_at",
        }
        codes = {
            "partial": "some_channels_failed",
            "failed": "all_channels_failed",
            "rate_limited": "telegram_rate_limited",
            "no_channels": "no_channels",
        }
        payload = {key: value for key, value in report.items() if key in allowed}
        if state in codes:
            payload["error_code"] = codes[state]
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 40000:
            raise ValueError("scan report exceeds storage limit")
        checked = datetime.fromisoformat(str(report["checked_at"]))
        checked = checked.replace(tzinfo=checked.tzinfo or UTC).astimezone(UTC)
        with self.database.session_factory.begin() as session:
            if not self._lock(session, subscription_id, version):
                return False
            row = self._summary(session, subscription_id, version)
            if row.status_json == encoded:
                return True
            row.status_json = encoded
            row.plan_version = version
            if state == "scanning":
                row.last_attempt_at = checked
                row.requested_at = None
                return True
            if row.last_attempt_at is None or state in {"no_channels", "completed"}:
                row.last_attempt_at = checked
            row.last_error_code = codes.get(state)
            if state == "succeeded":
                row.last_success_at = checked
                row.consecutive_failures = 0
            elif state in codes:
                row.consecutive_failures += 1
            if state == "rate_limited":
                row.requested_at = row.requested_at or checked
            else:
                row.requested_at = None
            session.add(
                SubscriptionScanHistoryRow(
                    id=str(uuid4()),
                    subscription_id=str(subscription_id),
                    checked_at=checked,
                    status_json=encoded,
                )
            )
            session.flush()
            keep = (
                select(SubscriptionScanHistoryRow.id)
                .where(SubscriptionScanHistoryRow.subscription_id == str(subscription_id))
                .order_by(
                    SubscriptionScanHistoryRow.checked_at.desc(),
                    SubscriptionScanHistoryRow.id.desc(),
                )
                .limit(self.HISTORY_PER_SUBSCRIPTION)
            )
            session.execute(
                delete(SubscriptionScanHistoryRow).where(
                    SubscriptionScanHistoryRow.subscription_id == str(subscription_id),
                    SubscriptionScanHistoryRow.id.not_in(keep),
                )
            )
            global_keep = (
                select(SubscriptionScanHistoryRow.id)
                .order_by(
                    SubscriptionScanHistoryRow.checked_at.desc(),
                    SubscriptionScanHistoryRow.id.desc(),
                )
                .limit(self.HISTORY_TOTAL)
            )
            session.execute(
                delete(SubscriptionScanHistoryRow).where(
                    or_(
                        SubscriptionScanHistoryRow.checked_at
                        < datetime.now(UTC) - timedelta(days=self.HISTORY_DAYS),
                        SubscriptionScanHistoryRow.id.not_in(global_keep),
                    )
                )
            )
        return True

    def status(self, subscription_id: UUID, version: int) -> dict[str, Any] | None:
        with self.database.session_factory() as session:
            row = session.get(SubscriptionScanSummaryRow, str(subscription_id))
            if (
                row is None
                or row.plan_version != version
                or not row.status_json
                or row.status_json == "{}"
            ):
                return None
            report = cast(dict[str, Any], json.loads(row.status_json))
            return report if report.get("version") == version else None

    def health(self, subscription_id: UUID) -> dict[str, Any]:
        with self.database.session_factory() as session:
            row = session.get(SubscriptionScanSummaryRow, str(subscription_id))
            # Existing immutable task/events also supply historical observations
            # for installations upgrading from schema 13.
            last_task = session.scalar(
                select(func.max(DownloadTaskRow.created_at)).where(
                    DownloadTaskRow.request_id == str(subscription_id),
                    DownloadTaskRow.archive_kind == "series",
                )
            )
            archived = session.scalar(
                select(func.max(TaskEventRow.occurred_at))
                .join(DownloadTaskRow, DownloadTaskRow.id == TaskEventRow.task_id)
                .where(
                    DownloadTaskRow.request_id == str(subscription_id),
                    TaskEventRow.new_state.in_(["imported", "provisional"]),
                )
            )
            subscription = session.get(SubscriptionRow, str(subscription_id))
            activity = [
                value
                for value in [row.last_enqueued_at if row else None, last_task, archived]
                if value
            ]
            resource_at = (
                max(activity) if activity else subscription.created_at if subscription else None
            )
            idle_days = (
                max(0, (datetime.now(UTC) - resource_at.replace(tzinfo=UTC)).days)
                if resource_at
                else 0
            )
            return {
                "resource_idle_days": idle_days,
                "last_attempt_at": timestamp(row.last_attempt_at) if row else None,
                "last_success_at": timestamp(row.last_success_at) if row else None,
                "last_enqueued_at": timestamp(row.last_enqueued_at or last_task)
                if row
                else timestamp(last_task),
                "last_archived_at": timestamp(archived),
                "consecutive_failures": row.consecutive_failures if row else 0,
                "last_error_code": row.last_error_code if row else None,
                "requested_at": timestamp(row.requested_at) if row else None,
            }

    def history(self, subscription_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= self.HISTORY_PER_SUBSCRIPTION:
            raise ValueError("scan history limit is invalid")
        with self.database.session_factory() as session:
            values = session.scalars(
                select(SubscriptionScanHistoryRow.status_json)
                .where(
                    SubscriptionScanHistoryRow.subscription_id == str(subscription_id),
                    SubscriptionScanHistoryRow.checked_at
                    >= datetime.now(UTC) - timedelta(days=self.HISTORY_DAYS),
                )
                .order_by(
                    SubscriptionScanHistoryRow.checked_at.desc(),
                    SubscriptionScanHistoryRow.id.desc(),
                )
                .limit(limit)
            ).all()
            return [cast(dict[str, Any], json.loads(value)) for value in values]

    def request(
        self, subscription_id: UUID, version: int, is_scanning: Callable[[], bool] = lambda: False
    ) -> str:
        with self.database.session_factory.begin() as session:
            if not self._lock(session, subscription_id, version, enabled=True):
                return "unavailable"
            if is_scanning():
                return "scanning"
            row = self._summary(session, subscription_id, version)
            if row.requested_at is not None:
                return "already_queued"
            row.requested_at = datetime.now(UTC)
            return "queued"

    def pending(self) -> set[UUID]:
        with self.database.session_factory() as session:
            ids = session.scalars(
                select(SubscriptionScanSummaryRow.subscription_id)
                .join(
                    SubscriptionRow,
                    SubscriptionRow.id == SubscriptionScanSummaryRow.subscription_id,
                )
                .where(
                    SubscriptionRow.enabled.is_(True),
                    SubscriptionScanSummaryRow.requested_at.is_not(None),
                )
            ).all()
            return {UUID(value) for value in ids}

    def defer(self, seconds: float) -> None:
        until = datetime.now(UTC) + timedelta(seconds=max(0, seconds))
        with self.database.session_factory.begin() as session:
            row = session.get(WorkerGateRow, "telegram_subscriptions")
            if row is None:
                session.add(WorkerGateRow(name="telegram_subscriptions", not_before=until))
            elif row.not_before.replace(tzinfo=UTC) < until:
                row.not_before = until

    def retry_at(self) -> datetime | None:
        with self.database.session_factory() as session:
            row = session.get(WorkerGateRow, "telegram_subscriptions")
            until = row.not_before.replace(tzinfo=UTC) if row else None
            return until if until and until > datetime.now(UTC) else None

    def diagnostic_rows(self) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            subscriptions = session.scalars(
                select(SubscriptionRow).order_by(SubscriptionRow.created_at.desc()).limit(500)
            ).all()
            ids = [value.id for value in subscriptions]
            summaries = {
                row.subscription_id: row
                for row in session.scalars(
                    select(SubscriptionScanSummaryRow).where(
                        SubscriptionScanSummaryRow.subscription_id.in_(ids)
                    )
                )
            }
            rows = []
            for subscription in subscriptions:
                row = summaries.get(subscription.id)
                try:
                    report = json.loads(row.status_json) if row else {}
                except (ValueError, TypeError):
                    report = {}
                if not isinstance(report, dict):
                    report = {}
                rows.append(
                    {
                        "id": subscription.id,
                        "enabled": subscription.enabled,
                        "report": report,
                        "last_attempt_at": timestamp(row.last_attempt_at) if row else None,
                        "last_success_at": timestamp(row.last_success_at) if row else None,
                        "last_enqueued_at": timestamp(row.last_enqueued_at) if row else None,
                        "consecutive_failures": row.consecutive_failures if row else 0,
                        "last_error_code": row.last_error_code if row else None,
                        "requested_at": timestamp(row.requested_at) if row else None,
                    }
                )
            return rows
