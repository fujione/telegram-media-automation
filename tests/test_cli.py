import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_media_automation import cli
from telegram_media_automation.infrastructure.config import Settings


def test_windows_cli_forces_utf8_console_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stream:
        def __init__(self) -> None:
            self.encoding: str | None = None

        def reconfigure(self, *, encoding: str) -> None:
            self.encoding = encoding

    stdout = Stream()
    stderr = Stream()
    monkeypatch.setattr(
        cli,
        "sys",
        SimpleNamespace(platform="win32", stdout=stdout, stderr=stderr),
    )

    cli._configure_windows_console_output()

    assert stdout.encoding == "utf-8"
    assert stderr.encoding == "utf-8"


def test_serve_preflight_reports_port_conflict_with_alternative() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        with pytest.raises(SystemExit) as raised:
            cli.ensure_port_available("127.0.0.1", port)
    finally:
        listener.close()

    message = str(raised.value)
    assert str(port) in message
    assert f"--port {port + 1}" in message


def test_rotate_token_cli_replaces_secret_without_printing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = tmp_path / "api_token"
    secret.write_text("exposed-token\n", encoding="utf-8")
    settings = Settings(_env_file=None, api_token_file=secret)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["mediactl", "rotate-token"])

    cli.main()

    replacement = secret.read_text(encoding="utf-8").strip()
    output = capsys.readouterr().out
    assert replacement != "exposed-token"
    assert len(replacement) >= 32
    assert replacement not in output
    assert "exposed-token" not in output


def test_set_admin_cli_hashes_password_without_printing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from telegram_media_automation.infrastructure.web_auth import verify_admin_password

    credentials = tmp_path / "admin_credentials.json"
    settings = Settings(_env_file=None, admin_credentials_file=credentials)
    answers = iter(["strong-password", "strong-password"])
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))
    monkeypatch.setattr(sys, "argv", ["mediactl", "set-admin", "--username", "owner"])

    cli.main()

    output = capsys.readouterr().out
    assert verify_admin_password(credentials, "owner", "strong-password")
    assert "strong-password" not in output
    assert "owner" in output
