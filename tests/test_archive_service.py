import errno
import os
from pathlib import Path

import pytest

from telegram_media_automation.application.archive_service import (
    ArchiveConflict,
    ArchiveModeUnavailable,
    ArchiveService,
)
from telegram_media_automation.domain.media import IdentityStatus, MediaItem, MediaKind


def service(tmp_path: Path, mode: str = "move") -> ArchiveService:
    return ArchiveService(  # type: ignore[arg-type]
        tmp_path / "inbox", tmp_path / "movies", tmp_path / "tv", mode=mode
    )


def inbox_file(tmp_path: Path, name: str, content: bytes = b"media") -> Path:
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    path = inbox / name
    path.write_bytes(content)
    return path


def test_movie_is_moved_into_stable_library_path(tmp_path: Path) -> None:
    source = inbox_file(tmp_path, "task-id__Movie.Name.2026.mkv")
    media = MediaItem(
        kind=MediaKind.MOVIE,
        title="Movie: Name",
        year=2026,
        identity_status=IdentityStatus.CONFIRMED,
    )

    result = service(tmp_path).import_file(source, media)

    assert result.path == tmp_path / "movies" / "Movie_ Name (2026)" / "Movie_ Name (2026).mkv"
    assert result.path.read_bytes() == b"media"
    assert not result.provisional
    assert not source.exists()
    assert result.mode == "move"


def test_series_requires_episode_identity_and_marks_unconfirmed_import(tmp_path: Path) -> None:
    media = MediaItem(kind=MediaKind.SERIES, title="A Show")
    source = inbox_file(tmp_path, "task__A.Show.S01E02.mkv")

    result = service(tmp_path).import_file(source, media, season=1, episode=2)

    assert result.path == tmp_path / "tv" / "A Show" / "Season 01" / "A Show - S01E02.mkv"
    assert result.provisional


def test_multi_episode_file_uses_range_name(tmp_path: Path) -> None:
    media = MediaItem(
        kind=MediaKind.SERIES,
        title="A Show",
        identity_status=IdentityStatus.CONFIRMED,
    )
    source = inbox_file(tmp_path, "bundle.mkv", b"bundle")

    result = service(tmp_path).import_file(source, media, season=1, episode=2, episode_end=3)

    assert result.path.name == "A Show - S01E02-E03.mkv"


def test_conflicting_destination_is_never_overwritten(tmp_path: Path) -> None:
    media = MediaItem(kind=MediaKind.MOVIE, title="Movie")
    archive = service(tmp_path)
    first = inbox_file(tmp_path, "first__movie.mkv", b"first")
    archive.import_file(first, media)
    second = inbox_file(tmp_path, "second__movie.mkv", b"second")

    with pytest.raises(ArchiveConflict):
        archive.import_file(second, media)

    assert second.read_bytes() == b"second"
    assert (tmp_path / "movies" / "Movie" / "Movie.mkv").read_bytes() == b"first"


def test_replace_existing_stages_new_file_and_retains_hidden_backup(tmp_path: Path) -> None:
    media = MediaItem(kind=MediaKind.MOVIE, title="Movie")
    archive = service(tmp_path)
    archive.import_file(inbox_file(tmp_path, "first__movie.mkv", b"first"), media)
    replacement = inbox_file(tmp_path, "second__movie.mkv", b"second")

    result = archive.import_file(replacement, media, replace_existing=True)

    assert result.replaced
    assert result.backup_path is not None
    assert result.path.read_bytes() == b"second"
    assert result.backup_path.read_bytes() == b"first"
    assert ".tma-replaced-backups" in result.backup_path.parts
    assert not replacement.exists()


