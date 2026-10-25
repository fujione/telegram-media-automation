"""Consistent SQLite backup and conservative restore support."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url


class BackupFormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BackupResult:
    archive: Path
    included_session: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_database_path(database_url: str) -> Path:
    parsed = make_url(database_url)
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        raise ValueError("backup currently supports file-backed SQLite databases only")
    if parsed.database == ":memory:":
        raise ValueError("an in-memory database cannot be backed up")
    return Path(parsed.database).resolve()


def create_backup(
    database_path: Path,
    archive: Path,
    *,
    telegram_session: Path | None = None,
) -> BackupResult:
    database_path = database_path.resolve()
    archive = archive.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if archive.exists():
        raise FileExistsError(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    include_session = telegram_session is not None and telegram_session.is_file()

    with tempfile.TemporaryDirectory(
        prefix="telegram-media-backup-", dir=archive.parent
    ) as temporary:
        snapshot = Path(temporary) / "database.sqlite3"
        with (
            closing(sqlite3.connect(database_path)) as source,
            closing(sqlite3.connect(snapshot)) as destination,
        ):
            source.backup(destination)
        manifest = {
            "format": "telegram-media-automation-backup",
            "version": 2,
            "created_at": datetime.now(UTC).isoformat(),
            "includes_session": include_session,
            "sha256": {
                "database.sqlite3": _sha256(snapshot),
                **(
                    {"telegram.session": _sha256(telegram_session)}
                    if include_session and telegram_session is not None
                    else {}
                ),
            },
        }
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(snapshot, "database.sqlite3")
            bundle.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            if include_session and telegram_session is not None:
                bundle.write(telegram_session, "telegram.session")
    return BackupResult(archive, include_session)


def _write_member(
    bundle: zipfile.ZipFile, member: str, destination: Path, expected_sha256: str
) -> None:
    temporary = destination.with_name(f".{destination.name}.restoring")
    if temporary.exists():
        raise FileExistsError(temporary)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with bundle.open(member) as source, temporary.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if _sha256(temporary) != expected_sha256:
            raise BackupFormatError(f"backup member checksum mismatch: {member}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore_backup(archive: Path, root: Path) -> tuple[Path, Path | None]:
    archive = archive.resolve()
    root = root.resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)
    database = root / "data/media_automation.sqlite3"
    session = root / "data/sessions/telegram.session"
    if database.exists():
        raise FileExistsError(f"refusing to replace existing database: {database}")

    with zipfile.ZipFile(archive) as bundle:
        try:
            manifest = json.loads(bundle.read("manifest.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise BackupFormatError("backup manifest is missing or invalid") from error
        if (
            manifest.get("format") != "telegram-media-automation-backup"
            or manifest.get("version") != 2
        ):
            raise BackupFormatError("unsupported backup format or version")
        checksums = manifest.get("sha256")
        if not isinstance(checksums, dict) or not isinstance(
            checksums.get("database.sqlite3"), str
        ):
            raise BackupFormatError("backup checksums are missing or invalid")
        if "database.sqlite3" not in bundle.namelist():
            raise BackupFormatError("backup database is missing")
        includes_session = bool(manifest.get("includes_session"))
        if includes_session and session.exists():
            raise FileExistsError(f"refusing to replace existing Telegram session: {session}")
        _write_member(bundle, "database.sqlite3", database, checksums["database.sqlite3"])
        if includes_session:
            if "telegram.session" not in bundle.namelist():
                database.unlink(missing_ok=True)
                raise BackupFormatError("manifest declares a missing Telegram session")
            if not isinstance(checksums.get("telegram.session"), str):
                database.unlink(missing_ok=True)
                raise BackupFormatError("Telegram session checksum is missing")
            try:
                _write_member(bundle, "telegram.session", session, checksums["telegram.session"])
            except Exception:
                database.unlink(missing_ok=True)
                raise
    return database, session if includes_session else None
