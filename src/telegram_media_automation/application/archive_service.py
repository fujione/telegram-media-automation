"""Safe inbox-to-library file importing."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from telegram_media_automation.application.download_service import safe_file_name
from telegram_media_automation.domain.media import IdentityStatus, MediaItem, MediaKind


class ArchiveConflict(FileExistsError):
    """A different file already occupies the desired library path."""


class ArchiveModeUnavailable(OSError):
    """The configured archive mode cannot be used for the selected paths."""


ArchiveMode = Literal["move", "hardlink", "copy"]


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    path: Path
    provisional: bool
    deduplicated: bool
    mode: ArchiveMode
    replaced: bool = False
    backup_path: Path | None = None


def safe_directory_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip()).rstrip(". ")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("directory name is empty after sanitization")
    return cleaned


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArchiveService:
    def __init__(
        self,
        inbox_dir: Path,
        movie_root: Path,
        tv_root: Path,
        *,
        mode: ArchiveMode = "move",
    ) -> None:
        if mode not in {"move", "hardlink", "copy"}:
            raise ValueError("archive mode must be move, hardlink or copy")
        self.inbox_dir = inbox_dir
        self.movie_root = movie_root
        self.tv_root = tv_root
        self.mode = mode

    def list_files(self, kind: MediaKind, query: str = "", limit: int = 100) -> list[Path]:
        root = self.movie_root if kind == MediaKind.MOVIE else self.tv_root
        allowed = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".ts", ".webm"}
        found: list[Path] = []
        if not root.is_dir():
            return found
        for folder in root.iterdir():
            if folder.name.startswith(".") or folder.is_symlink() or not folder.is_dir():
                continue
            directories = [folder]
            if kind == MediaKind.SERIES:
                directories = [
                    season
                    for season in folder.iterdir()
                    if not season.name.startswith(".")
                    and not season.is_symlink()
                    and season.is_dir()
                ]
            for directory in directories:
                for path in directory.iterdir():
                    if path.name.startswith(".") or path.is_symlink() or not path.is_file():
                        continue
                    if path.suffix.casefold() not in allowed:
                        continue
                    if query.casefold() in str(path.relative_to(root)).casefold():
                        found.append(path.resolve())
                        if len(found) >= limit:
                            return found
        return found

    @staticmethod
    def _verified_copy(source: Path, destination: Path) -> None:
        temporary = destination.with_name(f".{destination.name}.importing-{uuid4().hex}")
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            if _sha256(source) != _sha256(temporary):
                raise OSError("archive copy verification failed")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def import_file(
        self,
        source: Path,
        media: MediaItem,
        *,
        season: int | None = None,
        episode: int | None = None,
        episode_end: int | None = None,
        replace_existing: bool = False,
        target_path: Path | None = None,
    ) -> ArchiveResult:
        source = source.resolve()
        inbox = self.inbox_dir.resolve()
        if not source.is_file() or inbox not in source.parents:
            raise ValueError("source must be an existing file inside the configured inbox")

        folder = safe_directory_name(media.title)
        if media.year is not None:
            folder = f"{folder} ({media.year})"
        if media.kind == MediaKind.MOVIE:
            if season is not None or episode is not None or episode_end is not None:
                raise ValueError("movie imports cannot specify season or episode")
            destination_dir = self.movie_root / folder
            library_root = self.movie_root
            library_name = folder
        else:
            if season is None or episode is None or season < 0 or episode <= 0:
                raise ValueError("series imports require a valid season and episode")
            destination_dir = self.tv_root / folder / f"Season {season:02d}"
            library_root = self.tv_root
            episode_suffix = f"S{season:02d}E{episode:02d}"
            if episode_end is not None:
                if episode_end < episode:
                    raise ValueError("series episode range is invalid")
                if episode_end > episode:
                    episode_suffix += f"-E{episode_end:02d}"
            library_name = f"{safe_directory_name(media.title)} - {episode_suffix}"

        original_name = source.name.split("__", 1)[-1]
        extension = Path(safe_file_name(original_name)).suffix.casefold()
        destination = destination_dir / f"{library_name}{extension}"
        if target_path is not None:
            if not replace_existing:
                raise ValueError("explicit target paths are only supported for replacement")
            destination = target_path
            destination_dir = destination.parent
            if destination.suffix.casefold() != extension:
                raise ValueError("replacement target and new file must have the same extension")
        root = library_root.resolve()
        if root not in destination.resolve().parents:
            raise ValueError("destination must stay inside the configured media library")
        if any(part.startswith(".") for part in destination.resolve().relative_to(root).parts):
            raise ValueError("hidden library paths cannot be replacement targets")
        current = destination
        while current != library_root and current != current.parent:
            if current.is_symlink():
                raise ValueError("library destination cannot contain symbolic links")
            current = current.parent
        if replace_existing and not destination.exists():
            raise ArchiveConflict(f"replacement target does not exist: {destination}")
        destination_dir.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_file() and _sha256(source) == _sha256(destination):
                if self.mode == "move":
                    source.unlink()
                return ArchiveResult(
                    destination,
                    provisional=media.identity_status != IdentityStatus.CONFIRMED,
                    deduplicated=True,
                    mode=self.mode,
                )
            if not destination.is_file() or not replace_existing:
                raise ArchiveConflict(str(destination))

            backup_root = root / ".tma-replaced-backups"
            if backup_root.is_symlink():
                raise ValueError("replacement backup directory cannot be a symbolic link")
            backup_dir = backup_root
            if root not in backup_dir.resolve().parents:
                raise ValueError("replacement backup must stay inside the media library")
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            backup = backup_dir / (
                f"{destination.stem}.replaced-{stamp}-{uuid4().hex[:8]}{destination.suffix}"
            )
            staged = destination.with_name(f".{destination.name}.replacing-{uuid4().hex}")
            lock = destination.with_name(f".{destination.name}.replacement-lock")
            with lock.open("x"):
                pass
            try:
                original_digest = _sha256(destination)
                if self.mode == "hardlink":
                    try:
                        os.link(source, staged)
                    except OSError as error:
                        if error.errno == errno.EXDEV:
                            raise ArchiveModeUnavailable(
                                "hardlink archive mode requires inbox and library "
                                "on the same filesystem"
                            ) from error
                        raise
                else:
                    self._verified_copy(source, staged)
                if _sha256(source) != _sha256(staged):
                    raise OSError("replacement staging verification failed")
                if _sha256(destination) != original_digest:
                    raise ArchiveConflict("replacement target changed during staging")
                try:
                    os.link(destination, backup)
                except OSError:
                    self._verified_copy(destination, backup)
                if _sha256(backup) != original_digest:
                    raise OSError("replacement backup verification failed")
                os.replace(staged, destination)
            finally:
                staged.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)
            if self.mode == "move":
                with suppress(OSError):
                    source.unlink()
            return ArchiveResult(
                destination,
                provisional=media.identity_status != IdentityStatus.CONFIRMED,
                deduplicated=False,
                mode=self.mode,
                replaced=True,
                backup_path=backup,
            )

        if self.mode == "hardlink":
            try:
                os.link(source, destination)
            except OSError as error:
                if error.errno == errno.EXDEV:
                    raise ArchiveModeUnavailable(
                        "hardlink archive mode requires inbox and library on the same filesystem"
                    ) from error
                raise
        elif self.mode == "copy":
            self._verified_copy(source, destination)
        else:
            try:
                os.replace(source, destination)
            except OSError as error:
                if error.errno != errno.EXDEV:
                    raise
                self._verified_copy(source, destination)
                source.unlink()

        return ArchiveResult(
            destination,
            provisional=media.identity_status != IdentityStatus.CONFIRMED,
            deduplicated=False,
            mode=self.mode,
        )
