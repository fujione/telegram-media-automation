"""Import historical Telegram media state into the current SQLite schema.

The source database is opened read-only. The destination is backed up with SQLite's
online backup API before one atomic import transaction is started. The importer is
deliberately conservative: records without a downloadable message resource are
reported instead of being turned into synthetic queue items.
"""

# SQL statements are kept as one string to make column order easy to audit.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

STATE_MAP = {
    "imported": "imported",
    "provisional_imported": "provisional",
    "cancelled": "cancelled",
}
TERMINAL_STATES = {"imported", "provisional", "cancelled"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import historical media state")
    parser.add_argument("--old-db", type=Path, required=True)
    parser.add_argument("--new-db", type=Path, required=True)
    parser.add_argument("--old-media-root", type=Path, required=True)
    parser.add_argument("--old-inbox-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--include-tasks",
        action="store_true",
        help="also import historical resources and terminal task records",
    )
    return parser.parse_args()


def utc_text(value: Any) -> str:
    if value is None:
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    timestamp = float(value)
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def stable_id(kind: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"telegram-media-automation:migration:{kind}:{value}"))


def normalize_channel(value: str) -> str:
    value = value.strip()
    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    if parsed.netloc.casefold() in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0].casefold() == "s":
            parts.pop(0)
        if len(parts) >= 2 and parts[0].casefold() == "c" and parts[1].isdigit():
            return f"-100{parts[1]}"
        if parts:
            return parts[0].removeprefix("@").strip()
    return value.removeprefix("@").strip()


def safe_file_name(row: sqlite3.Row) -> str:
    for field in ("source_path", "destination_path"):
        value = row[field]
        if value:
            name = PurePosixPath(str(value)).name
            if name and name not in {".", ".."}:
                return name[:500]
    return f"telegram-message-{row['message_id'] or row['id']}.bin"


def host_path(value: str | None, old_media_root: Path, old_inbox_root: Path) -> Path | None:
    if not value:
        return None
    path = PurePosixPath(value)
    if str(path).startswith("/app/downloads/"):
        return old_inbox_root.joinpath(*path.parts[3:])
    if str(path).startswith("/data/"):
        return old_media_root.joinpath(*path.parts[2:])
    return None


def file_size(row: sqlite3.Row, old_media_root: Path, old_inbox_root: Path) -> int:
    expected = row["expected_size"]
    if expected is not None and int(expected) > 0:
        return int(expected)
    for field in ("source_path", "destination_path"):
        candidate = host_path(row[field], old_media_root, old_inbox_root)
        if candidate is not None:
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            if size >= 0:
                return size
    return 0


def quality_values(value: str | None) -> tuple[int | None, str | None, bool, str]:
    text = (value or "").casefold()
    resolution_match = re.search(r"(?:^|[^0-9])(4320|2160|1440|1080|720|480)p?", text)
    resolution = int(resolution_match.group(1)) if resolution_match else None
    codec = next((item for item in ("hevc", "h265", "h264", "av1", "avc") if item in text), None)
    hdr = bool(re.search(r"(^|[^a-z])hdr|dolby.?vision|dv([^a-z]|$)", text))
    return resolution, codec, hdr, ""


def archive_values(
    row: sqlite3.Row,
) -> tuple[str | None, str | None, int | None, int | None, int | None, int | None]:
    kind = "series" if row["media_kind"] == "tv" else "movie"
    title = str(row["title"] or row["folder"] or "").strip()
    if not title:
        return None, None, None, None, None, None
    year_match = re.search(
        r"(?:\(|\[)(18[7-9][0-9]|19[0-9]{2}|20[0-9]{2}|21[0-9]{2}|22[0-9]{2})(?:\)|\])", title
    )
    year = int(year_match.group(1)) if year_match else None
    clean_title = re.sub(
        r"\s*[\(\[](?:18[7-9][0-9]|19[0-9]{2}|20[0-9]{2}|21[0-9]{2}|22[0-9]{2})[\)\]]", "", title
    ).strip()
    if kind == "movie":
        return kind, clean_title or title, year, None, None, None
    season = int(row["season"] or 0)
    episode = row["episode_start"]
    if episode is None or int(episode) <= 0:
        return None, None, None, None, None, None
    return (
        kind,
        clean_title or title,
        year,
        season,
        int(episode),
        int(row["episode_end"] or episode),
    )


