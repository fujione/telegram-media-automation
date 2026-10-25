import zipfile
from pathlib import Path

import pytest
from sqlalchemy import text

from telegram_media_automation.infrastructure.backup import (
    BackupFormatError,
    create_backup,
    restore_backup,
)
from telegram_media_automation.infrastructure.database import Database


def test_backup_and_restore_consistent_database_and_optional_session(tmp_path: Path) -> None:
    source_database = tmp_path / "source/data/media_automation.sqlite3"
    database = Database(f"sqlite:///{source_database}")
    database.migrate()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO media_resources "
                "(id,provider,locator,file_name,file_size,identity_confidence,"
                "hdr,languages,discovered_at) "
                "VALUES ('id','telegram','locator','file.mkv',1,90,0,'','2026-01-01')"
            )
        )
    session = tmp_path / "source/data/sessions/telegram.session"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"session")
    archive = tmp_path / "backups/product.zip"

    result = create_backup(source_database, archive, telegram_session=session)
    restored_database, restored_session = restore_backup(archive, tmp_path / "restored")

    assert result.included_session
    restored = Database(f"sqlite:///{restored_database}")
    with restored.engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT file_name FROM media_resources").scalar()
            == "file.mkv"
        )
    assert restored_session is not None and restored_session.read_bytes() == b"session"

    with pytest.raises(FileExistsError):
        restore_backup(archive, tmp_path / "restored")


def test_restore_rejects_tampered_member_before_installing_database(tmp_path: Path) -> None:
    source_database = tmp_path / "source.sqlite3"
    Database(f"sqlite:///{source_database}").migrate()
    original = tmp_path / "original.zip"
    tampered = tmp_path / "tampered.zip"
    create_backup(source_database, original)
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        target.writestr("manifest.json", source.read("manifest.json"))
        target.writestr("database.sqlite3", source.read("database.sqlite3") + b"tampered")

    destination = tmp_path / "restored"
    with pytest.raises(BackupFormatError, match="checksum mismatch"):
        restore_backup(tampered, destination)

    assert not (destination / "data/media_automation.sqlite3").exists()
