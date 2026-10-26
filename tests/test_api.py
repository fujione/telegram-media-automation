import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from telegram_media_automation.api.app import create_app
from telegram_media_automation.application.telegram_types import ChannelScan
from telegram_media_automation.domain.resources import MediaResource, ResourceSource
from telegram_media_automation.infrastructure.config import Settings
from telegram_media_automation.infrastructure.telegram_gateway import TelegramChannelInfo
from telegram_media_automation.infrastructure.web_auth import write_admin_credentials


class SearchGateway:
    def __init__(self) -> None:
        self.authorized = False
        self.search_calls: list[tuple[list[str | int], str]] = []

    async def search(
        self, channels: list[str | int], query: str, limit: int = 100
    ) -> list[MediaResource]:
        self.search_calls.append((channels, query))
        return [
            MediaResource(
                provider="telegram",
                locator=f"{channels[0]}:{query}",
                file_name="result.mkv",
                file_size=42,
                identity_confidence=90,
                source=ResourceSource(
                    channel_id=-100123456,
                    message_id=42,
                    channel_title="Public Test Channel",
                    channel_username="public_channel",
                    message_link="https://t.me/public_channel/42",
                ),
            )
        ]

    async def is_authorized(self) -> bool:
        return self.authorized

    async def request_login_code(self, phone: str) -> str:
        assert phone.startswith("+")
        return "phone-hash"

    async def sign_in(self, phone: str, code: str, phone_code_hash: str) -> None:
        assert phone.startswith("+") and code == "12345" and phone_code_hash == "phone-hash"
        self.authorized = True

    async def sign_in_password(self, password: str) -> None:
        assert password
        self.authorized = True

    async def inspect_channel(self, channel: str | int) -> TelegramChannelInfo:
        if isinstance(channel, int):
            return TelegramChannelInfo(
                identifier=str(channel),
                title="Private Test Channel",
                visibility="private",
                username=None,
                chat_id=channel,
            )
        return TelegramChannelInfo(
            identifier=channel,
            title="Public Test Channel",
            visibility="public",
            username=channel,
            chat_id=-100123456,
        )

    async def list_channels(self, limit: int = 200) -> list[TelegramChannelInfo]:
        assert limit == 200
        return [
            await self.inspect_channel(-100987654),
            await self.inspect_channel("public_channel"),
        ]


class DownloadSource:
    async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
        value = b"x" * 42
        yield value[offset : offset + chunk_size]


class FullGateway(SearchGateway):
    async def download_source(self, _resource: MediaResource) -> DownloadSource:
        return DownloadSource()

    async def scan_channel(
        self, channel: str | int, query: str, min_message_id: int, limit: int = 100
    ) -> ChannelScan:
        return ChannelScan([], min_message_id)

    async def disconnect(self) -> None:
        return None


class CountingFullGateway(FullGateway):
    def __init__(self) -> None:
        super().__init__()
        self.scan_calls = 0

    async def scan_channel(
        self, channel: str | int, query: str, min_message_id: int, limit: int = 100
    ) -> ChannelScan:
        self.scan_calls += 1
        return await super().scan_channel(channel, query, min_message_id, limit)


def client(
    tmp_path: Path, token: str | None = "secret", *, with_telegram: bool = False
) -> TestClient:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'api.sqlite3'}",
        data_dir=tmp_path,
        backup_dir=tmp_path / "backups",
        api_token=token,
    )
    gateway = SearchGateway() if with_telegram else None
    return TestClient(create_app(settings, telegram_gateway=gateway))


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("authorized", "authorized"),
        ("login_required", "login_required"),
        ("timeout", "telegram_timeout"),
        ("proxy", "telegram_proxy_failed"),
        ("network", "telegram_connection_failed"),
        ("other", "telegram_operation_failed"),
        ("rate_limited", "telegram_rate_limited"),
    ],
)
def test_readiness_distinguishes_network_proxy_and_login_without_exposing_errors(
    tmp_path: Path,
    mode: str,
    expected: str,
) -> None:
    private_detail = "socks5://private-user:private-password@proxy.invalid:1234"

    class StatusGateway:
        async def is_authorized(self) -> bool:
            if mode == "timeout":
                raise TimeoutError(private_detail)
            if mode == "network":
                raise ConnectionError(private_detail)
            if mode == "proxy":
                raise type("ProxyConnectionError", (Exception,), {})(private_detail)
            if mode == "rate_limited":
                raise type("FloodWaitError", (Exception,), {"seconds": 17})(private_detail)
            if mode == "other":
                raise RuntimeError(private_detail)
            return mode == "authorized"

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        api_token="secret",
        database_url=f"sqlite:///{tmp_path / 'health.sqlite3'}",
    )
    app = create_app(settings, telegram_gateway=StatusGateway())
    app.state.workers_running = True
    api = TestClient(app)
    response = api.get("/health/ready")
    assert response.status_code == (200 if mode == "authorized" else 503)
    assert response.json()["telegram_status"] == expected
    assert response.json()["checks"]["telegram"] is (mode == "authorized")
    assert api.get("/health/live").status_code == 200
    assert "private-password" not in response.text and "proxy.invalid" not in response.text
    if mode == "proxy":
        detail = api.get("/api/v1/telegram/status", headers={"Authorization": "Bearer secret"})
        assert detail.status_code == 503
        assert detail.json()["detail"]["code"] == "telegram_proxy_failed"
        assert "private-password" not in detail.text


def test_proxy_configuration_is_parsed_and_validated(tmp_path: Path) -> None:
    secret = tmp_path / "proxy"
    secret.write_text("socks5://user:p%40ss@proxy.example:1080\n", encoding="utf-8")
    settings = Settings(_env_file=None, telegram_proxy_url_file=secret)
    assert settings.resolved_telegram_proxy() == (
        "socks5",
        "proxy.example",
        1080,
        True,
        "user",
        "p@ss",
    )


