import errno
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from telegram_media_automation.application.download_service import (
    DownloadExecutor,
    DownloadIntegrityError,
    InsufficientDiskSpace,
    safe_file_name,
)
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository


class BytesSource:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.requested_offsets: list[int] = []

    async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
        self.requested_offsets.append(offset)
        for position in range(offset, len(self.value), chunk_size):
            yield self.value[position : position + chunk_size]


class StateChangingSource:
    def __init__(
        self,
        repository: SqlTaskRepository,
        task_id: UUID,
        target: TaskState,
    ) -> None:
        self.repository = repository
        self.task_id = task_id
        self.target = target

    async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
        assert offset == 0
        yield b"abc"
        TaskService(self.repository).transition(self.task_id, self.target)
        yield b"def"


def queued_task(tmp_path: Path, expected_size: int) -> tuple[SqlTaskRepository, object]:
    database = Database(f"sqlite:///{tmp_path / 'tasks.sqlite3'}")
    database.create_schema_for_development()
    repository = SqlTaskRepository(database)
    service = TaskService(repository)
    task = service.create_task(uuid4(), uuid4(), expected_size)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    task = service.transition(task.id, TaskState.QUEUED)
    return repository, task


def test_safe_file_name_removes_directories_and_windows_characters() -> None:
    assert safe_file_name("../../folder/Show:S01E01?.mkv") == "Show_S01E01_.mkv"
    with pytest.raises(ValueError):
        safe_file_name("..")


@pytest.mark.asyncio
async def test_download_moves_verified_file_to_inbox(tmp_path: Path) -> None:
    content = b"abcdefghij"
    repository, task = queued_task(tmp_path, len(content))
    executor = DownloadExecutor(
        repository, tmp_path / "incomplete", tmp_path / "inbox", chunk_size=3
    )

    result = await executor.execute(task.id, BytesSource(content), "show.mkv")

    assert result.state == TaskState.DOWNLOADED
    assert result.path is not None
    assert result.path.read_bytes() == content
    assert repository.get(task.id).downloaded_bytes == len(content)  # type: ignore[union-attr]
    assert not (tmp_path / "incomplete" / f"{task.id}.part").exists()


@pytest.mark.asyncio
async def test_download_resumes_from_existing_partial_file(tmp_path: Path) -> None:
    content = b"resume-this-download"
    repository, task = queued_task(tmp_path, len(content))
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / f"{task.id}.part").write_bytes(content[:7])
    source = BytesSource(content)
    executor = DownloadExecutor(repository, incomplete, tmp_path / "inbox", chunk_size=4)

    result = await executor.execute(task.id, source, "episode.mkv")

    assert source.requested_offsets == [7]
    assert result.path is not None and result.path.read_bytes() == content


@pytest.mark.asyncio
async def test_resume_reconciles_database_progress_to_shorter_durable_part(tmp_path: Path) -> None:
    content = b"abcdef"
    repository, task = queued_task(tmp_path, len(content))
    TaskService(repository).record_progress(task.id, 5)
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / f"{task.id}.part").write_bytes(content[:3])
    source = BytesSource(content)
    executor = DownloadExecutor(repository, incomplete, tmp_path / "inbox", chunk_size=3)

    result = await executor.execute(task.id, source, "episode.mkv")

    assert source.requested_offsets == [3]
    assert result.state == TaskState.DOWNLOADED
    assert result.path is not None and result.path.read_bytes() == content


@pytest.mark.asyncio
async def test_verified_inbox_file_recovers_commit_without_remote_read(tmp_path: Path) -> None:
    content = b"abcdef"
    repository, task = queued_task(tmp_path, len(content))
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    final = inbox / f"{task.id}__episode.mkv"
    final.write_bytes(content)
    source = BytesSource(content)
    executor = DownloadExecutor(repository, tmp_path / "incomplete", inbox)

    result = await executor.execute(task.id, source, "episode.mkv")

    assert source.requested_offsets == []
    assert result.state == TaskState.DOWNLOADED
    assert result.path == final
    assert repository.get(task.id).downloaded_bytes == len(content)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cross_device_inbox_commit_copies_verifies_and_removes_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"cross-device"
    repository, task = queued_task(tmp_path, len(content))
    incomplete = tmp_path / "incomplete"
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    stale_commit = inbox / f".{task.id}__episode.mkv.committing"
    stale_commit.write_bytes(b"stale")
    executor = DownloadExecutor(repository, incomplete, inbox)
    real_replace = os.replace

    def replace(source: str | Path, destination: str | Path) -> None:
        if Path(source).name == f"{task.id}.part":
            raise OSError(errno.EXDEV, "different device")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)

    result = await executor.execute(task.id, BytesSource(content), "episode.mkv")

    assert result.state == TaskState.DOWNLOADED
    assert result.path is not None and result.path.read_bytes() == content
    assert not (incomplete / f"{task.id}.part").exists()
    assert not stale_commit.exists()


