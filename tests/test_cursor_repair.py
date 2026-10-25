import importlib.util
import sqlite3
from pathlib import Path

import pytest


def test_cursor_repair_is_scoped_backed_up_and_rejects_stale_plans(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "repair_subscription_cursors.py"
    spec = importlib.util.spec_from_file_location("cursor_repair", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database, backup = tmp_path / "live.sqlite3", tmp_path / "backup.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE subscription_cursors(subscription_id TEXT,channel TEXT,
                last_message_id INTEGER,updated_at TEXT);
            INSERT INTO subscription_cursors VALUES ('one','wrong',43000,'old');
            INSERT INTO subscription_cursors VALUES ('one','valid',7,'old');
            CREATE TABLE subscriptions(id TEXT,next_episode INTEGER);
            INSERT INTO subscriptions VALUES ('one',23);
            CREATE TABLE download_tasks(id TEXT,state TEXT);
            INSERT INTO download_tasks VALUES ('task','imported');
        """)
    plan = [{"subscription_id": "one", "channel": "wrong", "expected_message_id": 43000}]
    assert module.repair(database, plan, None) == 1
    with pytest.raises(ValueError, match="review the plan"):
        module.repair(
            database,
            [*plan, {"subscription_id": "one", "channel": "valid", "expected_message_id": 8}],
            backup,
        )
    assert not backup.exists()
    assert module.repair(database, plan, backup) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT channel,last_message_id FROM subscription_cursors ORDER BY channel"
        ).fetchall() == [("valid", 7), ("wrong", 0)]
        assert connection.execute("SELECT next_episode FROM subscriptions").fetchone() == (23,)
        assert connection.execute("SELECT state FROM download_tasks").fetchone() == ("imported",)
    with sqlite3.connect(backup) as connection:
        assert connection.execute(
            "SELECT last_message_id FROM subscription_cursors WHERE channel='wrong'"
        ).fetchone() == (43000,)
    with pytest.raises(ValueError, match="review the plan"):
        module.repair(database, plan, tmp_path / "second.sqlite3")
