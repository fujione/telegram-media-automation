"""Conservative cleanup for files exclusively owned by product workflows."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from telegram_media_automation.application.ports import TaskRepository
from telegram_media_automation.domain.states import TaskState

_ARCHIVE_TEMP = re.compile(r"^[.].+[.]importing-[0-9a-f]{32}$", re.IGNORECASE)
_DOWNLOAD_COMMIT_TEMP = re.compile(r"^[.].+[.]committing$")
_PART_FILE = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[.]part$", re.IGNORECASE
)
_PART_DISCARD_STATES = {
    TaskState.CANCELLED,
    TaskState.DOWNLOADED,
    TaskState.IMPORTED,
    TaskState.PROVISIONAL,
}


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    scanned: int
    removed: int
    reclaimed_bytes: int


class FileMaintenanceService:
    """Remove only stale artifacts whose names prove ownership by this product."""

    def __init__(
        self,
        tasks: TaskRepository,
        incomplete_dir: Path,
        inbox_dir: Path,
        movie_root: Path,
        tv_root: Path,
        *,
        retention_seconds: float = 86400,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if retention_seconds < 3600:
            raise ValueError("cleanup retention must be at least one hour")
        self.tasks = tasks
        self.incomplete_dir = incomplete_dir
        self.inbox_dir = inbox_dir
        self.movie_root = movie_root
        self.tv_root = tv_root
        self.retention_seconds = retention_seconds
        self.clock = clock

    def _is_stale(self, path: Path) -> bool:
        return path.stat().st_mtime <= self.clock() - self.retention_seconds

    @staticmethod
    def _remove(path: Path) -> int:
        size = path.stat().st_size
        path.unlink()
        return size

    def _clean_parts(self) -> tuple[int, int, int]:
        scanned = removed = reclaimed = 0
        if not self.incomplete_dir.is_dir():
            return scanned, removed, reclaimed
        for path in self.incomplete_dir.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            match = _PART_FILE.fullmatch(path.name)
            if match is None:
                continue
            scanned += 1
            if not self._is_stale(path):
                continue
            task = self.tasks.get(UUID(match.group(1)))
            if task is not None and task.state not in _PART_DISCARD_STATES:
                continue
            reclaimed += self._remove(path)
            removed += 1
        return scanned, removed, reclaimed

    def _clean_temporary_files(
        self, root: Path, pattern: re.Pattern[str], *, max_depth: int
    ) -> tuple[int, int, int]:
        scanned = removed = reclaimed = 0
        if not root.is_dir():
            return scanned, removed, reclaimed
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            for path in directory.iterdir():
                if path.is_symlink():
                    continue
                if path.is_dir():
                    if depth < max_depth:
                        pending.append((path, depth + 1))
                    continue
                if not path.is_file() or pattern.fullmatch(path.name) is None:
                    continue
                scanned += 1
                if self._is_stale(path):
                    reclaimed += self._remove(path)
                    removed += 1
        return scanned, removed, reclaimed

    def run_once(self) -> MaintenanceReport:
        totals = [0, 0, 0]
        operations = (
            self._clean_parts(),
            self._clean_temporary_files(self.inbox_dir, _DOWNLOAD_COMMIT_TEMP, max_depth=0),
            self._clean_temporary_files(self.movie_root, _ARCHIVE_TEMP, max_depth=1),
            self._clean_temporary_files(self.tv_root, _ARCHIVE_TEMP, max_depth=2),
        )
        for values in operations:
            for index, value in enumerate(values):
                totals[index] += value
        return MaintenanceReport(*totals)
