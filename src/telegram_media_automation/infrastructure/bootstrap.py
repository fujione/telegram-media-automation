"""Opt-in Docker first-run configuration, published as one atomic directory."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import Literal
from uuid import uuid4

from telegram_media_automation.infrastructure.config import Settings
from telegram_media_automation.infrastructure.web_auth import (
    validate_admin_credentials,
    write_admin_credentials,
)


def _private_write(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(value + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _persistent_secret(path: Path) -> None:
    if path.exists():
        if len(path.read_text(encoding="utf-8").strip()) < 32:
            raise ValueError("First-run secret file is invalid; restore it before restarting")
        return
    temporary = path.with_name("." + path.name + "." + uuid4().hex)
    try:
        _private_write(temporary, secrets.token_urlsafe(32))
        with suppress(FileExistsError):
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class InvalidSetupCode(PermissionError):
    pass


class BootstrapStore:
    def __init__(self, settings: Settings) -> None:
        self.root = settings.data_dir.resolve() / "configuration"
        self.install = self.root / "install"
        self.code_file = self.root / "setup_code"
        self.lock = threading.Lock()

    def configured_settings(self, settings: Settings) -> Settings:
        paths = {
            "api_token_file": self.root / "api_token",
            "admin_credentials_file": self.install / "admin_credentials.json",
            "telegram_api_id_file": self.install / "telegram_api_id",
            "telegram_api_hash_file": self.install / "telegram_api_hash",
            "telegram_proxy_url_file": self.install / "telegram_proxy_url",
        }
        for key, expected in paths.items():
            value = getattr(settings, key)
            if (
                key in settings.model_fields_set
                and value is not None
                and value.resolve() != expected
            ):
                raise ValueError("Web first-run mode requires its own configuration paths")
        if any(
            [
                settings.api_token,
                settings.telegram_api_id,
                settings.telegram_api_hash,
                settings.telegram_proxy_url,
            ]
        ):
            raise ValueError("Existing credentials require the existing deployment mode")
        preferences = self.install / "preferences.json"
        values = (
            json.loads(preferences.read_text(encoding="utf-8")) if preferences.is_file() else {}
        )
        validated = Settings(  # type: ignore[call-arg]
            _env_file=None,
            language=values.get("language", settings.language),
            timezone=values.get("timezone", settings.timezone),
        )
        return settings.model_copy(
            update={**paths, "language": validated.language, "timezone": validated.timezone}
        )

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _persistent_secret(self.root / "api_token")
        if not (self.install / "admin_credentials.json").exists():
            _persistent_secret(self.code_file)

    def setup_code(self) -> str:
        if (self.install / "admin_credentials.json").exists():
            raise FileExistsError("First-run setup is already complete")
        return self.code_file.read_text(encoding="utf-8").strip()

    def complete(
        self,
        *,
        code: str,
        username: str,
        password: str,
        telegram_api_id: int,
        telegram_api_hash: str,
        proxy_url: str | None = None,
        language: Literal["zh-CN", "en-US"] = "zh-CN",
        timezone: str = "Asia/Shanghai",
    ) -> None:
        with self.lock:
            if self.install.exists():
                raise FileExistsError("First-run setup is already complete")
            if not code or not secrets.compare_digest(code.encode(), self.setup_code().encode()):
                raise InvalidSetupCode("Invalid first-run setup code")
            validate_admin_credentials(username, password)
            validated = Settings(  # type: ignore[call-arg]
                _env_file=None,
                language=language,
                timezone=timezone,
                telegram_api_id=telegram_api_id,
                telegram_api_hash=telegram_api_hash,
                telegram_proxy_url=proxy_url,
            )
            validated.resolved_telegram_credentials()
            validated.resolved_telegram_proxy()
            if proxy_url and any(character in proxy_url for character in "\r\n"):
                raise ValueError("Invalid proxy URL")
            if (
                not telegram_api_hash.strip()
                or len(telegram_api_hash) > 256
                or "\n" in telegram_api_hash
                or "\r" in telegram_api_hash
            ):
                raise ValueError("Invalid Telegram API hash")
            stage = Path(tempfile.mkdtemp(prefix=".install-", dir=self.root))
            try:
                _private_write(stage / "telegram_api_id", str(telegram_api_id))
                _private_write(stage / "telegram_api_hash", telegram_api_hash.strip())
                if proxy_url:
                    _private_write(stage / "telegram_proxy_url", proxy_url.strip())
                _private_write(
                    stage / "preferences.json",
                    json.dumps({"language": language, "timezone": timezone}),
                )
                write_admin_credentials(stage / "admin_credentials.json", username, password)
                _sync_directory(stage)
                try:
                    os.rename(stage, self.install)
                except OSError as error:
                    if self.install.exists():
                        raise FileExistsError("First-run setup is already complete") from error
                    raise
                _sync_directory(self.root)
                self.code_file.unlink(missing_ok=True)
            finally:
                if stage.exists():
                    for child in stage.iterdir():
                        child.unlink()
                    stage.rmdir()
