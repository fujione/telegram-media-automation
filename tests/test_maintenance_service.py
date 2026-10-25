import os
from pathlib import Path
from uuid import uuid4

import pytest

from telegram_media_automation.application.maintenance_service import FileMaintenanceService
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository


def maintenance(tmp_path: Path, now: float = 100_000) -> tuple[FileMaintenanceService, TaskService]:
    database = Database(f"sqlite:///{tmp_path / 'tasks.sqlite3'}")
    database.create_schema_for_development()
    repository = SqlTaskRepository(database)
    return (
        FileMaintenanceService(
            repository,
            tmp_path / "incomplete",
            tmp_path / "inbox",
            tmp_path / "movies",
            tmp_path / "tv",
            retention_seconds=3600,
            clock=lambda: now,
        ),
        TaskService(repository),
    )


def stale(path: Path, now: float = 100_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stale")
    os.utime(path, (now - 7200, now - 7200))
    return path


def test_removes_stale_cancelled_and_orphan_parts_but_keeps_resumable_files(
    tmp_path: Path,
) -> None:
    cleaner, tasks = maintenance(tmp_path)
    cancelled = tasks.create_task(uuid4(), uuid4(), 5)
    tasks.transition(cancelled.id, TaskState.CANCELLED)
    cancelled_part = stale(tmp_path / "incomplete" / f"{cancelled.id}.part")

    paused = tasks.create_task(uuid4(), uuid4(), 5)
    tasks.transition(paused.id, TaskState.CANDIDATE_SELECTED)
    tasks.transition(paused.id, TaskState.QUEUED)
    tasks.transition(paused.id, TaskState.PAUSED)
    paused_part = stale(tmp_path / "incomplete" / f"{paused.id}.part")

    orphan = stale(tmp_path / "incomplete" / f"{uuid4()}.part")
    unknown = stale(tmp_path / "incomplete" / "manual.part")

    report = cleaner.run_once()

    assert report.removed == 2
    assert report.reclaimed_bytes == 10
    assert not cancelled_part.exists()
    assert not orphan.exists()
    assert paused_part.exists()
    assert unknown.exists()


def test_removes_only_stale_product_temporary_names(tmp_path: Path) -> None:
    cleaner, _ = maintenance(tmp_path)
    commit = stale(tmp_path / "inbox" / ".task__movie.mkv.committing")
    movie_import = stale(
        tmp_path / "movies" / "Movie" / ".Movie.mkv.importing-0123456789abcdef0123456789abcdef"
    )
    tv_import = stale(
        tmp_path / "tv" / "Show" / ".Show.mkv.importing-fedcba9876543210fedcba9876543210"
    )
    recent = tmp_path / "inbox" / ".recent.mkv.committing"
    recent.parent.mkdir(parents=True, exist_ok=True)
    recent.write_bytes(b"recent")
    unrelated = stale(tmp_path / "movies" / "Movie" / ".user-temporary")

    report = cleaner.run_once()

    assert report.removed == 3
    assert not commit.exists()
    assert not movie_import.exists()
    assert not tv_import.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_cleanup_retention_cannot_be_dangerously_short(tmp_path: Path) -> None:
    cleaner, _ = maintenance(tmp_path)

    with pytest.raises(ValueError, match="at least one hour"):
        FileMaintenanceService(
            cleaner.tasks,
            tmp_path / "incomplete",
            tmp_path / "inbox",
            tmp_path / "movies",
            tmp_path / "tv",
            retention_seconds=3599,
        )