def test_scan_status_and_diagnostic_export_survive_an_application_restart(tmp_path: Path) -> None:
    api = client(tmp_path)
    headers = {"Authorization": "Bearer secret"}
    created = api.post(
        "/api/v1/subscriptions",
        headers=headers,
        json={
            "title": "Private show title",
            "query": "Private search query",
            "season": 1,
            "next_episode": 2,
            "channels": ["private_channel"],
        },
    ).json()
    key = created["id"]
    path = f"/api/v1/subscriptions/{key}"
    assert api.post(f"{path}/check").status_code == 401
    assert api.post(f"{path}/check", headers=headers).json()["status"] == "already_queued"
    from datetime import UTC, datetime
    from uuid import UUID

    report = {
        "state": "succeeded",
        "version": 1,
        "checked_at": datetime.now(UTC).isoformat(),
        "outcome": "waiting",
        "successful_channels": 1,
        "channel_count": 1,
    }
    api.app.state.scan_repository.record_status(UUID(key), report)
    restarted = client(tmp_path)
    value = restarted.get(path, headers=headers).json()
    assert value["scan_status"]["state"] == "succeeded"
    assert value["scan_health"]["last_success_at"] == report["checked_at"]
    assert restarted.get(f"{path}/scans", headers=headers).json() == [report]
    assert restarted.get(f"{path}/scans?limit=51", headers=headers).status_code == 422
    assert restarted.get("/api/v1/diagnostics").status_code == 401
    preview = restarted.get("/api/v1/diagnostics", headers=headers)
    assert preview.status_code == 200 and preview.headers["Cache-Control"] == "no-store"
    for private in ["Private show title", "Private search query", "private_channel", key]:
        assert private not in preview.text
    download = restarted.get("/api/v1/diagnostics/download", headers=headers)
    assert download.status_code == 200 and "attachment" in download.headers["Content-Disposition"]
    assert download.json()["subscriptions"] == preview.json()["subscriptions"]
    restarted.post(f"{path}/pause", headers=headers)
    assert restarted.post(f"{path}/check", headers=headers).status_code == 409


def test_public_ui_config_exposes_only_locale_settings(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'locale.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
        language="en-US",
        timezone="Europe/Berlin",
    )
    api = TestClient(create_app(settings))

    response = api.get("/api/v1/ui/config")

    assert response.status_code == 200
    assert response.json() == {"language": "en-US", "timezone": "Europe/Berlin"}
    assert "token" not in response.text.casefold()


def test_liveness_is_public_but_tasks_require_authentication(tmp_path: Path) -> None:
    api = client(tmp_path)
    page = api.get("/").text
    assert "媒体下载工作台" in page
    assert 'id="login-form"' in page
    assert "使用你的管理员账号登录" in page
    assert 'id="section-channels-panel" open' in page
    assert 'id="toggle-sections"' in page
    assert 'id="result-pager"' in page
    assert "没有 Bot" not in page
    assert '<form id="phone-form" hidden>' in page
    assert "API Token<input" not in page
    assert "下载并自动入库" in page
    assert "仅下载" in page
    assert 'id="search-resolution"' in page
    assert 'id="search-max-size"' in page
    assert 'id="subscription-pending-episodes"' in page
    assert 'id="subscription-continue"' in page
    assert 'id="task-filter"' in page
    assert 'id="task-pager"' in page
    assert 'id="enqueue-replace"' in page
    assert 'id="enqueue-target"' in page
    assert 'id="import-replace"' in page
    assert 'id="import-target"' in page
    script = api.get("/static/app.js")
    assert script.status_code == 200
    workspace = api.get("/static/workspace.js")
    assert workspace.status_code == 200
    assert "navigateWorkspace" in workspace.text
    assert "pageSlice" in workspace.text
    assert "sessionStorage" not in script.text
    assert "payload.archive_plan = archivePlan" in script.text
    assert 'fetch("/api/v1/ui/config"' in script.text
    assert "Intl.DateTimeFormat" in script.text
    assert 'api("/api/v1/media/suggestions"' in script.text
    assert 'api("/api/v1/settings/quality"' in script.text
    assert "api(`/api/v1/tasks/page?" in script.text
    assert "enqueueIdempotencyKey = newIdempotencyKey()" in script.text
    assert '"Idempotency-Key": enqueueIdempotencyKey' in script.text
    assert "api(`/api/v1/resources/${resourceId}`)" in script.text
    assert api.get("/static/favicon.svg").status_code == 200
    assert api.get("/health/live").status_code == 200
    assert api.get("/api/v1/ui/config").json() == {
        "language": "zh-CN",
        "timezone": "Asia/Shanghai",
    }
    assert (
        api.get("/health/live", headers={"X-Request-ID": "test-request"}).headers["X-Request-ID"]
        == "test-request"
    )
    assert api.get("/health/ready").status_code == 503
    assert api.get("/api/v1/tasks").status_code == 401
    assert api.get("/api/v1/tasks/page").status_code == 401
    system = api.get("/api/v1/system/status", headers={"Authorization": "Bearer secret"})
    assert system.status_code == 200
    assert system.json()["schema_version"] == 14
    assert system.json()["archive_mode"] == "move"
    assert system.json()["maintenance_running"] is False
    assert system.json()["subscription_poll_interval"] == 300
    assert set(system.json()["directories"]) == {
        "incomplete",
        "inbox",
        "movies",
        "tv",
        "backups",
    }


