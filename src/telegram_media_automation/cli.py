"""mediactl command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import socket
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import cast

import uvicorn

from telegram_media_automation.application.archive_service import ArchiveMode, ArchiveService
from telegram_media_automation.application.download_service import DownloadExecutor
from telegram_media_automation.application.download_worker import DownloadWorker
from telegram_media_automation.application.import_service import ImportService
from telegram_media_automation.application.maintenance_service import FileMaintenanceService
from telegram_media_automation.application.subscription_worker import SubscriptionWorker
from telegram_media_automation.application.task_service import TaskService
from telegram_media_automation.infrastructure.backup import (
    create_backup,
    restore_backup,
    sqlite_database_path,
)
from telegram_media_automation.infrastructure.bootstrap import BootstrapStore
from telegram_media_automation.infrastructure.config import Settings
from telegram_media_automation.infrastructure.credential_store import rotate_api_token
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.doctor import run_doctor
from telegram_media_automation.infrastructure.initializer import initialize
from telegram_media_automation.infrastructure.logging import configure_logging
from telegram_media_automation.infrastructure.resource_repository import SqlResourceRepository
from telegram_media_automation.infrastructure.subscription_repository import (
    SqlSubscriptionRepository,
)
from telegram_media_automation.infrastructure.task_repository import SqlTaskRepository
from telegram_media_automation.infrastructure.telegram_gateway import TelethonGateway
from telegram_media_automation.infrastructure.web_auth import write_admin_credentials


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="mediactl")
    subcommands = command.add_subparsers(dest="command", required=True)
    subcommands.add_parser("setup-code", help="show the one-time Docker first-run code locally")
    initialize_command = subcommands.add_parser(
        "init", help="create local directories, secrets and configuration"
    )
    initialize_command.add_argument("--root", type=Path, default=Path.cwd())
    initialize_command.add_argument(
        "--docker", action="store_true", help="write paths for the bundled Compose deployment"
    )
    initialize_command.add_argument("--language", choices=("zh-CN", "en-US"))
    initialize_command.add_argument("--timezone", help="IANA timezone, for example Asia/Shanghai")
    initialize_command.add_argument("--data-dir", type=Path)
    initialize_command.add_argument("--backup-dir", type=Path)
    initialize_command.add_argument("--incomplete-dir", type=Path)
    initialize_command.add_argument("--inbox-dir", type=Path)
    initialize_command.add_argument("--movie-dir", type=Path)
    initialize_command.add_argument("--tv-dir", type=Path)
    initialize_command.add_argument(
        "--archive-mode",
        choices=("move", "hardlink", "copy"),
        help="how completed files enter the media library",
    )
    serve = subcommands.add_parser("serve", help="start the API and embedded workers")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    worker = subcommands.add_parser("worker", help="consume the persisted download queue")
    worker.add_argument("--once", action="store_true", help="process at most one task")
    backup = subcommands.add_parser("backup", help="create a consistent SQLite backup archive")
    backup.add_argument("archive", type=Path)
    backup.add_argument("--include-session", action="store_true")
    restore = subcommands.add_parser("restore", help="restore into an empty product root")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--root", type=Path, default=Path.cwd())
    doctor = subcommands.add_parser("doctor", help="check configuration and local storage")
    doctor.add_argument("--json", action="store_true", help="print a machine-readable report")
    rotate_token = subcommands.add_parser(
        "rotate-token", help="replace the file-backed administrator API token"
    )
    rotate_token.add_argument(
        "--show", action="store_true", help="print the new token once after rotation"
    )
    set_admin = subcommands.add_parser(
        "set-admin", help="create or replace the local Web administrator account"
    )
    set_admin.add_argument("--username", help="administrator username; prompted when omitted")
    set_admin.add_argument(
        "--credentials-file",
        type=Path,
        help="credential file to update; useful for a Compose host secret directory",
    )
    return command


def prompt_admin_credentials(username: str | None = None) -> tuple[str, str]:
    selected_username = (username or input("Administrator username [admin]: ")).strip() or "admin"
    password = getpass.getpass("Administrator password (at least 10 characters): ")
    confirmation = getpass.getpass("Confirm administrator password: ")
    if password != confirmation:
        raise SystemExit("两次输入的管理员密码不一致")
    return selected_username, password


def prompt_value(label: str, default: str) -> str:
    return input(f"{label} [{default}]: ").strip() or default


def prompt_directory(label: str, value: Path | None, default: str) -> Path:
    return value if value is not None else Path(prompt_value(label, default))


def ensure_port_available(host: str, port: int) -> None:
    if not 1 <= port <= 65535:
        raise SystemExit("监听端口必须在 1 到 65535 之间")
    try:
        addresses = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
    except OSError as error:
        raise SystemExit(f"无法解析监听地址 {host}: {error}") from error
    errors: list[OSError] = []
    for family, socket_type, protocol, _, address in addresses:
        probe = socket.socket(family, socket_type, protocol)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(address)
            return
        except OSError as error:
            errors.append(error)
        finally:
            probe.close()
    detail = errors[-1] if errors else "unknown socket error"
    raise SystemExit(
        f"无法监听 {host}:{port}, 端口可能已被占用 ({detail}). "
        f"可尝试: uv run mediactl serve --port {port + 1}"
    )


async def run_worker(once: bool) -> None:
    settings = Settings()
    configure_logging(json_output=settings.environment == "production")
    credentials = settings.resolved_telegram_credentials()
    if credentials is None:
        raise SystemExit("Telegram API ID and API hash are not configured")
    database = Database(settings.database_url)
    database.migrate()
    tasks = SqlTaskRepository(database)
    resources = SqlResourceRepository(database)
    subscriptions = SqlSubscriptionRepository(database)
    maintenance = FileMaintenanceService(
        tasks,
        settings.download_incomplete,
        settings.download_inbox,
        settings.movie_root,
        settings.tv_root,
        retention_seconds=settings.cleanup_retention_seconds,
    )
    gateway = TelethonGateway(
        settings.telegram_session,
        credentials[0],
        credentials[1],
        proxy=settings.resolved_telegram_proxy(),
        connect_timeout=settings.telegram_connect_timeout,
        connection_retries=settings.telegram_connection_retries,
    )
    executor = DownloadExecutor(
        tasks,
        settings.download_incomplete,
        settings.download_inbox,
        min_free_bytes=settings.minimum_free_bytes,
        bytes_per_second=settings.download_bytes_per_second,
        chunk_timeout=settings.download_chunk_timeout,
    )
    importer = ImportService(
        tasks,
        resources,
        ArchiveService(
            settings.download_inbox,
            settings.movie_root,
            settings.tv_root,
            mode=settings.archive_mode,
        ),
    )
    worker = DownloadWorker(
        tasks,
        resources,
        gateway,
        executor,
        max_attempts=settings.worker_max_attempts,
        retry_delay=settings.worker_retry_delay,
        lease_seconds=settings.worker_lease_seconds,
        importer=importer,
    )
    subscription_worker = SubscriptionWorker(
        subscriptions,
        resources,
        TaskService(tasks),
        gateway,
        settings.quality_policy(),
    )

    async def run_maintenance() -> None:
        try:
            report = await asyncio.to_thread(maintenance.run_once)
            if report.removed:
                logging.getLogger(__name__).info(
                    "stale managed files cleaned",
                    extra={
                        "removed_files": report.removed,
                        "reclaimed_bytes": report.reclaimed_bytes,
                    },
                )
        except Exception:
            logging.getLogger(__name__).exception("file maintenance iteration failed")

    try:
        if once:
            if settings.cleanup_enabled:
                await run_maintenance()
            try:
                await subscription_worker.run_once()
            except Exception:
                logging.getLogger(__name__).exception("subscription maintenance iteration failed")
            await worker.run_once()
        else:
            next_maintenance = 0.0
            next_subscription_scan = 0.0
            while True:
                worked = False
                idle_delay = settings.worker_poll_interval
                if settings.cleanup_enabled and time.monotonic() >= next_maintenance:
                    await run_maintenance()
                    next_maintenance = time.monotonic() + settings.cleanup_interval_seconds
                if time.monotonic() >= next_subscription_scan:
                    retry_after = 0.0
                    try:
                        worked = bool(await subscription_worker.run_once())
                    except Exception as error:
                        retry_after = float(getattr(error, "seconds", 0) or 0)
                        logging.getLogger(__name__).exception(
                            "subscription worker iteration failed"
                        )
                    next_subscription_scan = time.monotonic() + max(
                        settings.subscription_poll_interval, retry_after
                    )
                result = await worker.run_once()
                if not worked and not result.worked:
                    await asyncio.sleep(idle_delay)
    finally:
        await gateway.disconnect()


def _configure_windows_console_output() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def main() -> None:
    _configure_windows_console_output()
    arguments = parser().parse_args()
    if arguments.command == "setup-code":
        settings = Settings()
        if not settings.bootstrap_enabled:
            raise SystemExit("Web first-run mode is not enabled")
        try:
            print(BootstrapStore(settings).setup_code())
        except (OSError, ValueError) as error:
            raise SystemExit("Setup code is unavailable or setup is already complete") from error
    elif arguments.command == "init":
        admin_username, admin_password = prompt_admin_credentials()
        language = arguments.language or prompt_value("Language", "zh-CN")
        timezone = arguments.timezone or prompt_value("Timezone", "Asia/Shanghai")
        data_dir = prompt_directory("Data directory", arguments.data_dir, "data")
        backup_dir = prompt_directory(
            "Backup directory", arguments.backup_dir, str(data_dir / "backups")
        )
        incomplete_dir = prompt_directory(
            "Incomplete download directory",
            arguments.incomplete_dir,
            "downloads/incomplete",
        )
        inbox_dir = prompt_directory(
            "Completed download inbox", arguments.inbox_dir, "downloads/inbox"
        )
        movie_dir = prompt_directory("Movie library directory", arguments.movie_dir, "media/movies")
        tv_dir = prompt_directory("TV library directory", arguments.tv_dir, "media/tv")
        archive_mode = cast(
            ArchiveMode,
            arguments.archive_mode or prompt_value("Archive mode (move/hardlink/copy)", "move"),
        )
        api_id = input("Telegram API ID: ").strip()
        api_hash = getpass.getpass("Telegram API Hash: ").strip()
        proxy_url = getpass.getpass(
            "Telegram proxy URL (optional, socks5://user:pass@host:port): "
        ).strip()
        initialization_result = initialize(
            arguments.root,
            api_id,
            api_hash,
            admin_username=admin_username,
            admin_password=admin_password,
            language=language,
            timezone=timezone,
            data_dir=data_dir,
            backup_dir=backup_dir,
            download_incomplete=incomplete_dir,
            download_inbox=inbox_dir,
            movie_root=movie_dir,
            tv_root=tv_dir,
            archive_mode=archive_mode,
            proxy_url=proxy_url or None,
            deployment="docker" if arguments.docker else "local",
        )
        print(f"Initialized {initialization_result.root}")
        print(f"Administrator created: {admin_username}")
    elif arguments.command == "serve":
        ensure_port_available(arguments.host, arguments.port)
        uvicorn.run(
            "telegram_media_automation.api.app:create_app",
            factory=True,
            host=arguments.host,
            port=arguments.port,
            access_log=False,
        )
    elif arguments.command == "worker":
        asyncio.run(run_worker(arguments.once))
    elif arguments.command == "backup":
        settings = Settings()
        backup_result = create_backup(
            sqlite_database_path(settings.database_url),
            arguments.archive,
            telegram_session=(settings.telegram_session if arguments.include_session else None),
        )
        print(f"Backup created: {backup_result.archive}")
        print(f"Telegram session included: {backup_result.included_session}")
    elif arguments.command == "restore":
        database, session = restore_backup(arguments.archive, arguments.root)
        print(f"Database restored: {database}")
        if session is not None:
            print(f"Telegram session restored: {session}")
    elif arguments.command == "doctor":
        report = run_doctor(Settings())
        if arguments.json:
            print(
                json.dumps(
                    {
                        "healthy": report.healthy,
                        "checks": [asdict(check) for check in report.checks],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            symbols = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
            for check in report.checks:
                print(f"[{symbols[check.status]}] {check.name}: {check.message}")
        if not report.healthy:
            raise SystemExit(1)
    elif arguments.command == "rotate-token":
        token, path = rotate_api_token(Settings())
        print(f"API Token 已轮换: {path}")
        if arguments.show:
            print(f"New API Token: {token}")
        else:
            print("新 Token 未显示; 可在本机 secret 文件中读取。重启服务后生效。")
    elif arguments.command == "set-admin":
        admin_username, admin_password = prompt_admin_credentials(arguments.username)
        settings = Settings()
        path = write_admin_credentials(
            arguments.credentials_file or settings.admin_credentials_file,
            admin_username,
            admin_password,
            overwrite=True,
        )
        print(f"Web 管理员已设置: {admin_username} ({path})")
