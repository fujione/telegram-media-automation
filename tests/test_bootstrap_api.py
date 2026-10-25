from pathlib import Path

from fastapi.testclient import TestClient

from telegram_media_automation.api.app import create_app
from telegram_media_automation.infrastructure.bootstrap import BootstrapStore
from telegram_media_automation.infrastructure.config import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        bootstrap_enabled=True,
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'state.sqlite3'}",
        telegram_session=tmp_path / "sessions/telegram.session",
        download_incomplete=tmp_path / "downloads/incomplete",
        download_inbox=tmp_path / "downloads/inbox",
        movie_root=tmp_path / "media/movies",
        tv_root=tmp_path / "media/tv",
        backup_dir=tmp_path / "backups",
        workers_enabled=False,
        cleanup_enabled=False,
    )


def test_web_first_run_requires_code_and_origin_then_preserves_session_after_restart(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    configuration = settings(tmp_path)
    app = create_app(configuration)
    api = TestClient(app, client=("192.0.2.9", 50000))
    code = BootstrapStore(configuration).setup_code()
    state = api.get("/api/v1/auth/setup").json()
    assert state == {
        "setup_required": True,
        "local_setup_allowed": True,
        "bootstrap_required": True,
    }
    assert code not in api.get("/api/v1/auth/setup").text
    payload = {
        "username": "admin",
        "password": "Test-admin-password-2026",
        "setup_code": "wrong",
        "telegram_api_id": 12345,
        "telegram_api_hash": "test-private-api-hash",
        "language": "en-US",
        "timezone": "UTC",
    }
    assert api.post("/api/v1/auth/setup", json=payload).status_code == 403
    payload["setup_code"] = code
    assert (
        api.post(
            "/api/v1/auth/setup", json=payload, headers={"Origin": "https://other.invalid"}
        ).status_code
        == 403
    )
    response = api.post("/api/v1/auth/setup", json=payload, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert response.json()["restart_required"] is True
    assert api.get("/api/v1/auth/session").status_code == 200
    assert api.get("/api/v1/system/status").json()["setup_restart_required"] is True
    assert api.post("/api/v1/auth/setup", json=payload).status_code == 409
    assert api.get("/api/v1/diagnostics").status_code == 200
    for secret in [code, payload["password"], payload["telegram_api_hash"]]:
        assert secret not in api.get("/api/v1/diagnostics").text
        assert secret not in caplog.text
    constructors = []

    class Gateway:
        def __init__(self, *args, **kwargs):
            constructors.append(args)

        async def is_authorized(self):
            return False

    monkeypatch.setattr("telegram_media_automation.api.app.TelethonGateway", Gateway)
    restarted = TestClient(create_app(configuration))
    restarted.cookies.update(api.cookies)
    assert restarted.get("/api/v1/auth/session").status_code == 200
    assert restarted.get("/api/v1/ui/config").json() == {"language": "en-US", "timezone": "UTC"}
    assert restarted.get("/api/v1/system/status").json()["setup_restart_required"] is False
    assert len(constructors) == 1
    assert constructors[0][1:] == (12345, "test-private-api-hash")
    assert restarted.get("/api/v1/auth/setup").json()["setup_required"] is False
    assert restarted.post("/api/v1/auth/setup", json=payload).status_code == 409


def test_bootstrap_wrong_codes_are_rate_limited_without_creating_an_install(tmp_path: Path) -> None:
    configuration = settings(tmp_path)
    api = TestClient(create_app(configuration))
    payload = {
        "username": "admin",
        "password": "Test-admin-password-2026",
        "setup_code": "wrong",
        "telegram_api_id": 12345,
        "telegram_api_hash": "test-private-api-hash",
    }
    for _ in range(5):
        assert api.post("/api/v1/auth/setup", json=payload).status_code == 403
    limited = api.post("/api/v1/auth/setup", json=payload)
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "300"
    assert not BootstrapStore(configuration).install.exists()


def test_fresh_configuration_to_archive_subscription_backup_and_restart_with_controlled_gateway(
    tmp_path: Path, monkeypatch
) -> None:
    import sqlite3
    import time
    import zipfile

    from telegram_media_automation.application.telegram_types import ChannelScan
    from telegram_media_automation.domain.resources import EpisodeRange, MediaResource
    from telegram_media_automation.infrastructure.backup import restore_backup
    from telegram_media_automation.infrastructure.telegram_gateway import TelegramChannelInfo

    configuration = settings(tmp_path).model_copy(
        update={"workers_enabled": True, "worker_poll_interval": 0.01, "minimum_free_bytes": 0}
    )
    first = TestClient(create_app(configuration))
    payload = {
        "username": "admin",
        "password": "Flow-fixture-password-2026",
        "setup_code": BootstrapStore(configuration).setup_code(),
        "telegram_api_id": 12345,
        "telegram_api_hash": "flow-fixture-api-hash",
    }
    assert first.post("/api/v1/auth/setup", json=payload).status_code == 200
    cookies = first.cookies
    first.app.state.database.engine.dispose()
    marker = tmp_path / "controlled-gateway-authorized"
    active = []

    class Source:
        async def chunks(self, offset, chunk_size):
            yield (b"x" * 128)[offset:]

    class Gateway:
        def __init__(self, *args, **kwargs):
            active.append(self)
            assert len(active) == 1

        async def is_authorized(self):
            return marker.exists()

        async def request_login_code(self, phone):
            return "controlled-phone-hash"

        async def sign_in(self, phone, code, phone_code_hash):
            assert code == "12345" and phone_code_hash == "controlled-phone-hash"
            marker.write_text("controlled test authorization")

        async def inspect_channel(self, channel):
            assert marker.exists()
            return TelegramChannelInfo(channel, "Fixture source", "public", channel, -10012345)

        async def search(self, channels, query, limit=100):
            assert marker.exists()
            return [
                MediaResource(
                    provider="telegram",
                    locator="controlled:" + query,
                    file_name=query + ".mkv",
                    file_size=128,
                    identity_confidence=95,
                    episode_range=EpisodeRange(1, 1, 1) if "Series" in query else None,
                )
            ]

        async def scan_channel(self, channel, query, min_message_id, limit=100):
            return ChannelScan(await self.search([channel], query) if not min_message_id else [], 1)

        async def download_source(self, resource):
            return Source()

        async def disconnect(self):
            active.remove(self)

    monkeypatch.setattr("telegram_media_automation.api.app.TelethonGateway", Gateway)
    with TestClient(create_app(configuration)) as api:
        api.cookies.update(cookies)
        headers = {"X-CSRF-Token": api.cookies["tma_csrf"]}
        assert api.get("/api/v1/auth/session").status_code == 200
        assert (
            api.post(
                "/api/v1/telegram/login/code", headers=headers, json={"phone": "+15550000001"}
            ).status_code
            == 200
        )
        assert (
            api.post(
                "/api/v1/telegram/login/verify", headers=headers, json={"code": "12345"}
            ).status_code
            == 200
        )
        assert api.get("/health/ready").status_code == 200
        assert (
            api.post(
                "/api/v1/channels", headers=headers, json={"identifier": "fixture_channel"}
            ).status_code
            == 201
        )
        resource = api.post(
            "/api/v1/searches", headers=headers, json={"query": "Fixture Movie"}
        ).json()[0]
        created = api.post(
            "/api/v1/tasks",
            headers=headers,
            json={
                "resource_id": resource["id"],
                "archive_plan": {
                    "kind": "movie",
                    "title": "Fixture Movie",
                    "confirmed_identity": True,
                },
            },
        )
        assert created.status_code == 201
        task = created.json()
        deadline = time.monotonic() + 3
        while task["state"] != "imported" and time.monotonic() < deadline:
            time.sleep(0.01)
            task = api.get("/api/v1/tasks/" + task["id"]).json()
        assert task["state"] == "imported"
        assert Path(task["output_path"]).read_bytes() == b"x" * 128
        subscription = api.post(
            "/api/v1/subscriptions",
            headers=headers,
            json={
                "title": "Fixture Series",
                "query": "Fixture Series",
                "season": 1,
                "next_episode": 1,
            },
        ).json()
        path = "/api/v1/subscriptions/" + subscription["id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = api.get(path).json()
            if (
                current["next_episode"] == 2
                and api.get("/api/v1/tasks/summary").json()["completed"] == 2
            ):
                break
            time.sleep(0.01)
        assert current["next_episode"] == 2
        assert current["scan_health"]["last_enqueued_at"]
        assert api.get(path + "/scans").json()
        assert api.get("/api/v1/tasks/summary").json()["completed"] == 2
        backup = api.post("/api/v1/backups", headers=headers).json()
        archive = configuration.backup_dir / backup["name"]
        with zipfile.ZipFile(archive) as bundle:
            assert set(bundle.namelist()) == {"database.sqlite3", "manifest.json"}
        cookies = api.cookies
    assert not active
    with TestClient(create_app(configuration)) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get("/api/v1/auth/session").status_code == 200
        assert restarted.get("/health/ready").status_code == 200
        assert restarted.get("/api/v1/tasks/summary").json()["total"] == 2
        assert restarted.get(path).json()["next_episode"] == 2
        assert restarted.get(path + "/scans").json()
    assert not active
    restored, session = restore_backup(archive, tmp_path / "restored")
    assert session is None
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT COUNT(*) FROM download_tasks").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 1
