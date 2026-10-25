from pathlib import Path

from sqlalchemy import select

from telegram_media_automation.infrastructure.database import Database, SchemaVersionRow


def test_schema_twelve_upgrade_preserves_scope_and_progress_after_restart(tmp_path: Path) -> None:
    from telegram_media_automation.domain.subscriptions import Subscription
    from telegram_media_automation.infrastructure.subscription_repository import (
        SqlSubscriptionRepository,
    )

    url = f"sqlite:///{tmp_path / 'version-twelve.sqlite3'}"
    database = Database(url)
    database.migrate()
    repository = SqlSubscriptionRepository(database)
    original = repository.create(
        Subscription(
            title="Upgrade fixture",
            query="Upgrade fixture",
            season=2,
            next_episode=8,
            pending_episodes=(3, 5),
            channels=("fixture_channel",),
        )
    )
    repository.advance_cursor(original.id, "fixture_channel", 123)
    with database.engine.begin() as connection:
        # Recreate the schema-12 layout while keeping actual persisted business rows.
        connection.exec_driver_sql("ALTER TABLE subscriptions DROP COLUMN use_default_channels")
        connection.exec_driver_sql("DELETE FROM schema_versions WHERE version >= 13")
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO schema_versions VALUES (12, '2026-01-01')"
        )
    database.engine.dispose()

    restarted = Database(url)
    assert restarted.migrate() == 14
    restored_repository = SqlSubscriptionRepository(restarted)
    restored = restored_repository.get(original.id)
    assert restored is not None
    assert restored.channels == ("fixture_channel",)
    assert restored.next_episode == 8
    assert restored.pending_episodes == (3, 5)
    assert restored.version == original.version
    assert restored_repository.cursors(original.id) == {"fixture_channel": 123}
    dynamic = restored_repository.create(
        Subscription(
            title="Default fixture", query="Default fixture", season=1, next_episode=1, channels=()
        )
    )
    restarted.engine.dispose()

    reopened = Database(url)
    assert reopened.migrate() == 14
    reloaded = SqlSubscriptionRepository(reopened).get(dynamic.id)
    assert reloaded is not None and reloaded.channels == ()
    reopened.engine.dispose()


def test_migration_is_versioned_and_idempotent(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'migration.sqlite3'}")

    assert database.migrate() == 14
    assert database.migrate() == 14
    with database.session_factory() as session:
        versions = session.scalars(
            select(SchemaVersionRow.version).order_by(SchemaVersionRow.version)
        ).all()
    assert versions == [14]


