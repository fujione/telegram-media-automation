"""Typed configuration and secret-file resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from telegram_media_automation.domain.resources import QualityPolicy


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    environment: str = "development"
    bootstrap_enabled: bool = False
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    timezone: str = "Asia/Shanghai"
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///data/media_automation.sqlite3"
    backup_dir: Path = Path("data/backups")
    api_token: str | None = Field(default=None, repr=False)
    api_token_file: Path | None = None
    admin_credentials_file: Path = Path("secrets/admin_credentials.json")
    web_session_lifetime_seconds: int = Field(default=43200, ge=300, le=2592000)
    download_incomplete: Path = Path("downloads/incomplete")
    download_inbox: Path = Path("downloads/inbox")
    movie_root: Path = Path("media/movies")
    tv_root: Path = Path("media/tv")
    archive_mode: Literal["move", "hardlink", "copy"] = "move"
    telegram_api_id: int | None = Field(default=None, repr=False)
    telegram_api_hash: str | None = Field(default=None, repr=False)
    telegram_api_id_file: Path | None = None
    telegram_api_hash_file: Path | None = None
    telegram_session: Path = Path("data/sessions/telegram.session")
    telegram_proxy_url: str | None = Field(default=None, repr=False)
    telegram_proxy_url_file: Path | None = None
    telegram_search_channels: tuple[str, ...] = ()
    telegram_connect_timeout: int = Field(default=8, ge=1, le=120)
    telegram_connection_retries: int = Field(default=1, ge=0, le=20)
    telegram_operation_timeout: float = Field(default=20.0, gt=0, le=600)
    worker_poll_interval: float = Field(default=2.0, gt=0, le=300)
    worker_max_attempts: int = Field(default=5, ge=1, le=100)
    worker_retry_delay: float = Field(default=30.0, gt=0, le=86400)
    worker_lease_seconds: float = Field(default=60.0, ge=3, le=3600)
    subscription_poll_interval: float = Field(default=300.0, ge=30, le=86400)
    workers_enabled: bool = True
    minimum_free_bytes: int = Field(default=1024 * 1024 * 1024, ge=0)
    download_bytes_per_second: int | None = Field(default=None, gt=0)
    download_chunk_timeout: float = Field(default=75.0, gt=0, le=3600)
    quality_preferred_resolutions: tuple[int, ...] = (2160, 1080, 720)
    quality_allowed_resolutions: tuple[int, ...] = ()
    quality_preferred_languages: tuple[str, ...] = ("zh", "en")
    quality_allowed_languages: tuple[str, ...] = ()
    quality_prefer_hdr: bool = False
    quality_max_file_size: int | None = Field(default=None, gt=0)
    cleanup_enabled: bool = True
    cleanup_interval_seconds: float = Field(default=3600.0, ge=60, le=86400)
    cleanup_retention_seconds: float = Field(default=86400.0, ge=3600, le=31536000)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown IANA timezone: {normalized}") from error
        return normalized

    def resolved_api_token(self) -> str | None:
        if self.api_token:
            return self.api_token
        if self.api_token_file and self.api_token_file.is_file():
            value = self.api_token_file.read_text(encoding="utf-8").strip()
            return value or None
        return None

    def quality_policy(self) -> QualityPolicy:
        return QualityPolicy(
            preferred_resolutions=self.quality_preferred_resolutions,
            allowed_resolutions=self.quality_allowed_resolutions,
            preferred_languages=self.quality_preferred_languages,
            allowed_languages=self.quality_allowed_languages,
            max_file_size=self.quality_max_file_size,
            prefer_hdr=self.quality_prefer_hdr,
        )

    @staticmethod
    def _read_secret(path: Path | None) -> str | None:
        if path and path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            return value or None
        return None

    def resolved_telegram_credentials(self) -> tuple[int, str] | None:
        raw_id = (
            str(self.telegram_api_id)
            if self.telegram_api_id is not None
            else self._read_secret(self.telegram_api_id_file)
        )
        api_hash = self.telegram_api_hash or self._read_secret(self.telegram_api_hash_file)
        if raw_id is None and api_hash is None:
            return None
        if raw_id is None or api_hash is None:
            raise ValueError("both Telegram API ID and API hash must be configured")
        try:
            api_id = int(raw_id)
        except ValueError as error:
            raise ValueError("Telegram API ID must be an integer") from error
        if api_id <= 0:
            raise ValueError("Telegram API ID must be positive")
        return api_id, api_hash

    def resolved_telegram_proxy(self) -> tuple[str, str, int, bool, str | None, str | None] | None:
        value = self.telegram_proxy_url or self._read_secret(self.telegram_proxy_url_file)
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"socks5", "socks4", "http"}:
            raise ValueError("Telegram proxy scheme must be socks5, socks4 or http")
        if not parsed.hostname or parsed.port is None:
            raise ValueError("Telegram proxy URL must include a host and port")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Telegram proxy URL cannot contain a path, query or fragment")
        return (
            parsed.scheme.casefold(),
            parsed.hostname,
            parsed.port,
            True,
            unquote(parsed.username) if parsed.username else None,
            unquote(parsed.password) if parsed.password else None,
        )
