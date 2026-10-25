from pathlib import Path
from unittest.mock import patch

import pytest

from telegram_media_automation.infrastructure.config import Settings
from telegram_media_automation.infrastructure.initializer import initialize
from telegram_media_automation.infrastructure.web_auth import verify_admin_password


def test_initializer_creates_secret_files_and_migrated_database(tmp_path: Path) -> None:
    result = initialize(
        tmp_path,
        "12345",
        "api-hash",
        admin_username="owner",
        admin_password="strong-password",
        api_token="admin-token",
    )

    assert result.api_token == "admin-token"
    assert (tmp_path / ".env").is_file()
    assert (tmp_path / "secrets/api_token").read_text(encoding="utf-8").strip() == "admin-token"
    assert (tmp_path / "secrets/telegram_api_hash").read_text(
        encoding="utf-8"
    ).strip() == "api-hash"
    assert (tmp_path / "data/media_automation.sqlite3").is_file()
    assert (tmp_path / "downloads/incomplete").is_dir()
    assert "APP_ARCHIVE_MODE=move" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "APP_CLEANUP_ENABLED=true" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'APP_QUALITY_PREFERRED_RESOLUTIONS="[2160,1080,720]"' in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )
    assert "APP_SUBSCRIPTION_POLL_INTERVAL=300" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert verify_admin_password(
        tmp_path / "secrets/admin_credentials.json", "owner", "strong-password"
    )

    with pytest.raises(FileExistsError):
        initialize(
            tmp_path,
            "12345",
            "api-hash",
            admin_username="owner",
            admin_password="strong-password",
        )


def test_initializer_can_write_compose_paths_and_proxy_secret(tmp_path: Path) -> None:
    with (
        patch(
            "telegram_media_automation.infrastructure.initializer.os.getuid",
            return_value=1234,
            create=True,
        ),
        patch(
            "telegram_media_automation.infrastructure.initializer.os.getgid",
            return_value=5678,
            create=True,
        ),
    ):
        initialize(
            tmp_path,
            "12345",
            "api-hash",
            admin_username="owner",
            admin_password="strong-password",
            api_token="admin-token",
            proxy_url="socks5://user:password@proxy.example:1080",
            deployment="docker",
        )

    environment = (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'APP_DATABASE_URL="sqlite:////data/media_automation.sqlite3"' in environment
    assert 'APP_BACKUP_DIR="/backups"' in environment
    assert 'APP_API_TOKEN_FILE="/run/secrets/api_token"' in environment
    assert 'APP_ADMIN_CREDENTIALS_FILE="/run/secrets/admin_credentials.json"' in environment
    assert 'APP_TELEGRAM_PROXY_URL_FILE="/run/secrets/telegram_proxy_url"' in environment
    assert "TMA_HOST_MOVIE_DIR=" in environment
    assert "TMA_BIND_ADDRESS=127.0.0.1" in environment
    assert "TMA_HOST_PORT=8080" in environment
    assert "TMA_UID=1234" in environment
    assert "TMA_GID=5678" in environment
    assert "password" not in environment
    assert (tmp_path / "secrets/telegram_proxy_url").read_text(encoding="utf-8").strip() == (
        "socks5://user:password@proxy.example:1080"
    )


def test_initializer_validates_and_persists_custom_storage_paths(tmp_path: Path) -> None:
    custom_root = tmp_path / "custom storage"
    result = initialize(
        tmp_path / "product",
        "12345",
        "api-hash",
        admin_username="owner",
        admin_password="strong-password",
        language="en-US",
        timezone="Europe/Berlin",
        data_dir=custom_root / "state",
        backup_dir=custom_root / "backups",
        download_incomplete=custom_root / "partial",
        download_inbox=custom_root / "inbox",
        movie_root=custom_root / "movies",
        tv_root=custom_root / "shows",
        archive_mode="hardlink",
    )

    environment = result.env_file.read_text(encoding="utf-8")
    assert 'APP_LANGUAGE="en-US"' in environment
    assert 'APP_TIMEZONE="Europe/Berlin"' in environment
    assert "APP_ARCHIVE_MODE=hardlink" in environment
    assert f'APP_MOVIE_ROOT="{(custom_root / "movies").as_posix()}"' in environment
    assert (custom_root / "state/media_automation.sqlite3").is_file()
    assert (custom_root / "partial").is_dir()
    loaded = Settings(_env_file=result.env_file)
    assert loaded.language == "en-US"
    assert loaded.timezone == "Europe/Berlin"
    assert loaded.archive_mode == "hardlink"
    assert loaded.movie_root == custom_root / "movies"
    assert loaded.quality_policy().preferred_resolutions == (2160, 1080, 720)


def test_initializer_rejects_invalid_timezone_and_shared_operational_paths(
    tmp_path: Path,
) -> None:
    common = {
        "admin_username": "owner",
        "admin_password": "strong-password",
    }
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        initialize(tmp_path / "bad-zone", "12345", "api-hash", timezone="Mars/Base", **common)
    with pytest.raises(ValueError, match="must be different"):
        initialize(
            tmp_path / "shared-path",
            "12345",
            "api-hash",
            download_incomplete=Path("same"),
            download_inbox=Path("same"),
            **common,
        )
