"""Supervised queue consumer for persisted download tasks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from telegram_media_automation.application.download_service import (
    DownloadExecutor,
    DownloadIntegrityError,
    DownloadLeaseLost,
    DownloadSource,
    InsufficientDiskSpace,
)
from telegram_media_automation.application.ports import TaskRepository
from telegram_media_automation.application.resource_service import ResourceRepository
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.domain.media import MediaKind
from telegram_media_automation.domain.resources import MediaResource
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.domain.tasks import DownloadTask
from telegram_media_automation.security import redact_sensitive


class DownloadSourceGateway(Protocol):
    async def download_source(self, resource: MediaResource) -> DownloadSource: ...


class TaskImporter(Protocol):
    def replace_task(
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
        target_path: Path | None = None,
    ) -> object: ...

    def import_task(
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
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class WorkerResult:
    worked: bool
    task_id: UUID | None = None
    state: TaskState | None = None
    retry_after: float | None = None


class DownloadWorker:
    def __init__(
        self,
        tasks: TaskRepository,
        resources: ResourceRepository,
        gateway: DownloadSourceGateway,
        executor: DownloadExecutor,
        *,
        max_attempts: int = 5,
        retry_delay: float = 30.0,
        lease_seconds: float = 60.0,
        worker_id: str | None = None,
        importer: TaskImporter | None = None,
    ) -> None:
        self.tasks = tasks
        self.resources = resources
        self.gateway = gateway
        self.executor = executor
        self.service = TaskService(tasks)
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        if lease_seconds <= 0:
            raise ValueError("lease duration must be positive")
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or str(uuid4())
        self.importer = importer

    async def _renew_lease(self, task_id: UUID, lease_lost: asyncio.Event) -> None:
        interval = max(0.1, min(30.0, self.lease_seconds / 3))
        try:
            while True:
                await asyncio.sleep(interval)
                if not self.tasks.renew_lease(task_id, self.worker_id, self.lease_seconds):
                    lease_lost.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            lease_lost.set()

    def _verified_inbox_file(self, task: DownloadTask) -> Path | None:
        if not task.output_path:
            return None
        path = Path(task.output_path).resolve()
        inbox = self.executor.inbox_dir.resolve()
        if not path.is_file() or inbox not in path.parents:
            return None
        size = path.stat().st_size
        if size <= 0 or (task.expected_size and size != task.expected_size):
            return None
        return path

    def _finish_with_optional_import(self, task: DownloadTask) -> WorkerResult:
        if task.state == TaskState.DOWNLOADED and task.archive_plan is not None and self.importer:
            plan = task.archive_plan
            operation = (
                self.importer.replace_task if plan.replace_existing else self.importer.import_task
            )
            extra = (
                {"target_path": Path(plan.target_path) if plan.target_path else None}
                if plan.replace_existing
                else {}
            )
            imported = operation(
                task.id,
                kind=plan.kind,
                title=plan.title,
                year=plan.year,
                season=plan.season,
                episode=plan.episode,
                episode_end=plan.episode_end,
                confirmed_identity=plan.confirmed_identity,
                **extra,
            )
            return WorkerResult(True, task.id, getattr(imported, "state", task.state))
        return WorkerResult(True, task.id, task.state)

    async def run_once(self) -> WorkerResult:
        task = self.tasks.claim_next(self.worker_id, self.lease_seconds)
        if task is None:
            return WorkerResult(False)
        resource = self.resources.get(task.resource_id)
        if resource is None:
            task = self.service.transition(
                task.id,
                TaskState.FAILED,
                reason="selected resource no longer exists",
                actor="download-worker",
                error_code="resource_missing",
            )
            return WorkerResult(True, task.id, task.state)

        lease_lost = asyncio.Event()
        renewal_task = asyncio.create_task(self._renew_lease(task.id, lease_lost))
        try:
            existing = self._verified_inbox_file(task)
            if existing is not None:
                completed = self.service.transition(
                    task.id,
                    TaskState.DOWNLOADED,
                    reason="reused verified inbox file after import retry",
                    actor="download-worker",
                    output_path=str(existing),
                )
                return self._finish_with_optional_import(completed)
            source = await self.gateway.download_source(resource)
            result = await self.executor.execute(
                task.id,
                source,
                resource.file_name,
                lease_owner=self.worker_id,
                lease_lost=lease_lost,
            )
            stored = self.tasks.get(task.id)
            if stored is not None:
                return self._finish_with_optional_import(stored)
            return WorkerResult(True, result.task_id, result.state)
        except DownloadIntegrityError:
            return WorkerResult(True, task.id, TaskState.FAILED)
        except DownloadLeaseLost:
            latest = self.tasks.get(task.id)
            return WorkerResult(True, task.id, latest.state if latest else None)
        except Exception as error:
            latest = self.tasks.get(task.id)
            if latest is None:
                raise
            if latest.state in {TaskState.PAUSED, TaskState.CANCELLED, TaskState.FAILED}:
                return WorkerResult(True, latest.id, latest.state)
            if latest.state == TaskState.DOWNLOADING and latest.lease_owner != self.worker_id:
                return WorkerResult(True, latest.id, latest.state)
            permanent = isinstance(error, (ValueError, LookupError))
            retry_without_limit = isinstance(error, InsufficientDiskSpace) or bool(
                getattr(error, "seconds", 0)
            )
            target = (
                TaskState.FAILED
                if permanent
                or (latest.attempt_count >= self.max_attempts and not retry_without_limit)
                else TaskState.RETRYING
            )
            retry_after = float(getattr(error, "seconds", 0) or self.retry_delay)
            safe_error = redact_sensitive(str(error))
            latest = self.service.transition(
                latest.id,
                target,
                reason=safe_error,
                actor="download-worker",
                error_code=type(error).__name__,
                retry_after_seconds=retry_after if target == TaskState.RETRYING else None,
            )
            return WorkerResult(
                True,
                latest.id,
                latest.state,
                retry_after if target == TaskState.RETRYING else None,
            )
        finally:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task

    async def run_forever(self, poll_interval: float, stop: asyncio.Event | None = None) -> None:
        stop_event = stop or asyncio.Event()
        while not stop_event.is_set():
            result = await self.run_once()
            if not result.worked:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
