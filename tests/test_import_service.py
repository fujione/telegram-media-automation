from pathlib import Path

from telegram_media_automation.application.archive_service import ArchiveService
from telegram_media_automation.application.import_service import ImportService
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.domain.media import MediaKind
from telegram_media_automation.domain.resources import MediaResource
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.resource_repository import SqlResourceRepository
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository


def test_confirmed_download_is_imported_and_final_path_is_persisted(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'import.sqlite3'}")
    database.migrate()
    tasks = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    resource = resources.upsert_many([MediaResource("telegram", "movie", "Movie.2026.mkv", 5, 90)])[
        0
    ]
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / f"task__{resource.file_name}"
    source.write_bytes(b"movie")
    task_service = TaskService(tasks)
    task = task_service.create_task(resource.id, resource.id, 5)
    task_service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    task_service.transition(task.id, TaskState.QUEUED)
    task_service.transition(task.id, TaskState.DOWNLOADING)
    task_service.record_progress(task.id, 5)
    task = task_service.transition(task.id, TaskState.DOWNLOADED, output_path=str(source.resolve()))
    importer = ImportService(
        tasks,
        resources,
        ArchiveService(inbox, tmp_path / "movies", tmp_path / "tv"),
    )

    imported = importer.import_task(
        task.id,
        kind=MediaKind.MOVIE,
        title="Movie",
        year=2026,
        season=None,
        episode=None,
        confirmed_identity=True,
    )

    assert imported.state == TaskState.IMPORTED
    assert imported.output_path is not None
    assert Path(imported.output_path).read_bytes() == b"movie"


def test_downloaded_task_can_replace_existing_library_file(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'replace.sqlite3'}")
    database.migrate()
    tasks = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    resource = resources.upsert_many([MediaResource("telegram", "replacement", "new.mkv", 3, 90)])[
        0
    ]
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    movies = tmp_path / "movies"
    target = movies / "Movie" / "Movie.mkv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    source = inbox / f"task__{resource.file_name}"
    source.write_bytes(b"new")
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 3)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    service.transition(task.id, TaskState.DOWNLOADING)
    service.record_progress(task.id, 3)
    task = service.transition(task.id, TaskState.DOWNLOADED, output_path=str(source.resolve()))
    importer = ImportService(tasks, resources, ArchiveService(inbox, movies, tmp_path / "tv"))

    replaced = importer.replace_task(
        task.id,
        kind=MediaKind.MOVIE,
        title="Movie",
        year=None,
        season=None,
        episode=None,
        confirmed_identity=True,
    )

    assert replaced.state == TaskState.IMPORTED
    assert target.read_bytes() == b"new"
    backups = list((movies / ".tma-replaced-backups").rglob("*.mkv"))
    assert backups[0].read_bytes() == b"old"
