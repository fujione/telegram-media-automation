import json
import sys
from pathlib import Path

from telegram_media_automation import cli
from telegram_media_automation.infrastructure.config import Settings
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.doctor import run_doctor
from telegram_media_automation.infrastructure.web_auth import write_admin_credentials


def configured_settings(tmp_path: Path) -> Settings:
    paths = {
        name: tmp_path / name
        for name in ("incomplete", "inbox", "movies", "tv", "backups", "sessions")
    }
    for path in paths.values():
        path.mkdir()
    database_path = tmp_path / "product.sqlite3"
    Database(f"sqlite:///{database_path}").migrate()
    admin_credentials = tmp_path / "admin_credentials.json"
    write_admin_credentials(admin_credentials, "owner", "strong-password")
    return Settings(
        _env_file=None,
        api_token="admin-token",
        admin_credentials_file=admin_credentials,
        telegram_api_id=12345,
        telegram_api_hash="api-hash",
        database_url=f"sqlite:///{database_path}",
        download_incomplete=paths["incomplete"],
        download_inbox=paths["inbox"],
        movie_root=paths["movies"],
        tv_root=paths["tv"],
        backup_dir=paths["backups"],
        telegram_session=paths["sessions"] / "telegram.session",
        minimum_free_bytes=0,
    )


def test_doctor_accepts_initialized_product_before_first_telegram_login(tmp_path: Path) -> None:
    report = run_doctor(configured_settings(tmp_path))

    assert report.healthy
    statuses = {check.name: check.status for check in report.checks}
    assert statuses["database"] == "ok"
    assert statuses["web_admin"] == "ok"
    assert statuses["telegram_session"] == "warning"
    assert all(status != "error" for status in statuses.values())


def test_doctor_reports_missing_required_configuration_without_secret_values(
    tmp_path: Path,
) -> None:
    settings = configured_settings(tmp_path).model_copy(
        update={
            "api_token": None,
            "telegram_api_id": None,
            "telegram_api_hash": None,
            "telegram_proxy_url": "socks5://secret-user:secret-password@proxy.example",
            "download_inbox": tmp_path / "missing-inbox",
        }
    )

    report = run_doctor(settings)
    rendered = "\n".join(check.message for check in report.checks)

    assert not report.healthy
    assert {check.name for check in report.checks if check.status == "error"} >= {
        "api_token",
        "telegram_credentials",
        "telegram_proxy",
        "download_inbox",
    }
    assert "secret-user" not in rendered
    assert "secret-password" not in rendered


def test_doctor_cli_emits_machine_readable_report(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = configured_settings(tmp_path)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["mediactl", "doctor", "--json"])

    cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["healthy"] is True
    assert {check["name"] for check in payload["checks"]} >= {
        "database",
        "telegram_credentials",
        "download_incomplete",
    }
    assert "admin-token" not in json.dumps(payload)
    assert "api-hash" not in json.dumps(payload)
