"""One-time local initialization without placing secrets in configuration."""

from __future__ import annotations

import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.web_auth import write_admin_credentials


@dataclass(frozen=True, slots=True)
class InitializationResult:
    root: Path
    env_file: Path
    api_token: str


@dataclass(frozen=True, slots=True)
class InitializationPaths:
    data: Path
    backups: Path
    incomplete: Path
    inbox: Path
    movies: Path
    tv: Path


def _resolve_directory(root: Path, value: Path | None, default: str) -> Path:
    candidate = value if value is not None else Path(default)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def _prepare_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"configured directory is not a directory: {path}")
    try:
        with tempfile.NamedTemporaryFile(prefix=".mediactl-init-", dir=path):
            pass
    except OSError as error:
        raise ValueError(f"configured directory is not writable: {path}") from error


def _env_value(value: str | Path) -> str:
    text = value.as_posix() if isinstance(value, Path) else value
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def initialize(
    root: Path,
    telegram_api_id: str,
    telegram_api_hash: str,
    *,
    admin_username: str,
    admin_password: str,
    api_token: str | None = None,
    proxy_url: str | None = None,
    deployment: Literal["local", "docker"] = "local",
    language: str = "zh-CN",
    timezone: str = "Asia/Shanghai",
    data_dir: Path | None = None,
    backup_dir: Path | None = None,
    download_incomplete: Path | None = None,
    download_inbox: Path | None = None,
    movie_root: Path | None = None,
    tv_root: Path | None = None,
    archive_mode: Literal["move", "hardlink", "copy"] = "move",
) -> InitializationResult:
    root = root.resolve()
    env_file = root / ".env"
    if env_file.exists():
        raise FileExistsError(f"configuration already exists: {env_file}")
    try:
        api_id = int(telegram_api_id.strip())
    except ValueError as error:
        raise ValueError("Telegram API ID must be an integer") from error
    if api_id <= 0 or not telegram_api_hash.strip():
        raise ValueError("valid Telegram API ID and API hash are required")
    if deployment not in {"local", "docker"}:
        raise ValueError("deployment must be local or docker")
    if archive_mode not in {"move", "hardlink", "copy"}:
        raise ValueError("archive mode must be move, hardlink or copy")
    normalized_language = language.strip()
    if normalized_language not in {"zh-CN", "en-US"}:
        raise ValueError("language must be zh-CN or en-US")
    normalized_timezone = timezone.strip()
    try:
        ZoneInfo(normalized_timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown IANA timezone: {normalized_timezone}") from error

    data_path = _resolve_directory(root, data_dir, "data")
    paths = InitializationPaths(
        data=data_path,
        backups=_resolve_directory(root, backup_dir, str(data_path / "backups")),
        incomplete=_resolve_directory(root, download_incomplete, "downloads/incomplete"),
        inbox=_resolve_directory(root, download_inbox, "downloads/inbox"),
        movies=_resolve_directory(root, movie_root, "media/movies"),
        tv=_resolve_directory(root, tv_root, "media/tv"),
    )
    operational_paths = (paths.incomplete, paths.inbox, paths.movies, paths.tv)
    if len(set(operational_paths)) != len(operational_paths):
        raise ValueError("incomplete, inbox, movie and TV directories must be different")

    for directory in (
        paths.data,
        paths.data / "sessions",
        paths.backups,
        paths.incomplete,
        paths.inbox,
        paths.movies,
        paths.tv,
        root / "secrets",
    ):
        _prepare_directory(directory)

    generated_token = api_token or secrets.token_urlsafe(32)
    secret_values = {
        root / "secrets/api_token": generated_token,
        root / "secrets/telegram_api_id": str(api_id),
        root / "secrets/telegram_api_hash": telegram_api_hash.strip(),
    }
    if proxy_url:
        secret_values[root / "secrets/telegram_proxy_url"] = proxy_url.strip()
    written: list[Path] = []
    try:
        for path, value in secret_values.items():
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{value}\n")
            path.chmod(0o600)
            written.append(path)
        admin_credentials = root / "secrets/admin_credentials.json"
        write_admin_credentials(admin_credentials, admin_username, admin_password)
        written.append(admin_credentials)
        if deployment == "docker":
            runtime_paths = {
                "data": "/data",
                "database": "sqlite:////data/media_automation.sqlite3",
                "backups": "/backups",
                "incomplete": "/downloads/incomplete",
                "inbox": "/downloads/inbox",
                "movies": "/media/movies",
                "tv": "/media/tv",
                "secrets": "/run/secrets",
                "session": "/data/sessions/telegram.session",
            }
        else:
            runtime_paths = {
                "data": paths.data.as_posix(),
                "database": f"sqlite:///{(paths.data / 'media_automation.sqlite3').as_posix()}",
                "backups": paths.backups.as_posix(),
                "incomplete": paths.incomplete.as_posix(),
                "inbox": paths.inbox.as_posix(),
                "movies": paths.movies.as_posix(),
                "tv": paths.tv.as_posix(),
                "secrets": (root / "secrets").as_posix(),
                "session": (paths.data / "sessions/telegram.session").as_posix(),
            }
        secret_base = runtime_paths["secrets"]
        lines = [
            "APP_ENVIRONMENT=production",
            f"APP_LANGUAGE={_env_value(normalized_language)}",
            f"APP_TIMEZONE={_env_value(normalized_timezone)}",
            f"APP_ARCHIVE_MODE={archive_mode}",
            f"APP_DATA_DIR={_env_value(runtime_paths['data'])}",
            f"APP_DATABASE_URL={_env_value(runtime_paths['database'])}",
            f"APP_BACKUP_DIR={_env_value(runtime_paths['backups'])}",
            f"APP_DOWNLOAD_INCOMPLETE={_env_value(runtime_paths['incomplete'])}",
            f"APP_DOWNLOAD_INBOX={_env_value(runtime_paths['inbox'])}",
            f"APP_MOVIE_ROOT={_env_value(runtime_paths['movies'])}",
            f"APP_TV_ROOT={_env_value(runtime_paths['tv'])}",
            f"APP_API_TOKEN_FILE={_env_value(f'{secret_base}/api_token')}",
            f"APP_ADMIN_CREDENTIALS_FILE={_env_value(f'{secret_base}/admin_credentials.json')}",
            f"APP_TELEGRAM_API_ID_FILE={_env_value(f'{secret_base}/telegram_api_id')}",
            f"APP_TELEGRAM_API_HASH_FILE={_env_value(f'{secret_base}/telegram_api_hash')}",
            f"APP_TELEGRAM_SESSION={_env_value(runtime_paths['session'])}",
            "APP_TELEGRAM_CONNECT_TIMEOUT=8",
            "APP_TELEGRAM_CONNECTION_RETRIES=1",
            "APP_TELEGRAM_OPERATION_TIMEOUT=20",
            "APP_SUBSCRIPTION_POLL_INTERVAL=300",
            "APP_QUALITY_PREFERRED_RESOLUTIONS=" + _env_value("[2160,1080,720]"),
            "APP_QUALITY_ALLOWED_RESOLUTIONS=" + _env_value("[]"),
            "APP_QUALITY_PREFERRED_LANGUAGES=" + _env_value('["zh","en"]'),
            "APP_QUALITY_ALLOWED_LANGUAGES=" + _env_value("[]"),
            "APP_QUALITY_PREFER_HDR=false",
            "APP_WEB_SESSION_LIFETIME_SECONDS=43200",
            "APP_WORKERS_ENABLED=true",
            "APP_CLEANUP_ENABLED=true",
            "APP_CLEANUP_INTERVAL_SECONDS=3600",
            "APP_CLEANUP_RETENTION_SECONDS=86400",
        ]
        if deployment == "docker":
            container_uid = os.getuid() if hasattr(os, "getuid") else 10001
            container_gid = os.getgid() if hasattr(os, "getgid") else 10001
            lines[4:4] = [
                f"TMA_HOST_DATA_DIR={_env_value(paths.data)}",
                f"TMA_HOST_BACKUP_DIR={_env_value(paths.backups)}",
                f"TMA_HOST_INCOMPLETE_DIR={_env_value(paths.incomplete)}",
                f"TMA_HOST_INBOX_DIR={_env_value(paths.inbox)}",
                f"TMA_HOST_MOVIE_DIR={_env_value(paths.movies)}",
                f"TMA_HOST_TV_DIR={_env_value(paths.tv)}",
                "TMA_BIND_ADDRESS=127.0.0.1",
                "TMA_HOST_PORT=8080",
                f"TMA_UID={container_uid}",
                f"TMA_GID={container_gid}",
            ]
        if proxy_url:
            lines.append(
                f"APP_TELEGRAM_PROXY_URL_FILE={_env_value(f'{secret_base}/telegram_proxy_url')}"
            )
        with env_file.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        env_file.chmod(0o600)
        written.append(env_file)
        database_path = (paths.data / "media_automation.sqlite3").as_posix()
        Database(f"sqlite:///{database_path}").migrate()
    except Exception:
        for path in reversed(written):
            path.unlink(missing_ok=True)
        raise
    return InitializationResult(root, env_file, generated_token)
