"""Apply an explicit, reviewed cursor repair plan with a consistent backup.

Run with the service stopped. A plan is a JSON list of subscription_id, channel,
and expected_message_id. Only listed cursors are reset; tasks and plans are retained.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def repair(database: Path, plan: list[dict[str, object]], backup: Path | None) -> int:
    if not database.is_file():
        raise ValueError("database does not exist")
    keys = [(item["subscription_id"], item["channel"]) for item in plan]
    if len(set(keys)) != len(keys) or not plan:
        raise ValueError("repair plan must contain unique cursor keys")
    with sqlite3.connect(f"file:{database}?mode=rw", uri=True) as connection:
        connection.execute("PRAGMA busy_timeout=5000")
        # Validate every precondition before creating a backup or changing any row.
        for item in plan:
            current = connection.execute(
                "SELECT last_message_id FROM subscription_cursors "
                "WHERE subscription_id=? AND channel=?",
                (item["subscription_id"], item["channel"]),
            ).fetchone()
            if current is None or current[0] != item["expected_message_id"]:
                raise ValueError("cursor changed or is missing; review the plan again")
        if backup is None:
            return len(plan)
        if backup.resolve() == database.resolve() or backup.exists():
            raise ValueError("backup must be a new file separate from the live database")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup) as target:
            connection.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("backup integrity check failed")
        connection.execute("BEGIN IMMEDIATE")
        for item in plan:
            updated = connection.execute(
                "UPDATE subscription_cursors SET last_message_id=0, updated_at=? "
                "WHERE subscription_id=? AND channel=? AND last_message_id=?",
                (
                    datetime.now(UTC).isoformat(),
                    item["subscription_id"],
                    item["channel"],
                    item["expected_message_id"],
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("cursor changed during repair; transaction rolled back")
        return len(plan)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and args.backup is None:
        parser.error("--apply requires --backup")
    count = repair(
        args.database,
        json.loads(args.plan.read_text(encoding="utf-8")),
        args.backup if args.apply else None,
    )
    print(json.dumps({"applied": args.apply, "cursor_count": count}))


if __name__ == "__main__":
    main()
