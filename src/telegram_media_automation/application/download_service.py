"""Resumable download orchestration independent from Telegram libraries."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import re
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from telegram_media_automation.application.ports import TaskRepository
from telegram_media_automation.application.task_service import TaskNotFound, TaskService
from telegram_media_automation.domain.states import TaskState


class DownloadSource(Protocol):
    """A remote object that supports reading from a byte offset."""

    def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]: ...


class DownloadIntegrityError(RuntimeError):
    """The remote bytes do not match the task's declared size."""


class InsufficientDiskSpace(RuntimeError):
    """The incomplete volume cannot safely hold the remaining bytes."""


class DownloadLeaseLost(RuntimeError):
    """The task is no longer owned by the worker performing the download."""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    task_id: UUID
    state: TaskState
    downloaded_bytes: int
    path: Path | None


def safe_file_name(value: str) -> str:
    """Return a portable leaf name and reject path traversal semantics."""
    leaf = Path(value.replace("\\", "/")).name.strip()
    leaf = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", leaf)
    leaf = leaf.rstrip(". ")
    if leaf in {"", ".", ".."}:
        raise ValueError("file name does not contain a usable leaf name")
    return leaf


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadExecutor:
    def __init__(
        self,
        repository: TaskRepository,
        incomplete_dir: Path,
        inbox_dir: Path,
        *,
        chunk_size: int = 512 * 1024,
        min_free_bytes: int = 0,
        bytes_per_second: int | None = None,
        chunk_timeout: float = 75.0,
        free_space: Callable[[Path], int] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_timeout <= 0:
            raise ValueError("chunk timeout must be positive")
        self.repository = repository
        self.service = TaskService(repository)
        self.incomplete_dir = incomplete_dir
        self.inbox_dir = inbox_dir
        self.chunk_size = chunk_size
        self.min_free_bytes = min_free_bytes
        self.bytes_per_second = bytes_per_second
        self.chunk_timeout = chunk_timeout
        self.free_space = free_space or (lambda path: shutil.disk_usage(path).free)
        self.sleeper = sleeper or asyncio.sleep

    def _assert_lease(self, task_id: UUID, lease_owner: str | None) -> None:
        if lease_owner is None:
            return
        task = self.repository.get(task_id)
        if task is None or task.state != TaskState.DOWNLOADING or task.lease_owner != lease_owner:
            raise DownloadLeaseLost(f"download lease lost for task {task_id}")

    @staticmethod
    def _copy_and_verify(part_path: Path, temporary: Path) -> None:
        with part_path.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if _sha256(part_path) != _sha256(temporary):
            raise DownloadIntegrityError("cross-device inbox commit verification failed")

    async def _commit_part(
        self,
        task_id: UUID,
        part_path: Path,
        final_path: Path,
        lease_owner: str | None,
    ) -> None:
        self._assert_lease(task_id, lease_owner)
        try:
            os.replace(part_path, final_path)
            return
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
        temporary = final_path.with_name(f".{final_path.name}.committing")
        temporary.unlink(missing_ok=True)
        try:
            await asyncio.to_thread(self._copy_and_verify, part_path, temporary)
            self._assert_lease(task_id, lease_owner)
            os.replace(temporary, final_path)
            part_path.unlink()
        finally:
            temporary.unlink(missing_ok=True)

    async def _next_chunk(
        self,
        chunks: AsyncIterator[bytes],
        lease_lost: asyncio.Event | None,
    ) -> bytes:
        if lease_lost is None:
            return await asyncio.wait_for(anext(chunks), timeout=self.chunk_timeout)

        async def next_chunk() -> bytes:
            return await anext(chunks)

        chunk_task = asyncio.create_task(next_chunk())
        lease_task = asyncio.create_task(lease_lost.wait())
        try:
            done, _ = await asyncio.wait(
                {chunk_task, lease_task},
                timeout=self.chunk_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError
            if lease_task in done and lease_task.result():
                raise DownloadLeaseLost("download lease renewal failed")
            return chunk_task.result()
        finally:
            for pending in (chunk_task, lease_task):
                if not pending.done():
                    pending.cancel()
            for pending in (chunk_task, lease_task):
                with suppress(asyncio.CancelledError):
                    await pending

    async def execute(
        self,
        task_id: UUID,
        source: DownloadSource,
        file_name: str,
        *,
        lease_owner: str | None = None,
        lease_lost: asyncio.Event | None = None,
    ) -> DownloadResult:
        task = self.repository.get(task_id)
        if task is None:
            raise TaskNotFound(str(task_id))
        if task.state not in {TaskState.QUEUED, TaskState.RETRYING, TaskState.DOWNLOADING}:
            raise ValueError(f"task {task_id} is not executable from {task.state}")
        self._assert_lease(task_id, lease_owner)

        self.incomplete_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        final_name = f"{task.id}__{safe_file_name(file_name)}"
        part_path = self.incomplete_dir / f"{task.id}.part"
        final_path = self.inbox_dir / final_name

        existing_size = part_path.stat().st_size if part_path.exists() else 0
        if task.state != TaskState.DOWNLOADING:
            task = self.service.transition(task.id, TaskState.DOWNLOADING, actor="download-worker")
        if final_path.exists():
            self._assert_lease(task_id, lease_owner)
            actual_size = final_path.stat().st_size if final_path.is_file() else -1
            if actual_size <= 0 or (task.expected_size and actual_size != task.expected_size):
                self.service.transition(
                    task.id,
                    TaskState.FAILED,
                    reason="existing inbox target does not match the expected resource",
                    actor="download-worker",
                    error_code="download_integrity_error",
                )
                raise DownloadIntegrityError(
                    "existing inbox target does not match the expected resource"
                )
            task = self.service.reconcile_progress(task.id, actual_size)
            task = self.service.transition(
                task.id,
                TaskState.DOWNLOADED,
                reason="recovered verified inbox file after interrupted commit",
                actor="download-worker",
                output_path=str(final_path.resolve()),
            )
            return DownloadResult(task.id, task.state, task.downloaded_bytes, final_path)
        if task.expected_size and existing_size > task.expected_size:
            self.service.transition(
                task.id,
                TaskState.FAILED,
                reason="partial file is larger than the expected resource",
                actor="download-worker",
                error_code="download_integrity_error",
            )
            raise DownloadIntegrityError("partial file is larger than the expected resource")
        remaining = max(0, task.expected_size - existing_size)
        if self.free_space(self.incomplete_dir) < remaining + self.min_free_bytes:
            raise InsufficientDiskSpace(
                f"download requires {remaining} bytes plus {self.min_free_bytes} bytes reserve"
            )
        if task.downloaded_bytes != existing_size:
            task = self.service.reconcile_progress(task.id, existing_size)

        try:
            started_at = time.monotonic()
            transferred = 0
            with part_path.open("ab") as destination:
                chunks = source.chunks(existing_size, self.chunk_size).__aiter__()
                while True:
                    try:
                        chunk = await self._next_chunk(chunks, lease_lost)
                    except StopAsyncIteration:
                        break
                    if not chunk:
                        continue
                    if lease_lost is not None and lease_lost.is_set():
                        raise DownloadLeaseLost("download lease renewal failed")
                    self._assert_lease(task.id, lease_owner)
                    latest = self.repository.get(task.id)
                    if latest is None:
                        raise TaskNotFound(str(task.id))
                    if latest.state in {TaskState.PAUSED, TaskState.CANCELLED}:
                        return DownloadResult(
                            latest.id, latest.state, latest.downloaded_bytes, part_path
                        )
                    destination.write(chunk)
                    destination.flush()
                    transferred += len(chunk)
                    if self.bytes_per_second:
                        target_elapsed = transferred / self.bytes_per_second
                        delay = target_elapsed - (time.monotonic() - started_at)
                        if delay > 0:
                            await self.sleeper(delay)
                    current_size = destination.tell()
                    task = self.service.record_progress(task.id, current_size)
                os.fsync(destination.fileno())

            actual_size = part_path.stat().st_size
            if task.expected_size and actual_size != task.expected_size:
                raise DownloadIntegrityError(
                    f"expected {task.expected_size} bytes but received {actual_size}"
                )
            await self._commit_part(task.id, part_path, final_path, lease_owner)
            task = self.service.transition(
                task.id,
                TaskState.DOWNLOADED,
                actor="download-worker",
                output_path=str(final_path.resolve()),
            )
            return DownloadResult(task.id, task.state, task.downloaded_bytes, final_path)
        except DownloadIntegrityError:
            latest = self.repository.get(task.id)
            if latest is not None and latest.state == TaskState.DOWNLOADING:
                self.service.transition(
                    task.id,
                    TaskState.FAILED,
                    reason="download integrity check failed",
                    actor="download-worker",
                )
            raise