def test_failed_replacement_commit_keeps_original_and_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = MediaItem(kind=MediaKind.MOVIE, title="Movie")
    archive = service(tmp_path)
    first = archive.import_file(inbox_file(tmp_path, "first__movie.mkv", b"old"), media)
    replacement = inbox_file(tmp_path, "second__movie.mkv", b"new")
    real_replace = os.replace

    def fail_commit(source: Path, destination: Path) -> None:
        if ".replacing-" in source.name and destination == first.path:
            raise OSError("simulated commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_commit)
    with pytest.raises(OSError, match="commit failure"):
        archive.import_file(replacement, media, replace_existing=True)

    assert first.path.read_bytes() == b"old"
    assert replacement.read_bytes() == b"new"
    assert not list(first.path.parent.glob("*.replacement-lock"))
    assert not list(first.path.parent.glob("*.replacing-*"))


def test_replacement_supports_verified_legacy_library_path(tmp_path: Path) -> None:
    archive = service(tmp_path, "copy")
    target = tmp_path / "tv" / "English Title (2026)" / "Season 1" / "剧名 - S01E22.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    replacement = inbox_file(tmp_path, "new__22.mp4", b"new")
    result = archive.import_file(
        replacement,
        MediaItem(kind=MediaKind.SERIES, title="剧名"),
        season=1,
        episode=22,
        replace_existing=True,
        target_path=target,
    )
    assert result.path == target
    assert target.read_bytes() == b"new"
    assert replacement.exists()
    assert archive.list_files(MediaKind.SERIES, "剧名") == [target.resolve()]


def test_replacement_rejects_missing_or_outside_target(tmp_path: Path) -> None:
    archive = service(tmp_path)
    source = inbox_file(tmp_path, "new__movie.mkv")
    media = MediaItem(kind=MediaKind.MOVIE, title="Movie")
    with pytest.raises(ArchiveConflict, match="does not exist"):
        archive.import_file(source, media, replace_existing=True)
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"old")
    with pytest.raises(ValueError, match="inside"):
        archive.import_file(source, media, replace_existing=True, target_path=outside)
    assert outside.read_bytes() == b"old"
    assert source.exists()


def test_identical_destination_is_deduplicated(tmp_path: Path) -> None:
    media = MediaItem(kind=MediaKind.MOVIE, title="Movie")
    archive = service(tmp_path)
    archive.import_file(inbox_file(tmp_path, "one__movie.mkv"), media)
    duplicate = inbox_file(tmp_path, "two__movie.mkv")

    result = archive.import_file(duplicate, media)

    assert result.deduplicated
    assert not duplicate.exists()


def test_source_outside_inbox_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"media")
    with pytest.raises(ValueError):
        service(tmp_path).import_file(outside, MediaItem(kind=MediaKind.MOVIE, title="Movie"))


def test_copy_mode_retains_independent_inbox_file(tmp_path: Path) -> None:
    source = inbox_file(tmp_path, "copy__movie.mkv", b"copy-me")

    result = service(tmp_path, "copy").import_file(
        source, MediaItem(kind=MediaKind.MOVIE, title="Movie")
    )

    assert result.mode == "copy"
    assert source.read_bytes() == b"copy-me"
    assert result.path.read_bytes() == b"copy-me"
    assert not os.path.samefile(source, result.path)


def test_hardlink_mode_retains_same_file_in_inbox(tmp_path: Path) -> None:
    source = inbox_file(tmp_path, "link__movie.mkv", b"link-me")

    result = service(tmp_path, "hardlink").import_file(
        source, MediaItem(kind=MediaKind.MOVIE, title="Movie")
    )

    assert result.mode == "hardlink"
    assert source.exists()
    assert os.path.samefile(source, result.path)


def test_hardlink_mode_does_not_silently_copy_across_filesystems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = inbox_file(tmp_path, "link__movie.mkv")

    def cross_device_link(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", cross_device_link)

    with pytest.raises(ArchiveModeUnavailable, match="same filesystem"):
        service(tmp_path, "hardlink").import_file(
            source, MediaItem(kind=MediaKind.MOVIE, title="Movie")
        )

    assert source.exists()
    assert not (tmp_path / "movies" / "Movie" / "Movie.mkv").exists()


def test_invalid_archive_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="archive mode"):
        service(tmp_path, "symlink")
