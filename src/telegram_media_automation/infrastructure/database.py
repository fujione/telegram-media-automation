"""SQLAlchemy models and database lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

CURRENT_SCHEMA_VERSION = 14


class Base(DeclarativeBase):
    pass


class DownloadTaskRow(Base):
    __tablename__ = "download_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    desired_state: Mapped[str | None] = mapped_column(String(32))
    expected_size: Mapped[int] = mapped_column(Integer)
    downloaded_bytes: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    output_path: Mapped[str | None] = mapped_column(String(1000))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archive_kind: Mapped[str | None] = mapped_column(String(20))
    archive_title: Mapped[str | None] = mapped_column(String(300))
    archive_year: Mapped[int | None] = mapped_column(Integer)
    archive_season: Mapped[int | None] = mapped_column(Integer)
    archive_episode: Mapped[int | None] = mapped_column(Integer)
    archive_episode_end: Mapped[int | None] = mapped_column(Integer)
    archive_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    archive_replace_existing: Mapped[bool | None] = mapped_column(Boolean)
    archive_target_path: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskEventRow(Base):
    __tablename__ = "task_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("download_tasks.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(100))
    previous_state: Mapped[str | None] = mapped_column(String(32))
    new_state: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(80))
    correlation_id: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("download_tasks.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MediaResourceRow(Base):
    __tablename__ = "media_resources"
    __table_args__ = (UniqueConstraint("provider", "locator", name="uq_resource_locator"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    locator: Mapped[str] = mapped_column(String(1000))
    file_name: Mapped[str] = mapped_column(String(500))
    file_size: Mapped[int] = mapped_column(Integer)
    identity_confidence: Mapped[int] = mapped_column(Integer)
    season: Mapped[int | None] = mapped_column(Integer)
    episode_start: Mapped[int | None] = mapped_column(Integer)
    episode_end: Mapped[int | None] = mapped_column(Integer)
    episode_conflict: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    resolution: Mapped[int | None] = mapped_column(Integer)
    codec: Mapped[str | None] = mapped_column(String(30))
    hdr: Mapped[bool] = mapped_column(Boolean, default=False)
    languages: Mapped[str] = mapped_column(String(500), default="")
    source_channel_id: Mapped[int | None] = mapped_column(Integer)
    source_message_id: Mapped[int | None] = mapped_column(Integer)
    source_channel_title: Mapped[str | None] = mapped_column(String(300))
    source_channel_username: Mapped[str | None] = mapped_column(String(300))
    source_link: Mapped[str | None] = mapped_column(String(1000))
    forwarded_from_channel_id: Mapped[int | None] = mapped_column(Integer)
    forwarded_from_message_id: Mapped[int | None] = mapped_column(Integer)
    forwarded_from_title: Mapped[str | None] = mapped_column(String(300))
    forwarded_from_username: Mapped[str | None] = mapped_column(String(300))
    forwarded_from_link: Mapped[str | None] = mapped_column(String(1000))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SchemaVersionRow(Base):
    __tablename__ = "schema_versions"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionRow(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    season: Mapped[int] = mapped_column(Integer)
    next_episode: Mapped[int] = mapped_column(Integer)
    pending_episodes: Mapped[str] = mapped_column(String(5000), default="")
    continue_tracking: Mapped[bool] = mapped_column(Boolean, default=True)
    query: Mapped[str] = mapped_column(String(300))
    use_default_channels: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionCursorRow(Base):
    __tablename__ = "subscription_cursors"

    subscription_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    channel: Mapped[str] = mapped_column(String(300), primary_key=True)
    last_message_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionCandidateRow(Base):
    __tablename__ = "subscription_candidates"

    subscription_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    resource_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media_resources.id", ondelete="CASCADE"), primary_key=True
    )
    channel: Mapped[str] = mapped_column(String(300))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SearchChannelRow(Base):
    __tablename__ = "search_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identifier: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(300))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionScanSummaryRow(Base):
    __tablename__ = "subscription_scan_summaries"

    subscription_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True
    )
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    status_json: Mapped[str] = mapped_column(String(40000), default="{}")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_task_id: Mapped[str | None] = mapped_column(String(36))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class SubscriptionScanHistoryRow(Base):
    __tablename__ = "subscription_scan_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subscription_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status_json: Mapped[str] = mapped_column(String(40000))


class WorkerGateRow(Base):
    __tablename__ = "worker_gates"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Database:
    def __init__(self, url: str) -> None:
        parsed_url = make_url(url)
        database_path = parsed_url.database
        if (
            parsed_url.drivername.startswith("sqlite")
            and database_path
            and database_path != ":memory:"
        ):
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._configure_sqlite)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    def migrate(self) -> int:
        """Apply the versioned baseline; later releases append ordered migrations here."""
        schema_table = cast(Table, SchemaVersionRow.__table__)
        schema_table.create(self.engine, checkfirst=True)
        with self.session_factory.begin() as session:
            current = session.scalar(
                select(SchemaVersionRow.version).order_by(SchemaVersionRow.version.desc())
            )
            if current is not None and current > CURRENT_SCHEMA_VERSION:
                raise RuntimeError(f"database schema {current} is newer than this application")
            if current is None:
                Base.metadata.create_all(self.engine)
                session.add(
                    SchemaVersionRow(version=CURRENT_SCHEMA_VERSION, applied_at=datetime.now(UTC))
                )
                return CURRENT_SCHEMA_VERSION
            Base.metadata.create_all(self.engine)
            if current == 1:
                columns = {
                    str(row[1])
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(download_tasks)"
                    )
                }
                if "output_path" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE download_tasks ADD COLUMN output_path VARCHAR(1000)"
                    )
                session.add(SchemaVersionRow(version=2, applied_at=datetime.now(UTC)))
                current = 2
            if current == 2:
                columns = {
                    str(row[1])
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(download_tasks)"
                    )
                }
                if "next_attempt_at" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE download_tasks ADD COLUMN next_attempt_at DATETIME"
                    )
                    session.connection().exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_download_tasks_next_attempt_at "
                        "ON download_tasks (next_attempt_at)"
                    )
                session.add(SchemaVersionRow(version=3, applied_at=datetime.now(UTC)))
                current = 3
            if current == 3:
                session.add(SchemaVersionRow(version=4, applied_at=datetime.now(UTC)))
                current = 4
            if current == 4:
                columns = {
                    str(row[1])
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(download_tasks)"
                    )
                }
                if "lease_owner" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE download_tasks ADD COLUMN lease_owner VARCHAR(100)"
                    )
                    session.connection().exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_download_tasks_lease_owner "
                        "ON download_tasks (lease_owner)"
                    )
                if "lease_until" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE download_tasks ADD COLUMN lease_until DATETIME"
                    )
                    session.connection().exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_download_tasks_lease_until "
                        "ON download_tasks (lease_until)"
                    )
                session.add(SchemaVersionRow(version=5, applied_at=datetime.now(UTC)))
                current = 5
            if current == 5:
                columns = {
                    str(row[1])
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(download_tasks)"
                    )
                }
                additions = {
                    "archive_kind": "VARCHAR(20)",
                    "archive_title": "VARCHAR(300)",
                    "archive_year": "INTEGER",
                    "archive_season": "INTEGER",
                    "archive_episode": "INTEGER",
                    "archive_confirmed": "BOOLEAN",
                }
                for name, data_type in additions.items():
                    if name not in columns:
                        session.connection().exec_driver_sql(
                            f"ALTER TABLE download_tasks ADD COLUMN {name} {data_type}"
                        )
                session.add(SchemaVersionRow(version=6, applied_at=datetime.now(UTC)))
                current = 6
            if current == 6:
                columns = {
                    str(row[1])
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(download_tasks)"
                    )
                }
                if "archive_episode_end" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE download_tasks ADD COLUMN archive_episode_end INTEGER"
                    )
                session.add(SchemaVersionRow(version=7, applied_at=datetime.now(UTC)))
                current = 7
            if current == 7:
                columns = {
                    str(row[1])
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(media_resources)"
                    )
                }
                if "episode_conflict" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE media_resources "
                        "ADD COLUMN episode_conflict BOOLEAN NOT NULL DEFAULT 0"
                    )
                session.add(SchemaVersionRow(version=8, applied_at=datetime.now(UTC)))
                current = 8
            if current == 8:
                columns = {
                    str(row[1])
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(subscriptions)"
                    )
                }
                if "pending_episodes" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE subscriptions "
                        "ADD COLUMN pending_episodes VARCHAR(5000) NOT NULL DEFAULT ''"
                    )
                if "continue_tracking" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE subscriptions "
                        "ADD COLUMN continue_tracking BOOLEAN NOT NULL DEFAULT 1"
                    )
                session.add(SchemaVersionRow(version=9, applied_at=datetime.now(UTC)))
                current = 9
            if current == 9:
                columns = {
                    str(row[1])
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(media_resources)"
                    )
                }
                additions = {
                    "source_channel_id": "INTEGER",
                    "source_message_id": "INTEGER",
                    "source_channel_title": "VARCHAR(300)",
                    "source_channel_username": "VARCHAR(300)",
                    "source_link": "VARCHAR(1000)",
                    "forwarded_from_channel_id": "INTEGER",
                    "forwarded_from_message_id": "INTEGER",
                    "forwarded_from_title": "VARCHAR(300)",
                    "forwarded_from_username": "VARCHAR(300)",
                    "forwarded_from_link": "VARCHAR(1000)",
                }
                for name, data_type in additions.items():
                    if name not in columns:
                        session.connection().exec_driver_sql(
                            f"ALTER TABLE media_resources ADD COLUMN {name} {data_type}"
                        )
                session.add(SchemaVersionRow(version=10, applied_at=datetime.now(UTC)))
                current = 10
            if current == 10:
                rows = (
                    session.connection()
                    .exec_driver_sql(
                        "SELECT id, locator FROM media_resources "
                        "WHERE provider = 'telegram' AND source_channel_id IS NULL"
                    )
                    .all()
                )
                for resource_id, raw_locator in rows:
                    try:
                        locator = json.loads(str(raw_locator))
                        chat_id = int(locator["chat_id"])
                        message_id = int(locator["message_id"])
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if chat_id == 0 or message_id <= 0:
                        continue
                    serialized = str(chat_id)
                    source_link = (
                        f"https://t.me/c/{serialized[4:]}/{message_id}"
                        if serialized.startswith("-100") and serialized[4:].isdigit()
                        else None
                    )
                    session.connection().exec_driver_sql(
                        "UPDATE media_resources SET source_channel_id = ?, "
                        "source_message_id = ?, source_link = ? WHERE id = ?",
                        (chat_id, message_id, source_link, str(resource_id)),
                    )
                session.add(SchemaVersionRow(version=11, applied_at=datetime.now(UTC)))
                current = 11
            if current == 11:
                cast(Table, DownloadTaskRow.__table__).create(session.connection(), checkfirst=True)
                columns = {
                    row[1]
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(download_tasks)"
                    )
                }
                for name, data_type in {
                    "archive_replace_existing": "BOOLEAN",
                    "archive_target_path": "VARCHAR(1000)",
                }.items():
                    if name not in columns:
                        session.connection().exec_driver_sql(
                            f"ALTER TABLE download_tasks ADD COLUMN {name} {data_type}"
                        )
                session.add(SchemaVersionRow(version=12, applied_at=datetime.now(UTC)))
                current = 12
            if current == 12:
                columns = {
                    row[1]
                    for row in session.connection().exec_driver_sql(
                        "PRAGMA table_info(subscriptions)"
                    )
                }
                if "use_default_channels" not in columns:
                    session.connection().exec_driver_sql(
                        "ALTER TABLE subscriptions ADD COLUMN "
                        "use_default_channels BOOLEAN NOT NULL DEFAULT 0"
                    )
                session.add(SchemaVersionRow(version=13, applied_at=datetime.now(UTC)))
                current = 13
            if current == 13:
                for model in (
                    SubscriptionScanSummaryRow,
                    SubscriptionScanHistoryRow,
                    WorkerGateRow,
                ):
                    cast(Table, model.__table__).create(session.connection(), checkfirst=True)
                session.add(SchemaVersionRow(version=14, applied_at=datetime.now(UTC)))
                current = 14
            return current

    def create_schema_for_development(self) -> None:
        self.migrate()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            return True
        except Exception:
            return False

    def schema_version(self) -> int:
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(SchemaVersionRow.version).order_by(SchemaVersionRow.version.desc())
                )
                or 0
            )
