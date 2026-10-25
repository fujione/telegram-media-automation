import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from telegram_media_automation.infrastructure.database import Database


@pytest.mark.parametrize("with_channel_cursor", [False, True])
def test_migration_never_copies_subscription_cursor_between_channels(
    tmp_path: Path, with_channel_cursor: bool
) -> None:
    source = tmp_path / "old.sqlite3"
    destination = tmp_path / "new.sqlite3"
    Database(f"sqlite:///{destination}").migrate()
    with sqlite3.connect(source) as connection:
        connection.executescript("""
            CREATE TABLE jobs(id TEXT, created_at REAL);
            CREATE TABLE orders(id TEXT, created_at REAL, job_id TEXT);
            CREATE TABLE subscriptions(id TEXT, created_at REAL, updated_at REAL,
                title TEXT, query TEXT, folder TEXT, season INTEGER, episode_start INTEGER,
                active INTEGER, last_message_id INTEGER);
            CREATE TABLE subscription_cursors(subscription_id TEXT, chat TEXT,
                last_message_id INTEGER, updated_at REAL);
            CREATE TABLE search_chats(chat TEXT, created_at REAL);
            INSERT INTO subscriptions VALUES
                ('legacy', 1, 2, 'A Show', 'A Show', 'A Show', 1, 2, 1, 43000);
            INSERT INTO search_chats VALUES ('first', 1), ('second', 1);
        """)
        if with_channel_cursor:
            connection.execute("INSERT INTO subscription_cursors VALUES ('legacy','first',7,2)")
    script = Path(__file__).parents[1] / "scripts" / "migrate_state.py"
    command = [
        sys.executable,
        str(script),
        "--old-db",
        str(source),
        "--new-db",
        str(destination),
        "--old-media-root",
        str(tmp_path),
        "--old-inbox-root",
        str(tmp_path),
    ]
    subprocess.run(command, check=True, capture_output=True, encoding="utf-8")
    with sqlite3.connect(destination) as connection:
        cursors = dict(
            connection.execute("SELECT channel,last_message_id FROM subscription_cursors")
        )
        assert cursors == ({"first": 7} if with_channel_cursor else {"first": 0, "second": 0})
        # Re-running for an existing subscription must also use zero for missing cursors.
        connection.execute("DELETE FROM subscription_cursors")
    subprocess.run(command, check=True, capture_output=True, encoding="utf-8")
    with sqlite3.connect(destination) as connection:
        assert (
            dict(connection.execute("SELECT channel,last_message_id FROM subscription_cursors"))
            == cursors
        )
