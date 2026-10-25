"""Run the browser regression against disposable state, never the owner's .env."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def serve(root: Path, port: int) -> None:
    import uvicorn

    from telegram_media_automation.api.app import create_app
    from telegram_media_automation.infrastructure.config import Settings
    from telegram_media_automation.infrastructure.web_auth import write_admin_credentials

    settings = Settings(
        _env_file=None,
        api_token=secrets.token_urlsafe(32),
        data_dir=root,
        database_url=f"sqlite:///{root / 'state.sqlite3'}",
        admin_credentials_file=root / "admin.json",
        workers_enabled=False,
        download_incomplete=root / "incomplete",
        download_inbox=root / "inbox",
        movie_root=root / "movies",
        tv_root=root / "tv",
        backup_dir=root / "backups",
        telegram_session=root / "telegram.session",
    )
    write_admin_credentials(settings.admin_credentials_file, "admin", "Browser-test-only-2026")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--browser", choices=("chromium", "chrome"), default="chromium")
    parser.add_argument("--serve-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_root:
        serve(args.serve_root, args.port)
        return

    project = Path(__file__).resolve().parents[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("APP_", "TMA_", "WEBUI_TEST_"))
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    # Ignore host proxy settings even when the machine needs a Telegram proxy.
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    output = project / "dist" / "webui-review"
    output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="tma-browser-")
    with (
        temporary as directory,
        (output / "server.log").open("w", encoding="utf-8") as log,
    ):
        server = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--serve-root",
                directory,
                "--port",
                str(port),
            ],
            cwd=directory,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f"Isolated server exited; see {output / 'server.log'}")
                try:
                    with client.open(f"{url}/health/live", timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("Isolated server did not become healthy")
                time.sleep(0.2)
            request = urllib.request.Request(
                f"{url}/api/v1/auth/login",
                data=json.dumps(
                    {"username": "admin", "password": "Browser-test-only-2026"}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with client.open(request, timeout=10) as response:
                cookie = next(
                    value
                    for value in response.headers.get_all("Set-Cookie", [])
                    if value.startswith("tma_session=")
                )
            environment.update(
                WEBUI_TEST_URL=url,
                WEBUI_TEST_SESSION=cookie.split(";", 1)[0].split("=", 1)[1],
                WEBUI_TEST_BROWSER=args.browser,
            )
            subprocess.run(
                [args.node, "tests/web_workspace.browser.cjs"],
                cwd=project,
                env=environment,
                check=True,
                timeout=240,
            )
        finally:
            if os.name == "nt" and server.poll() is None:
                # Windows venv launchers can spawn a second Python process.
                # Stop only this owned process tree before removing its SQLite files.
                subprocess.run(
                    ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
            # Windows may release file handles shortly after process-tree exit.
            for attempt in range(20):
                try:
                    temporary.cleanup()
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.1)


if __name__ == "__main__":
    main()