def test_file_maintenance_runs_with_application_lifespan(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        system = api.get("/api/v1/system/status", headers={"Authorization": "Bearer secret"})

        assert system.status_code == 200
        assert system.json()["maintenance_running"] is True


def test_routine_poll_is_debug_but_failed_request_remains_info(tmp_path: Path, monkeypatch) -> None:
    api = client(tmp_path)
    levels: list[int] = []

    def record(level: int, *_args: object, **_kwargs: object) -> None:
        levels.append(level)

    monkeypatch.setattr(logging.getLogger("http"), "log", record)

    assert api.get("/health/live").status_code == 200
    assert api.get("/api/v1/tasks").status_code == 401

    assert levels == [logging.DEBUG, logging.INFO]


def test_create_list_and_download_safe_database_backup(tmp_path: Path) -> None:
    api = client(tmp_path)
    headers = {"Authorization": "Bearer secret"}

    created = api.post("/api/v1/backups", headers=headers)
    assert created.status_code == 201
    assert created.json()["name"].endswith(".zip")
    assert created.json()["size"] > 0
    assert api.get("/api/v1/backups", headers=headers).json() == [created.json()]
    downloaded = api.get(f"/api/v1/backups/{created.json()['name']}", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"
    assert api.get("/api/v1/backups/..%2F.env", headers=headers).status_code == 404


def test_missing_token_is_setup_required(tmp_path: Path) -> None:
    api = client(tmp_path, None)
    assert api.get("/api/v1/tasks").status_code == 503


def test_web_login_uses_secure_cookie_and_csrf_for_writes(tmp_path: Path) -> None:
    credentials = tmp_path / "secrets/admin_credentials.json"
    write_admin_credentials(credentials, "owner", "strong-password")
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'web-login.sqlite3'}",
        data_dir=tmp_path,
        backup_dir=tmp_path / "backups",
        api_token="secret",
        admin_credentials_file=credentials,
    )
    browser = TestClient(create_app(settings))

    assert browser.get("/api/v1/tasks").status_code == 401
    failed = browser.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "wrong-password"},
    )
    assert failed.status_code == 401
    login = browser.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "strong-password"},
    )
    assert login.status_code == 200
    assert login.json() == {"authenticated": True, "username": "owner"}
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert browser.get("/api/v1/tasks").status_code == 200
    assert browser.post("/api/v1/backups").status_code == 403

    csrf = browser.cookies.get("tma_csrf")
    assert csrf
    assert browser.post("/api/v1/backups", headers={"X-CSRF-Token": csrf}).status_code == 201
    assert browser.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert browser.get("/api/v1/tasks").status_code == 401


def test_existing_install_can_create_first_admin_from_local_browser(tmp_path: Path) -> None:
    credentials = tmp_path / "secrets/admin_credentials.json"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'web-setup.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
        admin_credentials_file=credentials,
    )
    browser = TestClient(create_app(settings))

    assert browser.get("/api/v1/auth/setup").json() == {
        "setup_required": True,
        "local_setup_allowed": True,
    }
    created = browser.post(
        "/api/v1/auth/setup",
        json={"username": "owner", "password": "strong-password"},
    )
    assert created.status_code == 200
    assert credentials.is_file()
    assert browser.get("/api/v1/auth/session").json() == {
        "authenticated": True,
        "username": "owner",
    }
    duplicate = browser.post(
        "/api/v1/auth/setup",
        json={"username": "other", "password": "another-password"},
    )
    assert duplicate.status_code == 409


def test_web_login_rate_limits_repeated_failures(tmp_path: Path) -> None:
    credentials = tmp_path / "secrets/admin_credentials.json"
    write_admin_credentials(credentials, "owner", "strong-password")
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'web-rate-limit.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
        admin_credentials_file=credentials,
    )
    browser = TestClient(create_app(settings))
    payload = {"username": "owner", "password": "wrong-password"}

    for _ in range(5):
        assert browser.post("/api/v1/auth/login", json=payload).status_code == 401
    limited = browser.post("/api/v1/auth/login", json=payload)

    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "300"


