"""Import completed downloads into the standalone media library."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from telegram_media_automation.application.archive_service import ArchiveService
from telegram_media_automation.application.ports import TaskRepository
from telegram_media_automation.application.resource_service import ResourceRepository
from telegram_media_automation.application.task_service import TaskNotFound, TaskService
from telegram_media_automation.domain.media import IdentityStatus, MediaItem, MediaKind
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.domain.tasks import DownloadTask


class ImportService:
    def __init__(
        self,
        tasks: TaskRepository,
        resources: ResourceRepository,
        archive: ArchiveService,
    ) -> None:
        self.tasks = tasks
        self.resources = resources
        self.archive = archive
        self.service = TaskService(tasks)

    def import_task(
        self,
        task_id: UUID,
        *,
        kind: MediaKind,
        title: str,
        year: int | None,
        season: int | None,
        episode: int | None,
        episode_end: int | None = None,
        confirmed_identity: bool,
    ) -> DownloadTask:
        return self._import_task(
            task_id,
            kind=kind,
            title=title,
            year=year,
            season=season,
            episode=episode,
            episode_end=episode_end,
            confirmed_identity=confirmed_identity,
            replace_existing=False,
        )

    def replace_task(
        self,
        task_id: UUID,
        *,
        kind: MediaKind,
        title: str,
        year: int | None,
        season: int | None,
        episode: int | None,
        episode_end: int | None = None,
        confirmed_identity: bool,
        target_path: Path | None = None,
    ) -> DownloadTask:
        if not confirmed_identity:
            raise ValueError("replacement requires confirmed media identity")
        return self._import_task(
            task_id,
            kind=kind,
            title=title,
            year=year,
            season=season,
            episode=episode,
            episode_end=episode_end,
            confirmed_identity=confirmed_identity,
            replace_existing=True,
            target_path=target_path,
        )

    def _import_task(
        self,
        task_id: UUID,
        *,
        kind: MediaKind,
        title: str,
        year: int | None,
        season: int | None,
        episode: int | None,
        episode_end: int | None,
        confirmed_identity: bool,
        replace_existing: bool,
        target_path: Path | None = None,
    ) -> DownloadTask:
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskNotFound(str(task_id))
        if task.state != TaskState.DOWNLOADED or task.output_path is None:
            raise ValueError("only a completed inbox download can be imported")
        if self.resources.get(task.resource_id) is None:
            raise LookupError("task resource no longer exists")
        source = Path(task.output_path)
        if source.is_symlink() or not source.is_file():
            raise ValueError("completed download must be an existing regular inbox file")
        actual_size = source.stat().st_size
        if actual_size <= 0 or (task.expected_size and actual_size != task.expected_size):
            raise ValueError("completed download size does not match the selected resource")
        media = MediaItem(
            kind=kind,
            title=title,
            year=year,
            identity_status=(
                IdentityStatus.CONFIRMED if confirmed_identity else IdentityStatus.PROVISIONAL
            ),
        )
        task = self.service.transition(task.id, TaskState.IMPORTING, actor="import-worker")
        try:
            result = self.archive.import_file(
                source,
                media,
                season=season,
                episode=episode,
                episode_end=episode_end,
                replace_existing=replace_existing,
                target_path=target_path,
            )
        except Exception as error:
            self.service.transition(
                task.id,
                TaskState.FAILED,
                reason=str(error),
                actor="import-worker",
                error_code=type(error).__name__,
            )
            raise
        target = TaskState.PROVISIONAL if result.provisional else TaskState.IMPORTED
        return self.service.transition(
            task.id,
            target,
            actor="import-worker",
            reason=(
                f"replaced existing library file; backup={result.backup_path}"
                if result.replaced
                else None
            ),
            output_path=str(result.path.resolve()),
        )