def main() -> int:
    args = parse_args()
    old_db = args.old_db.resolve()
    new_db = args.new_db.resolve()
    if not old_db.is_file() or not new_db.is_file():
        raise SystemExit("both --old-db and --new-db must point to existing files")
    backup = new_db.with_name(f"{new_db.stem}.pre-migration-{datetime.now():%Y%m%d%H%M%S}.sqlite3")
    source = sqlite3.connect(f"file:{old_db}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    destination = sqlite3.connect(new_db)
    destination.row_factory = sqlite3.Row
    destination.execute("PRAGMA foreign_keys=ON")
    destination.execute("PRAGMA busy_timeout=5000")
    backup_connection = sqlite3.connect(backup)
    try:
        destination.backup(backup_connection)
        backup_connection.close()
        backup_connection = None
        historical_jobs = source.execute("select * from jobs order by created_at, id").fetchall()
        jobs = historical_jobs if args.include_tasks else []
        orders = source.execute("select * from orders order by created_at, id").fetchall()
        subscriptions = source.execute(
            "select * from subscriptions order by created_at, id"
        ).fetchall()
        cursors = source.execute(
            "select * from subscription_cursors order by subscription_id, chat"
        ).fetchall()
        search_chats = source.execute(
            "select * from search_chats order by created_at, chat"
        ).fetchall()
        cursor_by_subscription: dict[str, list[sqlite3.Row]] = {}
        for row in cursors:
            cursor_by_subscription.setdefault(str(row["subscription_id"]), []).append(row)
        jobs_by_subscription: dict[str, list[sqlite3.Row]] = {}
        for row in historical_jobs:
            if row["subscription_id"]:
                jobs_by_subscription.setdefault(str(row["subscription_id"]), []).append(row)

        report: dict[str, Any] = {
            "backup": str(backup),
            "source": str(old_db),
            "destination": str(new_db),
            "source_counts": {
                "jobs": len(historical_jobs),
                "orders": len(orders),
                "subscriptions": len(subscriptions),
                "cursors": len(cursors),
                "search_chats": len(search_chats),
            },
            "imported": {
                "resources": 0,
                "tasks": 0,
                "subscriptions": 0,
                "cursors": 0,
                "search_channels": 0,
            },
            "skipped": {"orders_without_job": 0, "unsupported_jobs": 0},
            "warnings": [],
        }
        destination.execute("BEGIN IMMEDIATE")
        channel_values = {
            normalize_channel(str(row["chat"]))
            for row in search_chats
            if str(row["chat"] or "").strip()
        }
        channel_values.update(
            normalize_channel(str(row["chat"])) for row in cursors if str(row["chat"] or "").strip()
        )
        for existing_row in destination.execute(
            "select id, identifier from search_channels"
        ).fetchall():
            canonical = normalize_channel(str(existing_row["identifier"]))
            if canonical == existing_row["identifier"]:
                continue
            duplicate = destination.execute(
                "select id from search_channels where identifier=?", (canonical,)
            ).fetchone()
            if duplicate is None:
                destination.execute(
                    "update search_channels set identifier=? where id=?",
                    (canonical, existing_row["id"]),
                )
            else:
                destination.execute("delete from search_channels where id=?", (existing_row["id"],))
        for identifier in sorted(channel_values):
            existing = destination.execute(
                "select id from search_channels where identifier=?", (identifier,)
            ).fetchone()
            if existing:
                continue
            destination.execute(
                "insert into search_channels (id, identifier, name, enabled, created_at) values (?, ?, ?, ?, ?)",
                (stable_id("channel", identifier), identifier.lstrip("@"), None, 1, utc_text(None)),
            )
            report["imported"]["search_channels"] += 1

        resource_ids: dict[str, str] = {}
        task_rows: list[tuple[sqlite3.Row, str, str]] = []
        for row in jobs:
            old_status = str(row["status"] or "").casefold()
            state = STATE_MAP.get(old_status)
            if state is None:
                report["skipped"]["unsupported_jobs"] += 1
                report["warnings"].append(f"job {row['id']}: unsupported status {old_status}")
                continue
            locator = str(
                row["link"] or row["source_key"] or f"{row['chat_id']}:{row['message_id']}"
            )
            existing = destination.execute(
                "select id from media_resources where provider=? and locator=?",
                ("telegram", locator),
            ).fetchone()
            resource_id = str(existing["id"]) if existing else stable_id("resource", str(row["id"]))
            resource_ids[str(row["id"])] = resource_id
            if not existing:
                resolution, codec, hdr, languages = quality_values(row["quality"])
                size = file_size(row, args.old_media_root, args.old_inbox_root)
                episode_values = (
                    (
                        int(row["season"]),
                        int(row["episode_start"]),
                        int(row["episode_end"] or row["episode_start"]),
                    )
                    if row["season"] is not None and row["episode_start"] is not None
                    else (None, None, None)
                )
                destination.execute(
                    "insert into media_resources (id, provider, locator, file_name, file_size, identity_confidence, season, episode_start, episode_end, episode_conflict, resolution, codec, hdr, languages, discovered_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                    (
                        resource_id,
                        "telegram",
                        locator,
                        safe_file_name(row),
                        size,
                        95 if episode_values[1] else 75,
                        *episode_values,
                        resolution,
                        codec,
                        int(hdr),
                        languages,
                        utc_text(row["created_at"]),
                    ),
                )
                report["imported"]["resources"] += 1
            task_rows.append((row, state, resource_id))

        for row, state, resource_id in task_rows:
            task_id = stable_id("task", str(row["id"]))
            if destination.execute(
                "select 1 from download_tasks where id=?", (task_id,)
            ).fetchone():
                continue
            size = file_size(row, args.old_media_root, args.old_inbox_root)
            downloaded = size if state in TERMINAL_STATES - {"cancelled"} else 0
            (
                archive_kind,
                archive_title,
                archive_year,
                archive_season,
                archive_episode,
                archive_end,
            ) = archive_values(row)
            archive_confirmed = int(state in {"imported", "provisional"}) if archive_kind else None
            request_seed = str(row["order_id"] or row["id"])
            destination.execute(
                "insert into download_tasks (id, request_id, resource_id, state, desired_state, expected_size, downloaded_bytes, attempt_count, version, error_code, error_message, output_path, next_attempt_at, lease_owner, lease_until, archive_kind, archive_title, archive_year, archive_season, archive_episode, archive_episode_end, archive_confirmed, created_at, updated_at) values (?, ?, ?, ?, NULL, ?, ?, ?, 1, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    stable_id("request", request_seed),
                    resource_id,
                    state,
                    size,
                    downloaded,
                    1 if state in TERMINAL_STATES else 0,
                    "legacy_cancelled" if state == "cancelled" else None,
                    row["error"],
                    row["destination_path"] or row["source_path"],
                    archive_kind,
                    archive_title,
                    archive_year,
                    archive_season,
                    archive_episode,
                    archive_end,
                    archive_confirmed,
                    utc_text(row["created_at"]),
                    utc_text(row["updated_at"]),
                ),
            )
            destination.execute(
                "insert into task_events (id, task_id, event_type, previous_state, new_state, reason, actor, correlation_id, occurred_at) values (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    stable_id("event", str(row["id"])),
                    task_id,
                    "task.migrated",
                    state,
                    "historical state imported",
                    "migration",
                    str(row["id"]),
                    utc_text(row["updated_at"]),
                ),
            )
            report["imported"]["tasks"] += 1

        for row in subscriptions:
            subscription_id = stable_id("subscription", str(row["id"]))
            already_exists = (
                destination.execute(
                    "select 1 from subscriptions where id=?", (subscription_id,)
                ).fetchone()
                is not None
            )
            sub_cursors = cursor_by_subscription.get(str(row["id"]), [])
            channels = sorted(
                {
                    normalize_channel(str(item["chat"]))
                    for item in sub_cursors
                    if str(item["chat"] or "").strip()
                }
            )
            if not channels:
                channels = sorted(channel_values)
            if not channels:
                report["warnings"].append(f"subscription {row['id']}: no channels")
                continue
            if already_exists:
                for channel in channels:
                    source_cursor = next(
                        (
                            item
                            for item in sub_cursors
                            if normalize_channel(str(item["chat"])) == channel
                        ),
                        None,
                    )
                    destination.execute(
                        "insert or ignore into subscription_cursors (subscription_id, channel, last_message_id, updated_at) values (?, ?, ?, ?)",
                        (
                            subscription_id,
                            channel,
                            int((source_cursor["last_message_id"] if source_cursor else 0) or 0),
                            utc_text(
                                source_cursor["updated_at"] if source_cursor else row["updated_at"]
                            ),
                        ),
                    )
                continue
            matching_jobs = [
                item
                for item in historical_jobs
                if item["media_kind"] == row["media_kind"]
                and item["status"] in {"imported", "provisional_imported"}
                and any(
                    needle
                    and (
                        str(needle).strip() in str(item["title"] or "")
                        or str(needle).strip() in str(item["folder"] or "")
                    )
                    for needle in (row["query"], row["title"], row["folder"])
                )
            ]
            completed = [int(item["episode_end"]) for item in matching_jobs if item["episode_end"]]
            next_episode = max(completed, default=int(row["episode_start"] or 1)) + (
                1 if completed else 0
            )
            created = utc_text(row["created_at"])
            updated = utc_text(row["updated_at"])
            enabled = int(bool(row["active"]))
            destination.execute(
                "insert into subscriptions (id, title, season, next_episode, pending_episodes, continue_tracking, query, enabled, version, created_at, updated_at) values (?, ?, ?, ?, '[]', ?, ?, ?, 1, ?, ?)",
                (
                    subscription_id,
                    str(row["title"] or row["query"]),
                    int(row["season"] or 0),
                    next_episode,
                    enabled,
                    str(row["query"] or row["title"]),
                    enabled,
                    created,
                    updated,
                ),
            )
            report["imported"]["subscriptions"] += 1
            for cursor in sub_cursors:
                channel = normalize_channel(str(cursor["chat"]))
                if not channel:
                    continue
                destination.execute(
                    "insert or ignore into subscription_cursors (subscription_id, channel, last_message_id, updated_at) values (?, ?, ?, ?)",
                    (
                        subscription_id,
                        channel,
                        int(cursor["last_message_id"] or 0),
                        utc_text(cursor["updated_at"]),
                    ),
                )
                report["imported"]["cursors"] += 1
            if not sub_cursors:
                for channel in channels:
                    destination.execute(
                        "insert or ignore into subscription_cursors (subscription_id, channel, last_message_id, updated_at) values (?, ?, ?, ?)",
                        # Message IDs are scoped to a channel, never a whole subscription.
                        (subscription_id, channel, 0, updated),
                    )
                    report["imported"]["cursors"] += 1

        for order in orders:
            if not order["job_id"]:
                report["skipped"]["orders_without_job"] += 1
        destination.commit()
        if args.report:
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        destination.rollback()
        raise
    finally:
        if backup_connection is not None:
            backup_connection.close()
        destination.close()
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
