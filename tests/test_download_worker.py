import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest

from telegram_media_automation.application.archive_service import ArchiveService
from telegram_media_automation.application.download_service import DownloadExecutor
from telegram_media_automation.application.download_worker import DownloadWorker
from telegram_media_automation.application.import_service import ImportService
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.domain.media import ArchivePlan, MediaKind
from telegram_media_automation.domain.resources import MediaResource
from telegram_media_automation.domain.states import TaskState
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.resource_repository import SqlResourceRepository
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository


class Source:
    async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
        yield b"downloaded"[offset : offset + chunk_size]


class Gateway:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def download_source(self, _resource: MediaResource) -> Source:
        if self.error:
            raise self.error
        return Source()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["queued", "downloaded", "recovered"])
async def test_persisted_replacement_plan_runs_without_download_supervision(
    tmp_path: Path, phase: str
) -> None:
    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    target = tmp_path / "tv" / "Legacy Show" / "Season 1" / "Show - S01E22.mkv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old version")
    plan = ArchivePlan(
        MediaKind.SERIES,
        "Show",
        season=1,
        episode=22,
        replace_existing=True,
        target_path=str(target),
    )
    task = service.create_task(
        resource.id,
        resource.id,
        10,
        archive_plan=plan if phase in {"queued", "recovered"} else None,
    )
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    if phase != "queued":
        # Simulate a process stopping after the download was committed, before import.
        claimed = tasks.claim_next("previous-process", 60)
        assert claimed is not None
        inbox = executor.inbox_dir / f"{task.id}__one.mkv"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        inbox.write_bytes(b"downloaded")
        service.transition(task.id, TaskState.DOWNLOADED, output_path=str(inbox))
        if phase == "downloaded":
            service.set_replacement_plan(task.id, plan)
    stored = tasks.get(task.id)
    assert stored is not None and stored.archive_plan == plan
    importer = ImportService(
        tasks,
        resources,
        ArchiveService(executor.inbox_dir, tmp_path / "movies", tmp_path / "tv", mode="copy"),
    )
    result = await DownloadWorker(
        tasks, resources, Gateway(), executor, importer=importer
    ).run_once()
    assert result.state == TaskState.IMPORTED
    assert target.read_bytes() == b"downloaded"
    backups = list((tmp_path / "tv" / ".tma-replaced-backups").glob("*.mkv"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"old version"
    assert not (
        await DownloadWorker(tasks, resources, Gateway(), executor, importer=importer).run_once()
    ).worked


def dependencies(
    tmp_path: Path,
) -> tuple[SqlTaskRepository, SqlResourceRepository, DownloadExecutor]:
    database = Database(f"sqlite:///{tmp_path / 'worker.sqlite3'}")
    database.create_schema_for_development()
    tasks = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    executor = DownloadExecutor(tasks, tmp_path / "incomplete", tmp_path / "inbox")
    return tasks, resources, executor


@pytest.mark.asyncio
async def test_worker_downloads_oldest_queued_resource(tmp_path: Path) -> None:
    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    worker = DownloadWorker(tasks, resources, Gateway(), executor)

    result = await worker.run_once()

    assert result.worked and result.state == TaskState.DOWNLOADED
    stored = tasks.get(task.id)
    assert stored is not None and stored.attempt_count == 1


@pytest.mark.asyncio
async def test_worker_marks_source_error_for_retry(tmp_path: Path) -> None:
    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    worker = DownloadWorker(tasks, resources, Gateway(ConnectionError("offline")), executor)

    result = await worker.run_once()

    assert result.state == TaskState.RETRYING
    stored = tasks.get(task.id)
    assert stored is not None and stored.error_code == "ConnectionError"
    assert stored.next_attempt_at is not None
    assert not (await worker.run_once()).worked


@pytest.mark.asyncio
async def test_worker_redacts_secrets_from_persisted_errors(tmp_path: Path) -> None:
    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    error = ConnectionError("proxy socks5://user:secret@localhost:1080 +8613800000000")

    await DownloadWorker(tasks, resources, Gateway(error), executor).run_once()

    message = tasks.get(task.id).error_message or ""  # type: ignore[union-attr]
    assert "secret" not in message
    assert "13800000000" not in message


@pytest.mark.asyncio
async def test_permanent_resource_error_is_not_retried(tmp_path: Path) -> None:
    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)

    result = await DownloadWorker(
        tasks, resources, Gateway(ValueError("invalid locator")), executor
    ).run_once()

    assert result.state == TaskState.FAILED


@pytest.mark.asyncio
async def test_disk_pressure_remains_retryable_after_normal_attempt_limit(tmp_path: Path) -> None:
    tasks, resources, _ = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    executor = DownloadExecutor(
        tasks,
        tmp_path / "incomplete",
        tmp_path / "inbox",
        min_free_bytes=100,
        free_space=lambda _path: 0,
    )

    result = await DownloadWorker(tasks, resources, Gateway(), executor, max_attempts=1).run_once()

    assert result.state == TaskState.RETRYING


@pytest.mark.asyncio
async def test_stalled_download_times_out_and_keeps_task_retryable(tmp_path: Path) -> None:
    class HangingSource:
        async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
            await asyncio.sleep(60)
            yield b"never"

    class HangingGateway:
        async def download_source(self, _resource: MediaResource) -> HangingSource:
            return HangingSource()

    tasks, resources, _ = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    executor = DownloadExecutor(
        tasks, tmp_path / "incomplete", tmp_path / "inbox", chunk_timeout=0.01
    )

    result = await DownloadWorker(tasks, resources, HangingGateway(), executor).run_once()

    assert result.state == TaskState.RETRYING
    assert tasks.get(task.id).error_code == "TimeoutError"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_worker_honors_provider_flood_wait_seconds(tmp_path: Path) -> None:
    class FloodWaitError(RuntimeError):
        seconds = 120

    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    worker = DownloadWorker(tasks, resources, Gateway(FloodWaitError("wait")), executor)

    result = await worker.run_once()

    assert result.state == TaskState.RETRYING
    assert result.retry_after == 120


@pytest.mark.asyncio
async def test_atomic_lease_prevents_two_workers_downloading_same_task(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowSource:
        async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
            entered.set()
            await release.wait()
            yield b"downloaded"[offset : offset + chunk_size]

    class SlowGateway:
        async def download_source(self, _resource: MediaResource) -> SlowSource:
            return SlowSource()

    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    first = DownloadWorker(tasks, resources, SlowGateway(), executor, worker_id="first")
    second = DownloadWorker(tasks, resources, SlowGateway(), executor, worker_id="second")

    running = asyncio.create_task(first.run_once())
    await entered.wait()
    competing = await second.run_once()
    release.set()
    completed = await running

    assert competing.worked is False
    assert completed.state == TaskState.DOWNLOADED
    assert tasks.get(task.id).attempt_count == 1  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_long_download_renews_lease_before_another_worker_can_claim(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class WaitingSource:
        async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
            entered.set()
            await release.wait()
            yield b"downloaded"

    class WaitingGateway:
        async def download_source(self, _resource: MediaResource) -> WaitingSource:
            return WaitingSource()

    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "one", "one.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    first = DownloadWorker(
        tasks, resources, WaitingGateway(), executor, lease_seconds=0.3, worker_id="first"
    )
    second = DownloadWorker(
        tasks, resources, WaitingGateway(), executor, lease_seconds=0.3, worker_id="second"
    )

    running = asyncio.create_task(first.run_once())
    await entered.wait()
    await asyncio.sleep(0.45)
    competing = await second.run_once()
    release.set()
    completed = await running

    assert competing.worked is False
    assert completed.state == TaskState.DOWNLOADED
    assert tasks.get(task.id).attempt_count == 1  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_worker_stops_remote_read_after_losing_lease_and_replacement_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()

    class BlockedSource:
        async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
            entered.set()
            await asyncio.Event().wait()
            yield b"never-written"

    class BlockedGateway:
        async def download_source(self, _resource: MediaResource) -> BlockedSource:
            return BlockedSource()

    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many(
        [MediaResource("telegram", "lease-loss", "episode.mkv", 10, 90)]
    )[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    original_renew = tasks.renew_lease

    def renew_lease(task_id: UUID, worker_id: str, lease_seconds: float) -> bool:
        if worker_id == "first":
            return False
        return original_renew(task_id, worker_id, lease_seconds)

    monkeypatch.setattr(tasks, "renew_lease", renew_lease)
    first = DownloadWorker(
        tasks,
        resources,
        BlockedGateway(),
        executor,
        lease_seconds=0.15,
        worker_id="first",
    )
    running = asyncio.create_task(first.run_once())
    await entered.wait()

    abandoned = await asyncio.wait_for(running, timeout=1)
    assert abandoned.state == TaskState.DOWNLOADING
    await asyncio.sleep(0.06)

    replacement = DownloadWorker(
        tasks,
        resources,
        Gateway(),
        executor,
        lease_seconds=1,
        worker_id="second",
    )
    completed = await replacement.run_once()

    assert completed.state == TaskState.DOWNLOADED
    final = tmp_path / "inbox" / f"{task.id}__episode.mkv"
    assert final.read_bytes() == b"downloaded"
    assert tasks.get(task.id).attempt_count == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_worker_automatically_archives_a_subscription_download(tmp_path: Path) -> None:
    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many(
        [MediaResource("telegram", "episode", "A.Show.S01E02.mkv", 10, 90)]
    )[0]
    service = TaskService(tasks)
    task = service.create_task(
        resource.id,
        resource.id,
        10,
        archive_plan=ArchivePlan(
            kind=MediaKind.SERIES,
            title="A Show",
            season=1,
            episode=2,
        ),
    )
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    importer = ImportService(
        tasks,
        resources,
        ArchiveService(tmp_path / "inbox", tmp_path / "movies", tmp_path / "tv"),
    )
    worker = DownloadWorker(tasks, resources, Gateway(), executor, importer=importer)

    result = await worker.run_once()

    stored = tasks.get(task.id)
    assert result.state == TaskState.IMPORTED
    assert stored is not None and stored.state == TaskState.IMPORTED
    assert Path(stored.output_path or "").read_bytes() == b"downloaded"
    assert "Season 01" in (stored.output_path or "")


@pytest.mark.asyncio
async def test_retry_after_import_failure_reuses_verified_inbox_file(tmp_path: Path) -> None:
    class MustNotDownload:
        async def download_source(self, _resource: MediaResource) -> Source:
            raise AssertionError("Telegram must not be called for an import retry")

    tasks, resources, executor = dependencies(tmp_path)
    resource = resources.upsert_many([MediaResource("telegram", "movie", "Movie.mkv", 10, 90)])[0]
    service = TaskService(tasks)
    task = service.create_task(resource.id, resource.id, 10)
    service.transition(task.id, TaskState.CANDIDATE_SELECTED)
    service.transition(task.id, TaskState.QUEUED)
    downloaded = await DownloadWorker(tasks, resources, Gateway(), executor).run_once()
    assert downloaded.state == TaskState.DOWNLOADED
    service.transition(task.id, TaskState.IMPORTING)
    service.transition(task.id, TaskState.FAILED, reason="library is temporarily unavailable")
    service.transition(task.id, TaskState.RETRYING)

    recovered = await DownloadWorker(tasks, resources, MustNotDownload(), executor).run_once()

    assert recovered.state == TaskState.DOWNLOADED
    assert tasks.get(task.id).state == TaskState.DOWNLOADED  # type: ignore[union-attr]
