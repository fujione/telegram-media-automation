from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from telegram_media_automation.infrastructure.bootstrap import BootstrapStore
from telegram_media_automation.infrastructure.config import Settings
from telegram_media_automation.infrastructure.web_auth import (
    load_admin_credentials,
    verify_admin_password,
)


def configured_store(tmp_path: Path) -> tuple[BootstrapStore, Settings]:
    settings = Settings(_env_file=None, data_dir=tmp_path, bootstrap_enabled=True)
    store = BootstrapStore(settings)
    settings = store.configured_settings(settings)
    store.prepare()
    return store, settings


def test_first_run_is_private_persistent_and_publishes_one_complete_install(tmp_path: Path) -> None:
    store, settings = configured_store(tmp_path)
    token = settings.resolved_api_token()
    code = store.setup_code()
    assert token and token != code
    with pytest.raises(PermissionError):
        store.complete(
            code="incorrect",
            username="admin",
            password="A-password-2026",
            telegram_api_id=12345,
            telegram_api_hash="test-api-hash",
        )
    assert not store.install.exists()
    store.complete(
        code=code,
        username="admin",
        password="A-password-2026",
        telegram_api_id=12345,
        telegram_api_hash="test-api-hash",
        proxy_url="socks5://user:password@proxy.invalid:1080",
        language="en-US",
        timezone="UTC",
    )
    assert not store.code_file.exists()
    with pytest.raises(FileExistsError):
        store.setup_code()
    with pytest.raises(FileExistsError):
        store.complete(
            code=code,
            username="other",
            password="A-password-2026",
            telegram_api_id=9,
            telegram_api_hash="other",
        )
    restarted, restored = configured_store(tmp_path)
    assert restored.resolved_api_token() == token
    assert restored.resolved_telegram_credentials() == (12345, "test-api-hash")
    assert restored.resolved_telegram_proxy()[1] == "proxy.invalid"
    assert restored.language == "en-US" and restored.timezone == "UTC"
    admin = load_admin_credentials(restored.admin_credentials_file)
    assert admin and verify_admin_password(
        restored.admin_credentials_file, "admin", "A-password-2026"
    )
    assert not restarted.code_file.exists()
    assert not list(store.root.glob(".install-*"))


def test_first_run_failures_leave_no_partial_install_and_allow_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import telegram_media_automation.infrastructure.bootstrap as bootstrap

    store, _ = configured_store(tmp_path)
    code = store.setup_code()
    payload = dict(
        code=code,
        username="admin",
        password="A-password-2026",
        telegram_api_id=12345,
        telegram_api_hash="test-api-hash",
    )
    with pytest.raises(ValueError):
        store.complete(**{**payload, "proxy_url": "file:///private"})
    original = bootstrap.write_admin_credentials

    def fail(*args, **kwargs):
        raise OSError("Injected filesystem failure")

    monkeypatch.setattr(bootstrap, "write_admin_credentials", fail)
    with pytest.raises(OSError):
        store.complete(**payload)
    assert not store.install.exists() and store.setup_code() == code
    assert not list(store.root.glob(".install-*"))
    monkeypatch.setattr(bootstrap, "write_admin_credentials", original)
    store.complete(**payload)
    assert store.install.exists()


def test_concurrent_first_run_startups_share_secrets_and_only_one_install_wins(
    tmp_path: Path,
) -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: configured_store(tmp_path), range(4)))
    assert len({settings.resolved_api_token() for _, settings in results}) == 1
    assert len({store.setup_code() for store, _ in results}) == 1
    code = results[0][0].setup_code()

    def finish(pair):
        store, _ = pair
        try:
            store.complete(
                code=code,
                username="admin",
                password="A-password-2026",
                telegram_api_id=12345,
                telegram_api_hash="test-api-hash",
            )
            return True
        except OSError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(finish, results))
    assert outcomes.count(True) == 1
    assert not list(results[0][0].root.glob(".install-*"))


def test_first_run_rejects_existing_secret_configuration(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, data_dir=tmp_path, bootstrap_enabled=True, api_token="existing-secret"
    )
    with pytest.raises(ValueError, match="existing deployment"):
        BootstrapStore(settings).configured_settings(settings)
    assert not (tmp_path / "configuration").exists()


def test_setup_code_command_is_local_and_expires_after_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    from telegram_media_automation import cli

    store, configuration = configured_store(tmp_path)
    monkeypatch.setattr(cli, "Settings", lambda: configuration)
    monkeypatch.setattr(sys, "argv", ["mediactl", "setup-code"])
    cli.main()
    assert capsys.readouterr().out.strip() == store.setup_code()
    code = store.setup_code()
    store.complete(
        code=code,
        username="admin",
        password="A-password-2026",
        telegram_api_id=12345,
        telegram_api_hash="test-api-hash",
    )
    with pytest.raises(SystemExit, match="unavailable or setup is already complete"):
        cli.main()
    assert code not in capsys.readouterr().out