def test_version_one_database_upgrades_through_every_schema_version(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'legacy.sqlite3'}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO schema_versions (version, applied_at) VALUES (1, '2026-01-01')"
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE download_tasks (
                id VARCHAR(36) PRIMARY KEY,
                request_id VARCHAR(36) NOT NULL,
                resource_id VARCHAR(36) NOT NULL,
                state VARCHAR(32) NOT NULL,
                desired_state VARCHAR(32),
                expected_size INTEGER NOT NULL,
                downloaded_bytes INTEGER NOT NULL,
                attempt_count INTEGER NOT NULL,
                version INTEGER NOT NULL,
                error_code VARCHAR(120),
                error_message VARCHAR(1000),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

    assert database.migrate() == 14
    with database.engine.connect() as connection:
        columns = {
            str(row[1]) for row in connection.exec_driver_sql("PRAGMA table_info(download_tasks)")
        }
        versions = [
            int(row[0])
            for row in connection.exec_driver_sql(
                "SELECT version FROM schema_versions ORDER BY version"
            )
        ]
        tables = {
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {
        "output_path",
        "next_attempt_at",
        "lease_owner",
        "lease_until",
        "archive_kind",
        "archive_title",
        "archive_year",
        "archive_season",
        "archive_episode",
        "archive_episode_end",
        "archive_confirmed",
        "archive_replace_existing",
        "archive_target_path",
    } <= columns
    assert versions == list(range(1, 15))
    assert {"search_channels", "subscription_candidates"} <= tables


def test_version_eight_subscription_schema_gains_episode_plan(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'version-eight.sqlite3'}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO schema_versions (version, applied_at) VALUES (8, '2026-01-01')"
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE subscriptions (
                id VARCHAR(36) PRIMARY KEY,
                title VARCHAR(300) NOT NULL,
                season INTEGER NOT NULL,
                next_episode INTEGER NOT NULL,
                query VARCHAR(300) NOT NULL,
                enabled BOOLEAN NOT NULL,
                version INTEGER NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

    assert database.migrate() == 14
    with database.engine.connect() as connection:
        columns = {
            str(row[1]) for row in connection.exec_driver_sql("PRAGMA table_info(subscriptions)")
        }
    assert {"pending_episodes", "continue_tracking"} <= columns


def test_version_nine_resource_schema_gains_source_provenance(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'version-nine.sqlite3'}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO schema_versions (version, applied_at) VALUES (9, '2026-01-01')"
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE media_resources (
                id VARCHAR(36) PRIMARY KEY,
                provider VARCHAR(40) NOT NULL,
                locator VARCHAR(1000) NOT NULL,
                file_name VARCHAR(500) NOT NULL,
                file_size INTEGER NOT NULL,
                identity_confidence INTEGER NOT NULL,
                season INTEGER,
                episode_start INTEGER,
                episode_end INTEGER,
                episode_conflict BOOLEAN NOT NULL DEFAULT 0,
                resolution INTEGER,
                codec VARCHAR(30),
                hdr BOOLEAN NOT NULL DEFAULT 0,
                languages VARCHAR(500) NOT NULL DEFAULT '',
                discovered_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO media_resources (
                id, provider, locator, file_name, file_size, identity_confidence,
                episode_conflict, hdr, languages, discovered_at
            ) VALUES (
                'legacy-resource', 'telegram',
                '{"account":"default","chat_id":-100123456,"media_index":0,"message_id":42}',
                'legacy.mkv', 100, 80, 0, 0, '', '2026-01-01'
            )
            """
        )

    assert database.migrate() == 14
    with database.engine.connect() as connection:
        columns = {
            str(row[1]) for row in connection.exec_driver_sql("PRAGMA table_info(media_resources)")
        }
        source = connection.exec_driver_sql(
            "SELECT source_channel_id, source_message_id, source_link "
            "FROM media_resources WHERE id = 'legacy-resource'"
        ).one()

    assert {
        "source_channel_id",
        "source_message_id",
        "source_channel_title",
        "source_channel_username",
        "source_link",
        "forwarded_from_channel_id",
        "forwarded_from_message_id",
        "forwarded_from_title",
        "forwarded_from_username",
        "forwarded_from_link",
    } <= columns
    assert tuple(source) == (-100123456, 42, "https://t.me/c/123456/42")


def test_schema_thirteen_upgrade_adds_scan_tables_without_changing_business_rows(
    tmp_path: Path,
) -> None:
    from telegram_media_automation.domain.subscriptions import Subscription
    from telegram_media_automation.infrastructure.scan_repository import SqlScanRepository
    from telegram_media_automation.infrastructure.subscription_repository import (
        SqlSubscriptionRepository,
    )

    url = f"sqlite:///{tmp_path / 'thirteen.sqlite3'}"
    db = Database(url)
    db.migrate()
    subscriptions = SqlSubscriptionRepository(db)
    sub = subscriptions.create(
        Subscription("Fixture", 1, 4, "Fixture", ("fixture",), pending_episodes=(2,))
    )
    subscriptions.advance_cursor(sub.id, "fixture", 123)
    tables = [
        "subscriptions",
        "subscription_cursors",
        "download_tasks",
        "task_events",
        "subscription_candidates",
    ]
    with db.engine.begin() as connection:
        before = {
            name: connection.exec_driver_sql("SELECT * FROM " + name).fetchall() for name in tables
        }
        for table in ["subscription_scan_history", "subscription_scan_summaries", "worker_gates"]:
            connection.exec_driver_sql("DROP TABLE " + table)
        connection.exec_driver_sql("DELETE FROM schema_versions WHERE version>=14")
        connection.exec_driver_sql("INSERT OR IGNORE INTO schema_versions VALUES(13,'2026-01-01')")
    db.engine.dispose()
    upgraded = Database(url)
    assert upgraded.migrate() == 14
    assert upgraded.migrate() == 14
    with upgraded.engine.connect() as connection:
        after = {
            name: connection.exec_driver_sql("SELECT * FROM " + name).fetchall() for name in tables
        }
    assert after == before
    assert SqlScanRepository(upgraded).history(sub.id) == []
    assert SqlSubscriptionRepository(upgraded).cursors(sub.id) == {"fixture": 123}
    upgraded.engine.dispose()