@pytest.mark.asyncio
async def test_size_mismatch_fails_task_and_preserves_partial_file(tmp_path: Path) -> None:
    repository, task = queued_task(tmp_path, 10)
    incomplete = tmp_path / "incomplete"
    executor = DownloadExecutor(repository, incomplete, tmp_path / "inbox", chunk_size=4)

    with pytest.raises(DownloadIntegrityError):
        await executor.execute(task.id, BytesSource(b"short"), "bad.mkv")

    stored = repository.get(task.id)
    assert stored is not None and stored.state == TaskState.FAILED
    assert (incomplete / f"{task.id}.part").read_bytes() == b"short"


@pytest.mark.asyncio
async def test_oversized_partial_is_failed_before_remote_read(tmp_path: Path) -> None:
    repository, task = queued_task(tmp_path, 3)
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / f"{task.id}.part").write_bytes(b"too-long")
    source = BytesSource(b"abc")
    executor = DownloadExecutor(repository, incomplete, tmp_path / "inbox")

    with pytest.raises(DownloadIntegrityError):
        await executor.execute(task.id, source, "bad.mkv")

    stored = repository.get(task.id)
    assert stored is not None and stored.state == TaskState.FAILED
    assert source.requested_offsets == []


@pytest.mark.asyncio
async def test_disk_reserve_blocks_download_before_remote_read(tmp_path: Path) -> None:
    repository, task = queued_task(tmp_path, 10)
    source = BytesSource(b"0123456789")
    executor = DownloadExecutor(
        repository,
        tmp_path / "incomplete",
        tmp_path / "inbox",
        min_free_bytes=100,
        free_space=lambda _path: 109,
    )

    with pytest.raises(InsufficientDiskSpace):
        await executor.execute(task.id, source, "blocked.mkv")

    assert source.requested_offsets == []


@pytest.mark.asyncio
async def test_active_pause_preserves_part_and_resume_uses_exact_offset(tmp_path: Path) -> None:
    repository, task = queued_task(tmp_path, 6)
    executor = DownloadExecutor(
        repository, tmp_path / "incomplete", tmp_path / "inbox", chunk_size=3
    )

    paused = await executor.execute(
        task.id, StateChangingSource(repository, task.id, TaskState.PAUSED), "show.mkv"
    )
    part = tmp_path / "incomplete" / f"{task.id}.part"
    assert paused.state == TaskState.PAUSED
    assert part.read_bytes() == b"abc"

    TaskService(repository).transition(task.id, TaskState.QUEUED)
    source = BytesSource(b"abcdef")
    resumed = await executor.execute(task.id, source, "show.mkv")

    assert source.requested_offsets == [3]
    assert resumed.state == TaskState.DOWNLOADED
    assert resumed.path is not None and resumed.path.read_bytes() == b"abcdef"


@pytest.mark.asyncio
async def test_active_cancel_stops_before_writing_the_next_chunk(tmp_path: Path) -> None:
    repository, task = queued_task(tmp_path, 6)
    executor = DownloadExecutor(
        repository, tmp_path / "incomplete", tmp_path / "inbox", chunk_size=3
    )

    cancelled = await executor.execute(
        task.id,
        StateChangingSource(repository, task.id, TaskState.CANCELLED),
        "show.mkv",
    )

    assert cancelled.state == TaskState.CANCELLED
    assert (tmp_path / "incomplete" / f"{task.id}.part").read_bytes() == b"abc"
    assert not (tmp_path / "inbox" / f"{task.id}__show.mkv").exists()