def test_create_get_transition_and_events(tmp_path: Path) -> None:
    api = client(tmp_path, with_telegram=True)
    headers = {"Authorization": "Bearer secret", "Idempotency-Key": "create-1"}
    searched = api.post(
        "/api/v1/searches",
        json={"query": "A Show", "channels": ["channel"]},
        headers=headers,
    )
    assert searched.status_code == 200
    payload = {"request_id": str(uuid4()), "resource_id": searched.json()[0]["id"]}
    created = api.post("/api/v1/tasks", json=payload, headers=headers)
    assert created.status_code == 201
    task = created.json()
    assert task["state"] == "queued"

    selected = api.post(
        f"/api/v1/tasks/{task['id']}/retry",
        json={"reason": "network"},
        headers={"Authorization": "Bearer secret"},
    )
    assert selected.status_code == 200
    assert selected.json()["state"] == "retrying"

    cancelled = api.post(
        f"/api/v1/tasks/{task['id']}/cancel",
        json={"reason": "user request"},
        headers={"Authorization": "Bearer secret"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"

    events = api.get(
        f"/api/v1/tasks/{task['id']}/events",
        headers={"Authorization": "Bearer secret"},
    )
    assert [event["new_state"] for event in events.json()] == [
        "pending",
        "candidate_selected",
        "queued",
        "retrying",
        "cancelled",
    ]


def test_paginated_task_query_filters_state_and_fetches_resource(tmp_path: Path) -> None:
    api = client(tmp_path, with_telegram=True)
    headers = {"Authorization": "Bearer secret"}
    resource = api.post(
        "/api/v1/searches",
        json={"query": "A Show", "channels": ["channel"]},
        headers=headers,
    ).json()[0]
    tasks = [
        api.post(
            "/api/v1/tasks",
            json={"resource_id": resource["id"]},
            headers=headers,
        ).json()
        for _ in range(3)
    ]
    api.post(f"/api/v1/tasks/{tasks[0]['id']}/cancel", json={}, headers=headers)

    first = api.get("/api/v1/tasks/page?limit=2", headers=headers)
    queued = api.get("/api/v1/tasks/page?states=queued", headers=headers)
    cancelled = api.get("/api/v1/tasks/page?states=cancelled", headers=headers)
    summary = api.get("/api/v1/tasks/summary", headers=headers)

    assert first.status_code == 200
    assert first.json()["total"] == 3
    assert len(first.json()["items"]) == 2
    assert queued.json()["total"] == 2
    assert {task["state"] for task in queued.json()["items"]} == {"queued"}
    assert cancelled.json()["total"] == 1
    assert summary.json() == {"total": 3, "active": 2, "attention": 0, "completed": 1}
    assert api.get(f"/api/v1/resources/{resource['id']}", headers=headers).json() == resource
    assert api.get("/api/v1/tasks/page?states=unknown", headers=headers).status_code == 422
    assert api.get("/api/v1/tasks/page?offset=-1", headers=headers).status_code == 422


def test_api_idempotency_works_when_request_id_is_omitted(tmp_path: Path) -> None:
    api = client(tmp_path, with_telegram=True)
    auth = {"Authorization": "Bearer secret"}
    resource = api.post(
        "/api/v1/searches",
        json={"query": "A Show", "channels": ["channel"]},
        headers=auth,
    ).json()[0]
    headers = {**auth, "Idempotency-Key": "same-create-command"}
    payload = {"resource_id": resource["id"]}

    first = api.post("/api/v1/tasks", json=payload, headers=headers)
    second = api.post("/api/v1/tasks", json=payload, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["request_id"] == second.json()["request_id"]
    too_long = api.post(
        "/api/v1/tasks",
        json=payload,
        headers={**auth, "Idempotency-Key": "x" * 201},
    )
    assert too_long.status_code == 422


def test_search_requires_telegram_and_persists_candidates(tmp_path: Path) -> None:
    headers = {"Authorization": "Bearer secret"}
    unavailable = client(tmp_path)
    assert (
        unavailable.post(
            "/api/v1/searches",
            json={"query": "A Show", "channels": ["channel"]},
            headers=headers,
        ).status_code
        == 503
    )

    api = client(tmp_path / "configured", with_telegram=True)
    response = api.post(
        "/api/v1/searches",
        json={"query": "A Show", "channels": ["channel"]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()[0]["file_name"] == "result.mkv"
    assert response.json()[0]["source"] == {
        "channel_id": -100123456,
        "message_id": 42,
        "channel_title": "Public Test Channel",
        "channel_username": "public_channel",
        "message_link": "https://t.me/public_channel/42",
        "forwarded_from_channel_id": None,
        "forwarded_from_message_id": None,
        "forwarded_from_title": None,
        "forwarded_from_username": None,
        "forwarded_from_link": None,
    }
    assert api.get("/api/v1/resources", headers=headers).json() == response.json()


def test_media_identity_suggestion_is_authenticated_and_structured(tmp_path: Path) -> None:
    api = client(tmp_path)
    payload = {"text": "A Show (2026) S01E02 1080p"}

    assert api.post("/api/v1/media/suggestions", json=payload).status_code == 401
    response = api.post(
        "/api/v1/media/suggestions",
        json=payload,
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "title": "A Show",
        "year": 2026,
        "kind": "series",
        "season": 1,
        "episode": 2,
        "episode_end": None,
    }


def test_quality_settings_and_per_search_hard_filters(tmp_path: Path) -> None:
    api = client(tmp_path, with_telegram=True)
    headers = {"Authorization": "Bearer secret"}

    assert api.get("/api/v1/settings/quality").status_code == 401
    settings = api.get("/api/v1/settings/quality", headers=headers)
    filtered = api.post(
        "/api/v1/searches",
        json={
            "query": "A Show",
            "channels": ["channel"],
            "resolutions": [1080],
            "languages": ["zh"],
            "max_file_size": 100,
            "prefer_hdr": True,
        },
        headers=headers,
    )

    assert settings.status_code == 200
    assert settings.json()["preferred_resolutions"] == [2160, 1080, 720]
    assert settings.json()["preferred_languages"] == ["zh", "en"]
    assert filtered.status_code == 200
    assert filtered.json() == []


def test_automatic_archive_plan_requires_explicit_identity_confirmation(tmp_path: Path) -> None:
    api = client(tmp_path, with_telegram=True)
    headers = {"Authorization": "Bearer secret"}
    resource = api.post(
        "/api/v1/searches",
        json={"query": "A Movie", "channels": ["channel"]},
        headers=headers,
    ).json()[0]

    response = api.post(
        "/api/v1/tasks",
        json={
            "resource_id": resource["id"],
            "archive_plan": {"kind": "movie", "title": "A Movie"},
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "archive_identity_confirmation_required"


def test_manage_channels_and_use_enabled_channels_as_search_default(tmp_path: Path) -> None:
    gateway = SearchGateway()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'channels.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
        telegram_search_channels=("environment-fallback",),
    )
    api = TestClient(create_app(settings, telegram_gateway=gateway))
    headers = {"Authorization": "Bearer secret"}

    public_info = api.post(
        "/api/v1/channels/inspect",
        json={"identifier": "https://t.me/channel-a"},
        headers=headers,
    )
    private_info = api.post(
        "/api/v1/channels/inspect",
        json={"identifier": "https://t.me/c/1234567890/55"},
        headers=headers,
    )
    assert public_info.json() == {
        "identifier": "channel-a",
        "title": "Public Test Channel",
        "visibility": "public",
        "username": "channel-a",
        "chat_id": -100123456,
    }
    assert private_info.json()["visibility"] == "private"
    assert private_info.json()["identifier"] == "-1001234567890"
    discovered = api.get("/api/v1/channels/discover", headers=headers)
    assert [item["visibility"] for item in discovered.json()] == ["private", "public"]
    assert api.get("/api/v1/channels/discover?limit=0", headers=headers).status_code == 422

    first = api.post(
        "/api/v1/channels",
        json={"identifier": "https://t.me/channel-a", "name": "A"},
        headers=headers,
    )
    numeric = api.post(
        "/api/v1/channels",
        json={"identifier": "-1001234567890"},
        headers=headers,
    )
    assert first.status_code == numeric.status_code == 201
    invalid = api.post(
        "/api/v1/channels",
        json={"identifier": "https://t.me/+invite-code"},
        headers=headers,
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_channel"
    assert (
        api.post(
            "/api/v1/channels",
            json={"identifier": "@channel-a"},
            headers=headers,
        ).status_code
        == 409
    )

    searched = api.post("/api/v1/searches", json={"query": "A Show"}, headers=headers)
    assert searched.status_code == 200
    assert gateway.search_calls[-1][0] == ["channel-a", -1001234567890]

    paused = api.post(f"/api/v1/channels/{first.json()['id']}/pause", headers=headers)
    assert paused.json()["enabled"] is False
    api.post("/api/v1/searches", json={"query": "A Show"}, headers=headers)
    assert gateway.search_calls[-1][0] == [-1001234567890]

    assert (
        api.delete(f"/api/v1/channels/{numeric.json()['id']}", headers=headers).status_code == 204
    )
    no_scope = api.post("/api/v1/searches", json={"query": "A Show"}, headers=headers)
    assert no_scope.status_code == 422
    assert (
        api.post(f"/api/v1/channels/{first.json()['id']}/resume", headers=headers).status_code
        == 200
    )
    assert api.get("/api/v1/channels", headers=headers).json()[0]["identifier"] == "channel-a"


def test_telegram_user_login_flow(tmp_path: Path) -> None:
    api = client(tmp_path, with_telegram=True)
    headers = {"Authorization": "Bearer secret"}
    assert api.get("/api/v1/telegram/status", headers=headers).json() == {
        "status": "login_required",
        "authorized": False,
    }
    sent = api.post(
        "/api/v1/telegram/login/code", json={"phone": "+8613800000000"}, headers=headers
    )
    assert sent.json()["status"] == "code_sent"
    verified = api.post("/api/v1/telegram/login/verify", json={"code": "12345"}, headers=headers)
    assert verified.json() == {"status": "authorized", "authorized": True}


def test_telegram_login_is_shared_by_independent_browser_clients(tmp_path: Path) -> None:
    gateway = SearchGateway()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'shared-session.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
    )
    app = create_app(settings, telegram_gateway=gateway)
    browser_a = TestClient(app)
    browser_b = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    browser_a.post(
        "/api/v1/telegram/login/code",
        json={"phone": "+8613800000000"},
        headers=headers,
    )
    verified = browser_a.post(
        "/api/v1/telegram/login/verify",
        json={"code": "12345"},
        headers=headers,
    )

    assert verified.json() == {"status": "authorized", "authorized": True}
    assert browser_b.get("/api/v1/telegram/status", headers=headers).json() == {
        "status": "authorized",
        "authorized": True,
    }


def test_telegram_errors_have_stable_safe_api_codes(tmp_path: Path) -> None:
    class FloodWaitError(RuntimeError):
        seconds = 17

    class FailingGateway(SearchGateway):
        async def search(
            self, channels: list[str | int], query: str, limit: int = 100
        ) -> list[MediaResource]:
            raise FloodWaitError("provider detail must stay private")

        async def sign_in(self, phone: str, code: str, phone_code_hash: str) -> None:
            error_type = type("PhoneCodeInvalidError", (RuntimeError,), {})
            raise error_type("provider detail must stay private")

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'telegram-errors.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
    )
    api = TestClient(create_app(settings, telegram_gateway=FailingGateway()))
    headers = {"Authorization": "Bearer secret"}

    limited = api.post(
        "/api/v1/searches",
        json={"query": "A Show", "channels": ["channel"]},
        headers=headers,
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "17"
    assert limited.json()["detail"]["code"] == "telegram_rate_limited"
    assert "provider detail" not in limited.text

    api.post(
        "/api/v1/telegram/login/code",
        json={"phone": "+8613800000000"},
        headers=headers,
    )
    invalid = api.post(
        "/api/v1/telegram/login/verify",
        json={"code": "99999"},
        headers=headers,
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "telegram_code_invalid"


def test_unavailable_telegram_channel_has_stable_safe_api_code(tmp_path: Path) -> None:
    from telegram_media_automation.infrastructure.telegram_gateway import (
        TelegramChannelUnavailable,
    )

    class FailingGateway(SearchGateway):
        async def search(
            self, channels: list[str | int], query: str, limit: int = 100
        ) -> list[MediaResource]:
            raise TelegramChannelUnavailable("private provider detail")

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'unavailable-channel.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
    )
    api = TestClient(create_app(settings, telegram_gateway=FailingGateway()))

    response = api.post(
        "/api/v1/searches",
        json={"query": "A Show", "channels": ["channel"]},
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "telegram_channel_unavailable"
    assert "private provider detail" not in response.text


def test_telegram_status_has_a_hard_operation_timeout(tmp_path: Path) -> None:
    class HangingGateway(SearchGateway):
        async def is_authorized(self) -> bool:
            await asyncio.sleep(3600)
            return False

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'telegram-timeout.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
        telegram_operation_timeout=0.05,
    )
    api = TestClient(create_app(settings, telegram_gateway=HangingGateway()))

    started = time.monotonic()
    response = api.get(
        "/api/v1/telegram/status",
        headers={"Authorization": "Bearer secret"},
    )

    assert time.monotonic() - started < 1
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "telegram_connection_failed"


def test_edit_subscription_requires_version_and_preserves_pause_and_identity(
    tmp_path: Path,
) -> None:
    api = client(tmp_path)
    headers = {"Authorization": "Bearer secret"}
    body = {
        "title": "Edit fixture",
        "query": "Old query",
        "season": 1,
        "next_episode": 2,
        "pending_episodes": [1],
        "continue_tracking": True,
        "channels": ["first_channel"],
    }
    original = api.post("/api/v1/subscriptions", json=body, headers=headers).json()
    path = f"/api/v1/subscriptions/{original['id']}"
    paused = api.post(f"{path}/pause", headers=headers).json()
    edited_body = {
        **body,
        "query": "New query",
        "season": 2,
        "next_episode": 5,
        "pending_episodes": [3, 4],
        "channels": ["https://t.me/new_channel"],
        "expected_version": paused["version"],
    }
    assert api.put(path, json=edited_body).status_code == 401
    assert (
        api.put(path, json={**edited_body, "expected_version": 1}, headers=headers).status_code
        == 409
    )
    changed = api.put(path, json=edited_body, headers=headers)
    assert changed.status_code == 200
    value = changed.json()
    assert value["id"] == original["id"] and value["created_at"] == original["created_at"]
    assert value["version"] == paused["version"] + 1 and value["enabled"] is False
    assert value["channels"] == ["new_channel"] and value["pending_episodes"] == [3, 4]
    assert value["season"] == 2 and value["next_episode"] == 5
    assert value["scan_status"] is None
    assert api.get(path, headers=headers).json() == value

    assert api.put(path, json=edited_body, headers=headers).status_code == 409
    assert (
        api.put(
            path,
            json={**edited_body, "expected_version": value["version"], "channels": []},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        api.put(
            path,
            json={**edited_body, "expected_version": value["version"], "query": " "},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        api.put(f"/api/v1/subscriptions/{uuid4()}", json=edited_body, headers=headers).status_code
        == 404
    )
    assert api.get(path, headers=headers).json() == value

    completed = api.put(
        path,
        json={
            **edited_body,
            "expected_version": value["version"],
            "continue_tracking": False,
            "pending_episodes": [],
        },
        headers=headers,
    )
    assert completed.status_code == 200 and completed.json()["enabled"] is False


def test_create_media_specific_subscription(tmp_path: Path) -> None:
    api = client(tmp_path)
    headers = {"Authorization": "Bearer secret"}
    response = api.post(
        "/api/v1/subscriptions",
        json={
            "title": "A Show",
            "season": 1,
            "next_episode": 2,
            "pending_episodes": [1, 3, 3],
            "continue_tracking": True,
            "query": "A Show",
            "channels": ["https://t.me/show-channel"],
        },
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["next_episode"] == 2
    assert response.json()["pending_episodes"] == [1, 3]
    assert response.json()["continue_tracking"] is True
    assert response.json()["channels"] == ["show-channel"]
    assert api.get("/api/v1/subscriptions", headers=headers).json() == [response.json()]
    subscription_id = response.json()["id"]
    paused = api.post(f"/api/v1/subscriptions/{subscription_id}/pause", headers=headers)
    assert paused.status_code == 200 and paused.json()["enabled"] is False
    assert api.get("/api/v1/subscriptions", headers=headers).json()[0]["enabled"] is False
    resumed = api.post(f"/api/v1/subscriptions/{subscription_id}/resume", headers=headers)
    assert resumed.status_code == 200 and resumed.json()["enabled"] is True
    assert (
        api.delete(f"/api/v1/subscriptions/{subscription_id}", headers=headers).status_code == 204
    )
    assert api.get("/api/v1/subscriptions", headers=headers).json() == []


@pytest.mark.parametrize("scope", [None, [], ["https://t.me/custom-channel"]])
def test_subscription_optional_channel_scope(tmp_path: Path, scope: list[str] | None) -> None:
    api = client(tmp_path, with_telegram=True)
    headers = {"Authorization": "Bearer secret"}
    added = api.post("/api/v1/channels", json={"identifier": "default-channel"}, headers=headers)
    assert added.status_code == 201
    payload = {"title": "A Show", "query": "A Show"}
    if scope is not None:
        payload["channels"] = scope
    result = api.post("/api/v1/subscriptions", json=payload, headers=headers)
    assert result.status_code == 201
    expected = ["custom-channel"] if scope else []
    assert result.json()["channels"] == expected
    assert api.get("/api/v1/subscriptions", headers=headers).json()[0]["channels"] == expected


def test_default_scope_requires_enabled_defaults(tmp_path: Path) -> None:
    api = client(tmp_path, with_telegram=True)
    headers = {"Authorization": "Bearer secret"}
    payload = {"title": "A Show", "query": "A Show"}
    assert (
        api.post("/api/v1/subscriptions", json=payload, headers=headers).json()["detail"]["code"]
        == "no_default_channels"
    )
    channel = api.post(
        "/api/v1/channels", json={"identifier": "default-channel"}, headers=headers
    ).json()
    api.post(f"/api/v1/channels/{channel['id']}/pause", headers=headers)
    assert api.post("/api/v1/subscriptions", json=payload, headers=headers).status_code == 422
    payload["channels"] = ["custom-channel"]
    assert api.post("/api/v1/subscriptions", json=payload, headers=headers).status_code == 201


def test_fill_only_subscription_requires_missing_episodes(tmp_path: Path) -> None:
    api = client(tmp_path)
    response = api.post(
        "/api/v1/subscriptions",
        json={
            "title": "A Show",
            "season": 1,
            "next_episode": 8,
            "pending_episodes": [],
            "continue_tracking": False,
            "query": "A Show",
            "channels": ["show-channel"],
        },
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 422


def test_new_subscription_wakes_periodic_scan_without_busy_polling(tmp_path: Path) -> None:
    gateway = CountingFullGateway()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'subscription-schedule.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
        worker_poll_interval=0.01,
        subscription_poll_interval=300,
        minimum_free_bytes=0,
    )
    headers = {"Authorization": "Bearer secret"}
    with TestClient(create_app(settings, telegram_gateway=gateway)) as api:
        response = api.post(
            "/api/v1/subscriptions",
            json={
                "title": "A Show",
                "season": 1,
                "next_episode": 1,
                "query": "A Show",
                "channels": ["show-channel"],
            },
            headers=headers,
        )
        assert response.status_code == 201
        deadline = time.monotonic() + 1
        while gateway.scan_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        first_scan_count = gateway.scan_calls
        assert first_scan_count >= 1
        time.sleep(0.08)
        assert gateway.scan_calls == first_scan_count


def test_embedded_worker_downloads_without_second_telegram_session(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'integrated.sqlite3'}",
        data_dir=tmp_path,
        download_incomplete=tmp_path / "incomplete",
        download_inbox=tmp_path / "inbox",
        api_token="secret",
        worker_poll_interval=0.05,
    )
    headers = {"Authorization": "Bearer secret"}
    with TestClient(create_app(settings, telegram_gateway=FullGateway())) as api:
        resource = api.post(
            "/api/v1/searches",
            json={"query": "A Show", "channels": ["channel"]},
            headers=headers,
        ).json()[0]
        task = api.post(
            "/api/v1/tasks",
            json={"resource_id": resource["id"]},
            headers=headers,
        ).json()
        deadline = time.monotonic() + 2
        while task["state"] != "downloaded" and time.monotonic() < deadline:
            time.sleep(0.03)
            task = api.get(f"/api/v1/tasks/{task['id']}", headers=headers).json()

        assert task["state"] == "downloaded"
        assert Path(task["output_path"]).read_bytes() == b"x" * 42


def test_slow_subscription_scan_does_not_block_downloads(tmp_path: Path) -> None:
    import asyncio

    class SlowGateway(FullGateway):
        async def scan_channel(self, channel, query, min_message_id, limit=100):
            await asyncio.Event().wait()

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'slow.sqlite3'}",
        data_dir=tmp_path,
        download_incomplete=tmp_path / "incomplete",
        download_inbox=tmp_path / "inbox",
        api_token="secret",
        worker_poll_interval=0.01,
        telegram_operation_timeout=0.2,
    )
    headers = {"Authorization": "Bearer secret"}
    with TestClient(create_app(settings, telegram_gateway=SlowGateway())) as api:
        api.post(
            "/api/v1/subscriptions",
            json={"title": "A Show", "query": "A Show", "channels": ["channel"]},
            headers=headers,
        )
        resource = api.post(
            "/api/v1/searches", json={"query": "A Show", "channels": ["channel"]}, headers=headers
        ).json()[0]
        task = api.post(
            "/api/v1/tasks", json={"resource_id": resource["id"]}, headers=headers
        ).json()
        deadline = time.monotonic() + 2
        while task["state"] != "downloaded" and time.monotonic() < deadline:
            time.sleep(0.01)
            task = api.get(f"/api/v1/tasks/{task['id']}", headers=headers).json()
        assert task["state"] == "downloaded"
        deadline = time.monotonic() + 1
        report = None
        while time.monotonic() < deadline:
            report = api.get("/api/v1/subscriptions", headers=headers).json()[0]["scan_status"]
            if report and report["state"] == "failed":
                break
            time.sleep(0.01)
        assert report["state"] == "failed"
        readiness = api.get("/health/ready")
        assert readiness.status_code == 503
        assert readiness.json()["checks"]["workers"] is True


def test_api_task_archive_plan_completes_download_and_movie_import(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'auto-import.sqlite3'}",
        data_dir=tmp_path,
        download_incomplete=tmp_path / "incomplete",
        download_inbox=tmp_path / "inbox",
        movie_root=tmp_path / "movies",
        tv_root=tmp_path / "tv",
        backup_dir=tmp_path / "backups",
        api_token="secret",
        worker_poll_interval=0.05,
    )
    headers = {"Authorization": "Bearer secret"}
    with TestClient(create_app(settings, telegram_gateway=FullGateway())) as api:
        resource = api.post(
            "/api/v1/searches",
            json={"query": "A Movie", "channels": ["channel"]},
            headers=headers,
        ).json()[0]
        task = api.post(
            "/api/v1/tasks",
            json={
                "resource_id": resource["id"],
                "archive_plan": {
                    "kind": "movie",
                    "title": "A Movie",
                    "year": 2026,
                    "confirmed_identity": True,
                },
            },
            headers=headers,
        ).json()
        deadline = time.monotonic() + 2
        while task["state"] != "imported" and time.monotonic() < deadline:
            time.sleep(0.03)
            task = api.get(f"/api/v1/tasks/{task['id']}", headers=headers).json()

        assert task["state"] == "imported"
        assert Path(task["output_path"]).name == "A Movie (2026).mkv"
        assert Path(task["output_path"]).read_bytes() == b"x" * 42


@pytest.mark.parametrize("mode", ["direct", "scheduled", "automatic"])
def test_api_can_replace_existing_import_with_completed_download(tmp_path: Path, mode: str) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'replace-api.sqlite3'}",
        data_dir=tmp_path,
        download_incomplete=tmp_path / "incomplete",
        download_inbox=tmp_path / "inbox",
        movie_root=tmp_path / "movies",
        tv_root=tmp_path / "tv",
        backup_dir=tmp_path / "backups",
        api_token="secret",
        worker_poll_interval=0.05,
    )
    headers = {"Authorization": "Bearer secret"}
    with TestClient(create_app(settings, telegram_gateway=FullGateway())) as api:
        resource = api.post(
            "/api/v1/searches",
            json={"query": "A Movie", "channels": ["channel"]},
            headers=headers,
        ).json()[0]
        first = api.post(
            "/api/v1/tasks",
            json={
                "resource_id": resource["id"],
                "archive_plan": {
                    "kind": "movie",
                    "title": "A Movie",
                    "confirmed_identity": True,
                },
            },
            headers=headers,
        ).json()
        deadline = time.monotonic() + 2
        while first["state"] != "imported" and time.monotonic() < deadline:
            time.sleep(0.03)
            first = api.get(f"/api/v1/tasks/{first['id']}", headers=headers).json()

        target = Path(first["output_path"])
        target.write_bytes(b"old")
        plan = {
            "kind": "movie",
            "title": "A Movie",
            "confirmed_identity": True,
            "replace_existing": True,
            "target_path": str(target),
        }
        replacement = api.post(
            "/api/v1/tasks",
            json={
                "resource_id": resource["id"],
                **({"archive_plan": plan} if mode == "automatic" else {}),
            },
            headers=headers,
        ).json()
        deadline = time.monotonic() + 2
        while (
            replacement["state"] != ("imported" if mode == "automatic" else "downloaded")
            and time.monotonic() < deadline
        ):
            time.sleep(0.03)
            replacement = api.get(f"/api/v1/tasks/{replacement['id']}", headers=headers).json()

        if mode == "direct":
            response = api.post(
                f"/api/v1/tasks/{replacement['id']}/replace",
                json={"kind": "movie", "title": "A Movie", "confirmed_identity": True},
                headers=headers,
            )
        else:
            if mode == "scheduled":
                scheduled = api.post(
                    f"/api/v1/tasks/{replacement['id']}/replacement-plan",
                    json=plan,
                    headers=headers,
                )
                assert scheduled.status_code == 200
                deadline = time.monotonic() + 2
                while replacement["state"] != "imported" and time.monotonic() < deadline:
                    time.sleep(0.03)
                    replacement = api.get(
                        f"/api/v1/tasks/{replacement['id']}", headers=headers
                    ).json()
            response = api.get(f"/api/v1/tasks/{replacement['id']}", headers=headers)

        assert response.status_code == 200
        assert response.json()["state"] == "imported"
        assert target.read_bytes() == b"x" * 42
        backups = list((tmp_path / "movies" / ".tma-replaced-backups").rglob("*.mkv"))
        assert backups[0].read_bytes() == b"old"
        assert api.get("/api/v1/library/files?kind=movie").status_code == 401
        files = api.get("/api/v1/library/files?kind=movie&query=A%20Movie", headers=headers)
        assert files.json() == [{"path": str(target), "file_name": target.name, "file_size": 42}]
        assert api.get("/api/v1/library/files?kind=bad", headers=headers).status_code == 422


def test_check_during_scan_coalesces_and_next_check_scans_once(tmp_path: Path) -> None:
    import threading

    class ControlledGateway(CountingFullGateway):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        async def scan_channel(self, channel, query, min_message_id, limit=100):
            self.scan_calls += 1
            self.started.set()
            while not self.release.is_set():
                await asyncio.sleep(0.005)
            return ChannelScan([], min_message_id)

    gateway = ControlledGateway()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'coalesce.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
        worker_poll_interval=0.01,
        subscription_poll_interval=300,
    )
    headers = {"Authorization": "Bearer secret"}
    with TestClient(create_app(settings, telegram_gateway=gateway)) as api:
        subscription = api.post(
            "/api/v1/subscriptions",
            json={"title": "Fixture", "query": "Fixture", "channels": ["fixture"]},
            headers=headers,
        ).json()
        path = f"/api/v1/subscriptions/{subscription['id']}"
        try:
            assert gateway.started.wait(2)
            assert api.post(path + "/check", headers=headers).json()["status"] == "scanning"
            assert gateway.scan_calls == 1
        finally:
            gateway.release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = api.get(path, headers=headers).json()
            if value["scan_status"]["state"] == "succeeded":
                break
            time.sleep(0.01)
        assert value["scan_health"]["requested_at"] is None
        assert api.post(path + "/check", headers=headers).json()["status"] == "queued"
        deadline = time.monotonic() + 2
        while gateway.scan_calls < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gateway.scan_calls == 2
        time.sleep(0.04)
        assert gateway.scan_calls == 2


def test_restart_and_manual_check_respect_persisted_floodwait(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'gate.sqlite3'}",
        data_dir=tmp_path,
        api_token="secret",
        worker_poll_interval=0.01,
    )
    headers = {"Authorization": "Bearer secret"}
    original = create_app(settings)
    api = TestClient(original)
    subscription = api.post(
        "/api/v1/subscriptions",
        json={"title": "Fixture", "query": "Fixture", "channels": ["fixture"]},
        headers=headers,
    ).json()
    original.state.scan_repository.defer(60)
    deadline = original.state.scan_repository.retry_at()
    original.state.database.engine.dispose()
    gateway = CountingFullGateway()
    with TestClient(create_app(settings, telegram_gateway=gateway)) as restarted:
        path = f"/api/v1/subscriptions/{subscription['id']}/check"
        for _ in range(3):
            response = restarted.post(path, headers=headers)
            assert response.status_code == 202
            assert response.json()["retry_at"] == deadline.isoformat()
        time.sleep(0.08)
        assert gateway.scan_calls == 0
        assert restarted.app.state.scan_repository.retry_at() == deadline
