"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from telegram_media_automation import __version__
from telegram_media_automation.api.schemas import (
    ArchivePlanCreate,
    BackupResponse,
    DirectoryStatusResponse,
    ImportCreate,
    LibraryFileResponse,
    MediaSuggestionCreate,
    MediaSuggestionResponse,
    QualitySettingsResponse,
    ReplaceCreate,
    ResourceResponse,
    SearchChannelCreate,
    SearchChannelInspectCreate,
    SearchChannelInspectionResponse,
    SearchChannelResponse,
    SearchCreate,
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionUpdate,
    SystemStatusResponse,
    TaskAction,
    TaskCreate,
    TaskEventResponse,
    TaskPageResponse,
    TaskResponse,
    TaskSummaryResponse,
    TelegramAuthResponse,
    TelegramCodeRequest,
    TelegramCodeVerify,
    TelegramPasswordVerify,
    UiConfigResponse,
    WebAuthRequest,
    WebAuthSessionResponse,
    WebAuthSetupResponse,
    WebSetupRequest,
)
from telegram_media_automation.application.archive_service import ArchiveService
from telegram_media_automation.application.diagnostics import build_diagnostic_report
from telegram_media_automation.application.download_service import DownloadExecutor
from telegram_media_automation.application.download_worker import DownloadWorker
from telegram_media_automation.application.import_service import ImportService
from telegram_media_automation.application.maintenance_service import FileMaintenanceService
from telegram_media_automation.application.resource_service import (
    ResourceSearchService,
    SearchGateway,
)
from telegram_media_automation.application.subscription_worker import SubscriptionWorker
from telegram_media_automation.application.task_service import (
    IdempotencyConflict,
    TaskNotFound,
    TaskService,
)
from telegram_media_automation.domain.channels import (
    SearchChannel,
    normalize_channel_identifier,
    telegram_peer,
)
from telegram_media_automation.domain.media import ArchivePlan, MediaKind
from telegram_media_automation.domain.resources import QualityPolicy
from telegram_media_automation.domain.rules.media_identity import suggest_media_identity
from telegram_media_automation.domain.states import InvalidStateTransition, TaskState
from telegram_media_automation.domain.subscriptions import Subscription
from telegram_media_automation.infrastructure.backup import create_backup, sqlite_database_path
from telegram_media_automation.infrastructure.bootstrap import BootstrapStore, InvalidSetupCode
from telegram_media_automation.infrastructure.channel_repository import (
    DuplicateSearchChannel,
    SqlSearchChannelRepository,
)
from telegram_media_automation.infrastructure.config import Settings
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.logging import configure_logging
from telegram_media_automation.infrastructure.resource_repository import SqlResourceRepository
from telegram_media_automation.infrastructure.scan_repository import SqlScanRepository, timestamp
from telegram_media_automation.infrastructure.subscription_repository import (
    ConcurrentSubscriptionUpdate,
    SqlSubscriptionRepository,
)
from telegram_media_automation.infrastructure.task_repository import (
    ConcurrentTaskUpdate,
    SqlTaskRepository,
)
from telegram_media_automation.infrastructure.telegram_gateway import (
    TelegramChannelUnavailable,
    TelegramPasswordRequired,
    TelethonGateway,
)
from telegram_media_automation.infrastructure.web_auth import (
    derive_web_session_key,
    issue_web_session,
    load_admin_credentials,
    verify_admin_password,
    verify_web_session,
    write_admin_credentials,
)

_WEB_SESSION_COOKIE = "tma_session"
_WEB_CSRF_COOKIE = "tma_csrf"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_app(
    settings: Settings | None = None, *, telegram_gateway: SearchGateway | None = None
) -> FastAPI:
    configuration = settings or Settings()
    bootstrap_store = BootstrapStore(configuration) if configuration.bootstrap_enabled else None
    if bootstrap_store is not None:
        configuration = bootstrap_store.configured_settings(configuration)
        bootstrap_store.prepare()
    configure_logging(json_output=configuration.environment == "production")
    database = Database(configuration.database_url)
    database.migrate()
    repository = SqlTaskRepository(database)
    resource_repository = SqlResourceRepository(database)
    subscription_repository = SqlSubscriptionRepository(database)
    channel_repository = SqlSearchChannelRepository(database)
    scan_repository = SqlScanRepository(database)
    service = TaskService(repository)
    import_service = ImportService(
        repository,
        resource_repository,
        ArchiveService(
            configuration.download_inbox,
            configuration.movie_root,
            configuration.tv_root,
            mode=configuration.archive_mode,
        ),
    )
    maintenance_service = FileMaintenanceService(
        repository,
        configuration.download_incomplete,
        configuration.download_inbox,
        configuration.movie_root,
        configuration.tv_root,
        retention_seconds=configuration.cleanup_retention_seconds,
    )
    telegram_configuration_error: str | None = None
    if telegram_gateway is None:
        try:
            credentials = configuration.resolved_telegram_credentials()
        except ValueError as error:
            credentials = None
            telegram_configuration_error = str(error)
        if credentials is not None:
            telegram_gateway = TelethonGateway(
                configuration.telegram_session,
                credentials[0],
                credentials[1],
                proxy=configuration.resolved_telegram_proxy(),
                connect_timeout=configuration.telegram_connect_timeout,
                connection_retries=configuration.telegram_connection_retries,
            )
    search_service = (
        ResourceSearchService(telegram_gateway, resource_repository)
        if telegram_gateway is not None
        else None
    )
    configured_quality_policy = configuration.quality_policy()

    def enabled_default_channels() -> tuple[str, ...]:
        configured = channel_repository.list()
        identifiers = (
            [channel.identifier for channel in configured if channel.enabled]
            if configured
            else configuration.telegram_search_channels
        )
        return tuple(dict.fromkeys(normalize_channel_identifier(value) for value in identifiers))

    workers_supported = telegram_gateway is not None and all(
        hasattr(telegram_gateway, attribute) for attribute in ("download_source", "scan_channel")
    )
    stop_workers = asyncio.Event()
    subscription_wakeup = asyncio.Event()
    subscription_wakeup.set()

    def report_subscription_scan(subscription_id: UUID, report: dict[str, Any]) -> None:
        key = str(subscription_id)
        version = report.get("version")
        if report.get("state") == "scanning":
            app.state.subscription_scanning[key] = version
        try:
            scan_repository.record_status(subscription_id, report)
        except Exception:
            if app.state.subscription_scanning.get(key) == version:
                app.state.subscription_scanning.pop(key, None)
            raise
        if (
            report.get("state") != "scanning"
            and isinstance(version, int)
            and app.state.subscription_scanning.get(key, version + 1) <= version
        ):
            app.state.subscription_scanning.pop(key, None)

    async def worker_loop() -> None:
        gateway = telegram_gateway
        if gateway is None:
            return
        download_worker = DownloadWorker(
            repository,
            resource_repository,
            gateway,  # type: ignore[arg-type]
            DownloadExecutor(
                repository,
                configuration.download_incomplete,
                configuration.download_inbox,
                min_free_bytes=configuration.minimum_free_bytes,
                bytes_per_second=configuration.download_bytes_per_second,
                chunk_timeout=configuration.download_chunk_timeout,
            ),
            max_attempts=configuration.worker_max_attempts,
            retry_delay=configuration.worker_retry_delay,
            lease_seconds=configuration.worker_lease_seconds,
            importer=import_service,
        )
        subscription_worker = SubscriptionWorker(
            subscription_repository,
            resource_repository,
            service,
            gateway,  # type: ignore[arg-type]
            configured_quality_policy,
            default_channels=enabled_default_channels,
            scan_timeout=configuration.telegram_operation_timeout,
            report_status=report_subscription_scan,
        )

        async def scan_loop() -> None:
            next_scan = 0.0
            not_before = 0.0
            while not stop_workers.is_set():
                now = time.monotonic()
                if now >= not_before and (now >= next_scan or subscription_wakeup.is_set()):
                    retry_at = scan_repository.retry_at()
                    if retry_at is not None:
                        not_before = now + max(0, (retry_at - datetime.now(UTC)).total_seconds())
                        continue
                    scheduled = now >= next_scan
                    requested = None if scheduled else scan_repository.pending()
                    subscription_wakeup.clear()
                    retry_after = 0.0
                    try:
                        await subscription_worker.run_once(requested)
                    except Exception as error:
                        retry_after = float(getattr(error, "seconds", 0) or 0)
                        if retry_after > 0:
                            scan_repository.defer(retry_after)
                            subscription_wakeup.set()
                        logging.getLogger(__name__).warning(
                            "subscription iteration failed: error_type=%s", type(error).__name__
                        )
                    completed = time.monotonic()
                    if scheduled:
                        next_scan = completed + configuration.subscription_poll_interval
                    not_before = completed + retry_after
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop_workers.wait(), timeout=configuration.worker_poll_interval
                    )

        # A slow channel must not stall downloads or hold shutdown indefinitely.
        scan_task = asyncio.create_task(scan_loop())
        try:
            while not stop_workers.is_set():
                worked = False
                try:
                    result = await download_worker.run_once()
                    worked = result.worked
                except Exception:
                    logging.getLogger(__name__).exception("download worker iteration failed")
                if not worked:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(
                            stop_workers.wait(), timeout=configuration.worker_poll_interval
                        )
        finally:
            scan_task.cancel()
            with suppress(asyncio.CancelledError):
                await scan_task

    async def maintenance_loop() -> None:
        while not stop_workers.is_set():
            try:
                report = await asyncio.to_thread(maintenance_service.run_once)
                if report.removed:
                    logging.getLogger(__name__).info(
                        "stale managed files cleaned",
                        extra={
                            "removed_files": report.removed,
                            "reclaimed_bytes": report.reclaimed_bytes,
                        },
                    )
            except Exception:
                logging.getLogger(__name__).exception("file maintenance iteration failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_workers.wait(), timeout=configuration.cleanup_interval_seconds
                )

    @asynccontextmanager
    async def lifespan(active_app: FastAPI) -> AsyncIterator[None]:
        stop_workers.clear()
        active_app.state.worker_event_loop = asyncio.get_running_loop()
        worker_task: asyncio.Task[None] | None = None
        maintenance_task: asyncio.Task[None] | None = None
        if configuration.workers_enabled and workers_supported:
            worker_task = asyncio.create_task(worker_loop())
            active_app.state.workers_running = True
        if configuration.cleanup_enabled:
            maintenance_task = asyncio.create_task(maintenance_loop())
            active_app.state.maintenance_running = True
        try:
            yield
        finally:
            active_app.state.workers_running = False
            active_app.state.maintenance_running = False
            active_app.state.worker_event_loop = None
            stop_workers.set()
            if worker_task is not None:
                await worker_task
            if maintenance_task is not None:
                await maintenance_task
            if telegram_gateway is not None and hasattr(telegram_gateway, "disconnect"):
                await telegram_gateway.disconnect()

    bearer = HTTPBearer(auto_error=False)
    app = FastAPI(title="Telegram Media Automation", version=__version__, lifespan=lifespan)
    web_dir = Path(__file__).resolve().parents[1] / "web"
    app.mount("/static", StaticFiles(directory=web_dir), name="static")
    app.state.settings = configuration
    app.state.database = database
    app.state.repository = repository
    app.state.task_service = service
    app.state.import_service = import_service
    app.state.maintenance_service = maintenance_service
    app.state.resource_repository = resource_repository
    app.state.subscription_repository = subscription_repository
    app.state.channel_repository = channel_repository
    app.state.telegram_gateway = telegram_gateway
    app.state.telegram_configuration_error = telegram_configuration_error
    app.state.pending_telegram_login = None
    app.state.workers_running = False
    app.state.maintenance_running = False
    app.state.worker_event_loop = None
    app.state.failed_logins = {}
    app.state.subscription_scanning = {}
    app.state.scan_repository = scan_repository

    def subscription_response(subscription: Subscription) -> SubscriptionResponse:
        scan = scan_repository.status(subscription.id, subscription.version)
        if (
            scan
            and scan.get("state") == "scanning"
            and app.state.subscription_scanning.get(str(subscription.id)) != subscription.version
        ):
            scan = {**scan, "state": "interrupted"}
        return SubscriptionResponse.model_validate(subscription).model_copy(
            update={"scan_status": scan, "scan_health": scan_repository.health(subscription.id)}
        )

    @app.exception_handler(ConcurrentSubscriptionUpdate)
    async def subscription_conflict(_request: Request, _error: Exception) -> Response:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "subscription_changed",
                    "message": "订阅已被扫描或其他操作更新,请重新载入后再保存。",
                }
            },
        )

    def wake_subscription_scan(subscription_id: UUID | None = None) -> None:
        if subscription_id is not None:
            current = subscription_repository.get(subscription_id)
            if current and current.enabled:
                scan_repository.request(
                    subscription_id,
                    current.version,
                    lambda: (
                        app.state.subscription_scanning.get(str(subscription_id)) == current.version
                    ),
                )
        event_loop = app.state.worker_event_loop
        if event_loop is not None and event_loop.is_running():
            event_loop.call_soon_threadsafe(subscription_wakeup.set)
            return
        subscription_wakeup.set()

    def telegram_http_error(error: Exception) -> HTTPException:
        error_name = type(error).__name__
        if "FloodWait" in error_name:
            retry_after = max(1, int(getattr(error, "seconds", 1) or 1))
            scan_repository.defer(retry_after)
            return HTTPException(
                status_code=429,
                detail={
                    "code": "telegram_rate_limited",
                    "message": f"Telegram 要求等待 {retry_after} 秒后重试",
                },
                headers={"Retry-After": str(retry_after)},
            )
        validation_errors = {
            "PhoneCodeInvalidError": ("telegram_code_invalid", "验证码不正确"),
            "PhoneCodeExpiredError": ("telegram_code_expired", "验证码已过期。请重新发送"),
            "PasswordHashInvalidError": ("telegram_password_invalid", "两步验证密码不正确"),
            "PhoneNumberInvalidError": ("telegram_phone_invalid", "手机号格式无效"),
        }
        if error_name in validation_errors:
            code, message = validation_errors[error_name]
            return HTTPException(status_code=422, detail={"code": code, "message": message})
        if isinstance(error, TelegramChannelUnavailable):
            return HTTPException(
                status_code=404,
                detail={
                    "code": "telegram_channel_unavailable",
                    "message": "当前 Telegram 账号未加入该频道。频道也可能已不可用",
                },
            )
        if error_name in {"ProxyError", "ProxyConnectionError", "ProxyTimeoutError"}:
            return HTTPException(
                status_code=503,
                detail={
                    "code": "telegram_proxy_failed",
                    "message": "Telegram 代理连接失败。请检查代理服务和出口节点。",
                },
            )
        if isinstance(error, (ConnectionError, OSError, TimeoutError)):
            return HTTPException(
                status_code=503,
                detail={
                    "code": "telegram_connection_failed",
                    "message": "暂时无法连接 Telegram。请稍后重试",
                },
            )
        # Record only the exception class and traceback locations. Provider
        # exception messages may contain channel identifiers, phone numbers or
        # other account-specific details.
        frames: list[str] = []
        traceback = error.__traceback__
        while traceback is not None:
            frame_code = traceback.tb_frame.f_code
            frames.append(
                f"{Path(frame_code.co_filename).name}:{traceback.tb_lineno}:{frame_code.co_name}"
            )
            traceback = traceback.tb_next
        logging.getLogger("telegram").warning(
            "Telegram operation failed with %s at %s",
            error_name,
            " > ".join(frames[-6:]) or "unknown",
        )
        return HTTPException(
            status_code=502,
            detail={
                "code": "telegram_operation_failed",
                "message": "Telegram 操作失败。请检查账号状态和频道权限",
            },
        )

    @app.middleware("http")
    async def request_logging(request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", supplied_id) else str(uuid4())
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logging.getLogger("http").exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        response.headers["X-Request-ID"] = request_id
        routine_poll = (
            response.status_code < 400
            and request.method == "GET"
            and (
                request.url.path in {"/", "/health/live", "/api/v1/tasks", "/api/v1/tasks/page"}
                or request.url.path.startswith("/static/")
            )
        )
        logging.getLogger("http").log(
            logging.DEBUG if routine_poll else logging.INFO,
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response

    @app.get("/", include_in_schema=False)
    def web_index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/api/v1/ui/config", response_model=UiConfigResponse)
    def ui_config() -> UiConfigResponse:
        """Expose non-sensitive presentation settings before Web authentication."""
        return UiConfigResponse(
            language=configuration.language,
            timezone=configuration.timezone,
        )

    def configured_api_token() -> str:
        expected = configuration.resolved_api_token()
        if expected is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "setup_required", "message": "service signing key is missing"},
            )
        return expected

    def authenticate(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
    ) -> str:
        expected = configured_api_token()
        if credentials is not None:
            supplied = credentials.credentials if credentials.scheme.lower() == "bearer" else ""
            if supplied and secrets.compare_digest(supplied, expected):
                return "api-token"
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "unauthorized", "message": "A valid bearer token is required"},
            )

        session_cookie = request.cookies.get(_WEB_SESSION_COOKIE, "")
        if not session_cookie:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "unauthorized", "message": "Please sign in"},
            )
        try:
            session_key = derive_web_session_key(configuration.admin_credentials_file, expected)
        except ValueError as error:
            raise HTTPException(
                status_code=503,
                detail={"code": "setup_required", "message": str(error)},
            ) from error
        username = verify_web_session(session_cookie, session_key)
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "unauthorized", "message": "Please sign in"},
            )
        if request.method not in _SAFE_METHODS:
            supplied_csrf = request.headers.get("X-CSRF-Token", "")
            cookie_csrf = request.cookies.get(_WEB_CSRF_COOKIE, "")
            if (
                not supplied_csrf
                or not cookie_csrf
                or not secrets.compare_digest(supplied_csrf, cookie_csrf)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "csrf_failed", "message": "Web session verification failed"},
                )
        return username

    def local_setup_allowed(request: Request) -> bool:
        host = request.client.host if request.client is not None else ""
        return host in {"127.0.0.1", "::1", "testclient"}

    def set_web_session(response: Response, request: Request, username: str) -> None:
        signing_key = derive_web_session_key(
            configuration.admin_credentials_file, configured_api_token()
        )
        lifetime = configuration.web_session_lifetime_seconds
        response.set_cookie(
            _WEB_SESSION_COOKIE,
            issue_web_session(username, signing_key, lifetime),
            max_age=lifetime,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        response.set_cookie(
            _WEB_CSRF_COOKIE,
            secrets.token_urlsafe(24),
            max_age=lifetime,
            httponly=False,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )

    def enforce_login_rate_limit(request: Request) -> str:
        key = request.client.host if request.client is not None else "unknown"
        now = time.monotonic()
        attempts = [value for value in app.state.failed_logins.get(key, []) if now - value < 300]
        app.state.failed_logins[key] = attempts
        if len(attempts) >= 5:
            raise HTTPException(
                status_code=429,
                detail={"code": "login_rate_limited", "message": "登录失败次数过多。请稍后再试"},
                headers={"Retry-After": "300"},
            )
        return key

    @app.get(
        "/api/v1/auth/setup",
        response_model=WebAuthSetupResponse,
        response_model_exclude_defaults=True,
    )
    def web_auth_setup_state(request: Request) -> WebAuthSetupResponse:
        return WebAuthSetupResponse(
            setup_required=not configuration.admin_credentials_file.is_file(),
            local_setup_allowed=bootstrap_store is not None or local_setup_allowed(request),
            bootstrap_required=bootstrap_store is not None,
        )

    @app.post(
        "/api/v1/auth/setup",
        response_model=WebAuthSessionResponse,
        response_model_exclude_defaults=True,
    )
    def web_auth_setup(
        payload: WebSetupRequest, request: Request, response: Response
    ) -> WebAuthSessionResponse:
        if bootstrap_store is None and not local_setup_allowed(request):
            raise HTTPException(
                status_code=403,
                detail={"code": "local_setup_only", "message": "首次管理员创建只能在本机完成"},
            )
        if configuration.admin_credentials_file.exists():
            raise HTTPException(
                status_code=409,
                detail={"code": "setup_complete", "message": "管理员账号已经存在"},
            )
        try:
            if bootstrap_store is not None:
                key = enforce_login_rate_limit(request)
                origin = request.headers.get("origin")
                if (
                    origin and origin != f"{request.url.scheme}://{request.url.netloc}"
                ) or request.headers.get("sec-fetch-site") == "cross-site":
                    raise HTTPException(status_code=403, detail={"code": "cross_origin_setup"})
                if payload.telegram_api_id is None or payload.telegram_api_hash is None:
                    raise ValueError("Telegram API credentials are required")
                bootstrap_store.complete(
                    code=payload.setup_code,
                    username=payload.username,
                    password=payload.password,
                    telegram_api_id=payload.telegram_api_id,
                    telegram_api_hash=payload.telegram_api_hash,
                    proxy_url=payload.proxy_url,
                    language=payload.language,
                    timezone=payload.timezone,
                )
                app.state.failed_logins.pop(key, None)
            else:
                write_admin_credentials(
                    configuration.admin_credentials_file,
                    payload.username,
                    payload.password,
                )
        except InvalidSetupCode as error:
            app.state.failed_logins.setdefault(key, []).append(time.monotonic())
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "invalid_setup_code",
                    "message": "初始化码不正确。请从容器终端读取",
                },
            ) from error
        except FileExistsError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "setup_complete", "message": "管理员账号已经存在"},
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_credentials",
                    "message": "请检查管理员账号、Telegram API、代理和时区配置"
                    if bootstrap_store
                    else str(error),
                },
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "credentials_not_writable",
                    "message": "无法写入配置。请检查数据卷和运行用户的写权限"
                    if bootstrap_store
                    else "无法写入管理员凭据。请在服务主机运行 mediactl set-admin",
                },
            ) from error
        set_web_session(response, request, payload.username.strip())
        return WebAuthSessionResponse(
            authenticated=True,
            username=payload.username.strip(),
            restart_required=bootstrap_store is not None,
        )

    @app.post(
        "/api/v1/auth/login",
        response_model=WebAuthSessionResponse,
        response_model_exclude_defaults=True,
    )
    def web_auth_login(
        payload: WebAuthRequest, request: Request, response: Response
    ) -> WebAuthSessionResponse:
        key = enforce_login_rate_limit(request)
        try:
            configured = load_admin_credentials(configuration.admin_credentials_file)
        except ValueError as error:
            raise HTTPException(
                status_code=503,
                detail={"code": "credentials_invalid", "message": str(error)},
            ) from error
        if configured is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "setup_required", "message": "请先创建管理员账号"},
            )
        if not verify_admin_password(
            configuration.admin_credentials_file, payload.username, payload.password
        ):
            app.state.failed_logins[key].append(time.monotonic())
            raise HTTPException(
                status_code=401,
                detail={"code": "invalid_login", "message": "用户名或密码不正确"},
            )
        app.state.failed_logins.pop(key, None)
        set_web_session(response, request, configured.username)
        return WebAuthSessionResponse(authenticated=True, username=configured.username)

    @app.get(
        "/api/v1/auth/session",
        response_model=WebAuthSessionResponse,
        response_model_exclude_defaults=True,
    )
    def web_auth_session(
        username: str = Depends(authenticate),
    ) -> WebAuthSessionResponse:
        return WebAuthSessionResponse(authenticated=True, username=username)

    @app.post("/api/v1/auth/logout", status_code=204)
    def web_auth_logout(
        response: Response,
        _username: str = Depends(authenticate),
    ) -> None:
        response.delete_cookie(_WEB_SESSION_COOKIE, path="/")
        response.delete_cookie(_WEB_CSRF_COOKIE, path="/")

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready")
    async def health_ready(response: Response) -> dict[str, object]:
        telegram_ready = False
        telegram_status = "not_configured"
        if telegram_gateway is not None and hasattr(telegram_gateway, "is_authorized"):
            try:
                telegram_ready = bool(
                    await asyncio.wait_for(
                        telegram_gateway.is_authorized(),
                        timeout=min(5.0, configuration.telegram_operation_timeout),
                    )
                )
                telegram_status = "authorized" if telegram_ready else "login_required"
            except TimeoutError:
                telegram_status = "telegram_timeout"
            except Exception as error:
                failure = telegram_http_error(error)
                telegram_status = (
                    str(failure.detail.get("code", "telegram_operation_failed"))
                    if isinstance(failure.detail, dict)
                    else "telegram_operation_failed"
                )
        checks = {
            "database": repository.ping(),
            "configured": configuration.resolved_api_token() is not None,
            "telegram": telegram_ready,
            "workers": app.state.workers_running,
        }
        ready = all(checks.values())
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ready" if ready else "not_ready",
            "checks": checks,
            "telegram_status": telegram_status,
        }

    def directory_status(path: Path) -> DirectoryStatusResponse:
        resolved = path.resolve()
        probe = resolved
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            free_bytes = shutil.disk_usage(probe).free
        except OSError:
            free_bytes = None
        return DirectoryStatusResponse(
            path=str(resolved),
            exists=resolved.is_dir(),
            writable=os.access(probe, os.W_OK),
            free_bytes=free_bytes,
        )

    @app.get(
        "/api/v1/system/status",
        response_model=SystemStatusResponse,
        dependencies=[Depends(authenticate)],
    )
    def system_status() -> SystemStatusResponse:
        return SystemStatusResponse(
            setup_restart_required=bootstrap_store is not None
            and telegram_gateway is None
            and configuration.admin_credentials_file.exists(),
            version=__version__,
            environment=configuration.environment,
            language=configuration.language,
            timezone=configuration.timezone,
            archive_mode=configuration.archive_mode,
            schema_version=database.schema_version(),
            workers_running=bool(app.state.workers_running),
            maintenance_running=bool(app.state.maintenance_running),
            subscription_poll_interval=configuration.subscription_poll_interval,
            telegram_configured=telegram_gateway is not None,
            directories={
                "incomplete": directory_status(configuration.download_incomplete),
                "inbox": directory_status(configuration.download_inbox),
                "movies": directory_status(configuration.movie_root),
                "tv": directory_status(configuration.tv_root),
                "backups": directory_status(configuration.backup_dir),
            },
        )

    async def diagnostic_snapshot() -> dict[str, Any]:
        system = system_status()
        summary = task_summary()
        return build_diagnostic_report(
            version=__version__,
            schema_version=database.schema_version(),
            health=await health_ready(Response()),
            tasks=summary.model_dump(),
            directories={name: value.model_dump() for name, value in system.directories.items()},
            scans=scan_repository.diagnostic_rows(),
        )

    @app.get("/api/v1/diagnostics", dependencies=[Depends(authenticate)])
    async def diagnostic_preview(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await diagnostic_snapshot()

    @app.get("/api/v1/diagnostics/download", dependencies=[Depends(authenticate)])
    async def diagnostic_download() -> Response:
        report = await diagnostic_snapshot()
        filename = (
            "telegram-media-diagnostics-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json"
        )
        return Response(
            content=json.dumps(report, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    def backup_response(path: Path) -> BackupResponse:
        stat = path.stat()
        return BackupResponse(
            name=path.name,
            size=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        )

    @app.get(
        "/api/v1/backups",
        response_model=list[BackupResponse],
        dependencies=[Depends(authenticate)],
    )
    def list_backups() -> list[BackupResponse]:
        if not configuration.backup_dir.is_dir():
            return []
        return [
            backup_response(path)
            for path in sorted(
                configuration.backup_dir.glob("telegram-media-*.zip"),
                key=lambda value: value.stat().st_mtime,
                reverse=True,
            )
            if path.is_file()
        ]

    @app.post(
        "/api/v1/backups",
        response_model=BackupResponse,
        status_code=201,
        dependencies=[Depends(authenticate)],
    )
    def create_database_backup() -> BackupResponse:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        destination = configuration.backup_dir / f"telegram-media-{timestamp}-{uuid4().hex[:8]}.zip"
        result = create_backup(sqlite_database_path(configuration.database_url), destination)
        return backup_response(result.archive)

    @app.get(
        "/api/v1/backups/{backup_name}",
        dependencies=[Depends(authenticate)],
        response_class=FileResponse,
    )
    def download_backup(backup_name: str) -> FileResponse:
        if not re.fullmatch(r"telegram-media-[A-Za-z0-9-]+[.]zip", backup_name):
            raise HTTPException(status_code=404, detail={"code": "backup_not_found"})
        path = configuration.backup_dir / backup_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "backup_not_found"})
        return FileResponse(path, filename=backup_name, media_type="application/zip")

    @app.post(
        "/api/v1/tasks",
        response_model=TaskResponse,
        status_code=201,
        dependencies=[Depends(authenticate)],
    )
    def create_task(
        payload: TaskCreate,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TaskResponse:
        resource = resource_repository.get(payload.resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail={"code": "resource_not_found"})
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 200:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_idempotency_key",
                    "message": "Idempotency-Key must contain 1 to 200 characters",
                },
            )
        if payload.archive_plan is not None and not payload.archive_plan.confirmed_identity:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "archive_identity_confirmation_required",
                    "message": "automatic archive plans require explicit identity confirmation",
                },
            )
        try:
            archive_plan = (
                ArchivePlan(
                    kind=MediaKind(payload.archive_plan.kind),
                    title=payload.archive_plan.title,
                    year=payload.archive_plan.year,
                    season=payload.archive_plan.season,
                    episode=payload.archive_plan.episode,
                    episode_end=payload.archive_plan.episode_end,
                    confirmed_identity=payload.archive_plan.confirmed_identity,
                    replace_existing=payload.archive_plan.replace_existing,
                    target_path=payload.archive_plan.target_path,
                )
                if payload.archive_plan
                else None
            )
            task = service.create_task(
                payload.request_id
                or (
                    uuid5(NAMESPACE_URL, f"telegram-media-automation:{idempotency_key}")
                    if idempotency_key
                    else uuid4()
                ),
                payload.resource_id,
                resource.file_size,
                idempotency_key,
                archive_plan,
            )
            if task.state == TaskState.PENDING:
                task = service.transition(task.id, TaskState.CANDIDATE_SELECTED, actor="api")
                task = service.transition(task.id, TaskState.QUEUED, actor="api")
            return TaskResponse.model_validate(task)
        except IdempotencyConflict as error:
            raise HTTPException(
                status_code=409, detail={"code": "idempotency_conflict", "message": str(error)}
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_archive_plan", "message": str(error)},
            ) from error

    @app.post(
        "/api/v1/media/suggestions",
        response_model=MediaSuggestionResponse,
        dependencies=[Depends(authenticate)],
    )
    def suggest_media(payload: MediaSuggestionCreate) -> MediaSuggestionResponse:
        suggestion = suggest_media_identity(payload.text)
        return MediaSuggestionResponse(
            title=suggestion.title,
            year=suggestion.year,
            kind=suggestion.kind.value,
            season=suggestion.season,
            episode=suggestion.episode,
            episode_end=suggestion.episode_end,
        )

    @app.get(
        "/api/v1/settings/quality",
        response_model=QualitySettingsResponse,
        dependencies=[Depends(authenticate)],
    )
    def quality_settings() -> QualitySettingsResponse:
        policy = configured_quality_policy
        return QualitySettingsResponse(
            preferred_resolutions=policy.preferred_resolutions,
            allowed_resolutions=policy.allowed_resolutions,
            preferred_languages=policy.preferred_languages,
            allowed_languages=policy.allowed_languages,
            prefer_hdr=policy.prefer_hdr,
            max_file_size=policy.max_file_size,
        )

    @app.post(
        "/api/v1/searches",
        response_model=list[ResourceResponse],
        dependencies=[Depends(authenticate)],
    )
    async def search_resources(payload: SearchCreate) -> list[ResourceResponse]:
        if search_service is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "telegram_unavailable",
                    "message": telegram_configuration_error or "Telegram is not configured",
                },
            )
        configured_channels = channel_repository.list()
        raw_channels = payload.channels
        if not raw_channels:
            raw_channels = (
                [channel.identifier for channel in configured_channels if channel.enabled]
                if configured_channels
                else list(configuration.telegram_search_channels)
            )
        try:
            channels = [
                telegram_peer(channel)
                for channel in dict.fromkeys(
                    value.strip() for value in raw_channels if value.strip()
                )
            ]
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_channel",
                    "message": (
                        "频道格式无效。请填写公开 t.me 链接、@频道名、纯频道名或 -100 数字 ID"
                    ),
                },
            ) from error
        policy = QualityPolicy(
            preferred_resolutions=(
                tuple(payload.resolutions)
                if payload.resolutions
                else configured_quality_policy.preferred_resolutions
            ),
            allowed_resolutions=(
                tuple(payload.resolutions)
                if payload.resolutions
                else configured_quality_policy.allowed_resolutions
            ),
            preferred_languages=(
                tuple(payload.languages)
                if payload.languages
                else configured_quality_policy.preferred_languages
            ),
            allowed_languages=(
                tuple(payload.languages)
                if payload.languages
                else configured_quality_policy.allowed_languages
            ),
            min_file_size=configured_quality_policy.min_file_size,
            max_file_size=payload.max_file_size or configured_quality_policy.max_file_size,
            prefer_hdr=(
                payload.prefer_hdr
                if payload.prefer_hdr is not None
                else configured_quality_policy.prefer_hdr
            ),
            preferred_codecs=configured_quality_policy.preferred_codecs,
            allow_episode_conflicts=True,
        )
        try:
            resources = await asyncio.wait_for(
                search_service.search(channels, payload.query, payload.limit, policy),
                timeout=configuration.telegram_operation_timeout,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail={"code": "invalid_search", "message": str(error)}
            ) from error
        except Exception as error:
            raise telegram_http_error(error) from error
        return [ResourceResponse.model_validate(resource) for resource in resources]

    @app.get(
        "/api/v1/channels",
        response_model=list[SearchChannelResponse],
        dependencies=[Depends(authenticate)],
    )
    def list_search_channels() -> list[SearchChannelResponse]:
        return [
            SearchChannelResponse.model_validate(channel) for channel in channel_repository.list()
        ]

    @app.post(
        "/api/v1/channels/inspect",
        response_model=SearchChannelInspectionResponse,
        dependencies=[Depends(authenticate)],
    )
    async def inspect_search_channel(
        payload: SearchChannelInspectCreate,
    ) -> SearchChannelInspectionResponse:
        gateway = require_telegram()
        if not hasattr(gateway, "inspect_channel"):
            raise HTTPException(
                status_code=503,
                detail={"code": "channel_inspection_unavailable"},
            )
        try:
            channel = telegram_peer(payload.identifier)
            result = await asyncio.wait_for(
                gateway.inspect_channel(channel),
                timeout=configuration.telegram_operation_timeout,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_channel", "message": str(error)},
            ) from error
        except Exception as error:
            raise telegram_http_error(error) from error
        return SearchChannelInspectionResponse.model_validate(result)

    @app.get(
        "/api/v1/channels/discover",
        response_model=list[SearchChannelInspectionResponse],
        dependencies=[Depends(authenticate)],
    )
    async def discover_search_channels(limit: int = 200) -> list[SearchChannelInspectionResponse]:
        if not 1 <= limit <= 500:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_channel_discovery_limit"},
            )
        gateway = require_telegram()
        if not hasattr(gateway, "list_channels"):
            raise HTTPException(
                status_code=503,
                detail={"code": "channel_discovery_unavailable"},
            )
        try:
            channels = await asyncio.wait_for(
                gateway.list_channels(limit),
                timeout=configuration.telegram_operation_timeout,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_channel", "message": str(error)},
            ) from error
        except Exception as error:
            raise telegram_http_error(error) from error
        return [SearchChannelInspectionResponse.model_validate(channel) for channel in channels]

    @app.post(
        "/api/v1/channels",
        response_model=SearchChannelResponse,
        status_code=201,
        dependencies=[Depends(authenticate)],
    )
    def create_search_channel(payload: SearchChannelCreate) -> SearchChannelResponse:
        try:
            channel = channel_repository.create(
                SearchChannel(identifier=payload.identifier, name=payload.name)
            )
        except DuplicateSearchChannel as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "channel_exists", "message": "该搜索频道已存在"},
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_channel",
                    "message": (
                        "频道格式无效。请填写公开 t.me 链接、@频道名、纯频道名或 -100 数字 ID"
                    ),
                },
            ) from error
        return SearchChannelResponse.model_validate(channel)

    def set_search_channel_enabled(channel_id: UUID, enabled: bool) -> SearchChannelResponse:
        channel = channel_repository.set_enabled(channel_id, enabled)
        if channel is None:
            raise HTTPException(status_code=404, detail={"code": "channel_not_found"})
        return SearchChannelResponse.model_validate(channel)

    @app.post(
        "/api/v1/channels/{channel_id}/pause",
        response_model=SearchChannelResponse,
        dependencies=[Depends(authenticate)],
    )
    def pause_search_channel(channel_id: UUID) -> SearchChannelResponse:
        return set_search_channel_enabled(channel_id, False)

    @app.post(
        "/api/v1/channels/{channel_id}/resume",
        response_model=SearchChannelResponse,
        dependencies=[Depends(authenticate)],
    )
    def resume_search_channel(channel_id: UUID) -> SearchChannelResponse:
        return set_search_channel_enabled(channel_id, True)

    @app.delete(
        "/api/v1/channels/{channel_id}",
        status_code=204,
        dependencies=[Depends(authenticate)],
    )
    def delete_search_channel(channel_id: UUID) -> Response:
        if not channel_repository.delete(channel_id):
            raise HTTPException(status_code=404, detail={"code": "channel_not_found"})
        return Response(status_code=204)

    def require_telegram() -> Any:
        if telegram_gateway is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "telegram_unavailable",
                    "message": telegram_configuration_error or "Telegram is not configured",
                },
            )
        return telegram_gateway

    @app.get(
        "/api/v1/telegram/status",
        response_model=TelegramAuthResponse,
        dependencies=[Depends(authenticate)],
    )
    async def telegram_status() -> TelegramAuthResponse:
        gateway = require_telegram()
        try:
            authorized = bool(
                await asyncio.wait_for(
                    gateway.is_authorized(), timeout=configuration.telegram_operation_timeout
                )
            )
        except Exception as error:
            raise telegram_http_error(error) from error
        return TelegramAuthResponse(
            status="authorized" if authorized else "login_required", authorized=authorized
        )

    @app.post(
        "/api/v1/telegram/login/code",
        response_model=TelegramAuthResponse,
        dependencies=[Depends(authenticate)],
    )
    async def telegram_login_code(payload: TelegramCodeRequest) -> TelegramAuthResponse:
        gateway = require_telegram()
        try:
            phone_hash = await asyncio.wait_for(
                gateway.request_login_code(payload.phone),
                timeout=configuration.telegram_operation_timeout,
            )
        except Exception as error:
            raise telegram_http_error(error) from error
        app.state.pending_telegram_login = (payload.phone, phone_hash)
        return TelegramAuthResponse(status="code_sent", authorized=False)

    @app.post(
        "/api/v1/telegram/login/verify",
        response_model=TelegramAuthResponse,
        dependencies=[Depends(authenticate)],
    )
    async def telegram_login_verify(payload: TelegramCodeVerify) -> TelegramAuthResponse:
        pending = app.state.pending_telegram_login
        if pending is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "login_not_started", "message": "request a login code first"},
            )
        gateway = require_telegram()
        try:
            await asyncio.wait_for(
                gateway.sign_in(pending[0], payload.code, pending[1]),
                timeout=configuration.telegram_operation_timeout,
            )
        except TelegramPasswordRequired:
            return TelegramAuthResponse(status="password_required", authorized=False)
        except Exception as error:
            raise telegram_http_error(error) from error
        app.state.pending_telegram_login = None
        return TelegramAuthResponse(status="authorized", authorized=True)

    @app.post(
        "/api/v1/telegram/login/password",
        response_model=TelegramAuthResponse,
        dependencies=[Depends(authenticate)],
    )
    async def telegram_login_password(
        payload: TelegramPasswordVerify,
    ) -> TelegramAuthResponse:
        gateway = require_telegram()
        try:
            await asyncio.wait_for(
                gateway.sign_in_password(payload.password),
                timeout=configuration.telegram_operation_timeout,
            )
        except Exception as error:
            raise telegram_http_error(error) from error
        app.state.pending_telegram_login = None
        return TelegramAuthResponse(status="authorized", authorized=True)

    @app.get(
        "/api/v1/resources",
        response_model=list[ResourceResponse],
        dependencies=[Depends(authenticate)],
    )
    def list_resources(limit: int = 100) -> list[ResourceResponse]:
        bounded = max(1, min(limit, 500))
        return [
            ResourceResponse.model_validate(resource)
            for resource in resource_repository.list(bounded)
        ]

    @app.get(
        "/api/v1/resources/{resource_id}",
        response_model=ResourceResponse,
        dependencies=[Depends(authenticate)],
    )
    def get_resource(resource_id: UUID) -> ResourceResponse:
        resource = resource_repository.get(resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail={"code": "resource_not_found"})
        return ResourceResponse.model_validate(resource)

    @app.post(
        "/api/v1/subscriptions",
        response_model=SubscriptionResponse,
        status_code=201,
        dependencies=[Depends(authenticate)],
    )
    def create_subscription(payload: SubscriptionCreate) -> SubscriptionResponse:
        if not payload.channels and not enabled_default_channels():
            raise HTTPException(
                status_code=422,
                detail={"code": "no_default_channels", "message": "请先添加并启用至少一个默认频道"},
            )
        try:
            subscription = Subscription(
                title=payload.title,
                season=payload.season,
                next_episode=payload.next_episode,
                query=payload.query,
                channels=tuple(normalize_channel_identifier(value) for value in payload.channels),
                pending_episodes=tuple(payload.pending_episodes),
                continue_tracking=payload.continue_tracking,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_subscription",
                    "message": "订阅配置无效: 请检查频道、集数与追更方式",
                },
            ) from error
        created = subscription_repository.create(subscription)
        wake_subscription_scan(created.id)
        return subscription_response(created)

    @app.get(
        "/api/v1/subscriptions",
        response_model=list[SubscriptionResponse],
        dependencies=[Depends(authenticate)],
    )
    def list_subscriptions() -> list[SubscriptionResponse]:
        return [
            subscription_response(subscription) for subscription in subscription_repository.list()
        ]

    @app.get(
        "/api/v1/subscriptions/{subscription_id}",
        response_model=SubscriptionResponse,
        dependencies=[Depends(authenticate)],
    )
    def get_subscription(subscription_id: UUID) -> SubscriptionResponse:
        value = subscription_repository.get(subscription_id)
        if value is None:
            raise HTTPException(status_code=404, detail={"code": "subscription_not_found"})
        return subscription_response(value)

    @app.put(
        "/api/v1/subscriptions/{subscription_id}",
        response_model=SubscriptionResponse,
        dependencies=[Depends(authenticate)],
    )
    def edit_subscription(
        subscription_id: UUID, payload: SubscriptionUpdate
    ) -> SubscriptionResponse:
        current = subscription_repository.get(subscription_id)
        if current is None:
            raise HTTPException(status_code=404, detail={"code": "subscription_not_found"})
        if current.version != payload.expected_version:
            raise ConcurrentSubscriptionUpdate(str(subscription_id))
        if not payload.channels and not enabled_default_channels():
            raise HTTPException(
                status_code=422,
                detail={"code": "no_default_channels", "message": "请先添加并启用至少一个默认频道"},
            )
        try:
            replacement = Subscription(
                **payload.model_dump(exclude={"expected_version", "channels"}),
                channels=tuple(normalize_channel_identifier(value) for value in payload.channels),
                id=current.id,
                enabled=current.enabled,
                created_at=current.created_at,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail={"code": "invalid_subscription", "message": str(error)}
            ) from error
        value = subscription_repository.edit(replacement, payload.expected_version)
        wake_subscription_scan(subscription_id)
        return subscription_response(value)

    def set_subscription_enabled(subscription_id: UUID, enabled: bool) -> SubscriptionResponse:
        subscription = subscription_repository.get(subscription_id)
        if subscription is None:
            raise HTTPException(status_code=404, detail={"code": "subscription_not_found"})
        expected_version = subscription.version
        try:
            subscription.set_enabled(enabled)
        except ValueError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "subscription_plan_complete", "message": str(error)},
            ) from error
        if subscription.version != expected_version:
            subscription_repository.save(subscription, expected_version)
        return subscription_response(subscription)

    @app.post(
        "/api/v1/subscriptions/{subscription_id}/pause",
        response_model=SubscriptionResponse,
        dependencies=[Depends(authenticate)],
    )
    def pause_subscription(subscription_id: UUID) -> SubscriptionResponse:
        return set_subscription_enabled(subscription_id, False)

    @app.post(
        "/api/v1/subscriptions/{subscription_id}/resume",
        response_model=SubscriptionResponse,
        dependencies=[Depends(authenticate)],
    )
    def resume_subscription(subscription_id: UUID) -> SubscriptionResponse:
        response = set_subscription_enabled(subscription_id, True)
        wake_subscription_scan(subscription_id)
        return response

    @app.delete(
        "/api/v1/subscriptions/{subscription_id}",
        status_code=204,
        dependencies=[Depends(authenticate)],
    )
    def delete_subscription(subscription_id: UUID) -> Response:
        if not subscription_repository.delete(subscription_id):
            raise HTTPException(status_code=404, detail={"code": "subscription_not_found"})
        app.state.subscription_scanning.pop(str(subscription_id), None)
        return Response(status_code=204)

    @app.post(
        "/api/v1/subscriptions/{subscription_id}/check",
        status_code=202,
        dependencies=[Depends(authenticate)],
    )
    def check_subscription(subscription_id: UUID) -> dict[str, Any]:
        current = subscription_repository.get(subscription_id)
        if current is None:
            raise HTTPException(status_code=404, detail={"code": "subscription_not_found"})
        if not current.enabled:
            raise HTTPException(
                status_code=409,
                detail={"code": "subscription_paused", "message": "请先恢复订阅再检查"},
            )
        result = scan_repository.request(
            subscription_id,
            current.version,
            lambda: app.state.subscription_scanning.get(str(subscription_id)) == current.version,
        )
        if result == "unavailable":
            raise ConcurrentSubscriptionUpdate(str(subscription_id))
        if result != "scanning":
            wake_subscription_scan()
        retry_at = scan_repository.retry_at()
        return {
            "subscription_id": str(subscription_id),
            "status": result,
            "retry_at": timestamp(retry_at),
        }

    @app.get("/api/v1/subscriptions/{subscription_id}/scans", dependencies=[Depends(authenticate)])
    def subscription_scans(subscription_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
        if subscription_repository.get(subscription_id) is None:
            raise HTTPException(status_code=404, detail={"code": "subscription_not_found"})
        if not 1 <= limit <= scan_repository.HISTORY_PER_SUBSCRIPTION:
            raise HTTPException(status_code=422, detail={"code": "invalid_history_limit"})
        return scan_repository.history(subscription_id, limit)

    @app.post(
        "/api/v1/tasks/{task_id}/import",
        response_model=TaskResponse,
        dependencies=[Depends(authenticate)],
    )
    def import_task(task_id: UUID, payload: ImportCreate) -> TaskResponse:
        try:
            task = import_service.import_task(
                task_id,
                kind=MediaKind(payload.kind),
                title=payload.title,
                year=payload.year,
                season=payload.season,
                episode=payload.episode,
                episode_end=payload.episode_end,
                confirmed_identity=payload.confirmed_identity,
            )
            return TaskResponse.model_validate(task)
        except TaskNotFound as error:
            raise HTTPException(status_code=404, detail={"code": "task_not_found"}) from error
        except (ValueError, LookupError, OSError) as error:
            raise HTTPException(
                status_code=409, detail={"code": "import_rejected", "message": str(error)}
            ) from error

    @app.post(
        "/api/v1/tasks/{task_id}/replace",
        response_model=TaskResponse,
        dependencies=[Depends(authenticate)],
    )
    def replace_task(task_id: UUID, payload: ReplaceCreate) -> TaskResponse:
        try:
            task = import_service.replace_task(
                task_id,
                kind=MediaKind(payload.kind),
                title=payload.title,
                year=payload.year,
                season=payload.season,
                episode=payload.episode,
                episode_end=payload.episode_end,
                confirmed_identity=payload.confirmed_identity,
                target_path=Path(payload.target_path) if payload.target_path else None,
            )
            return TaskResponse.model_validate(task)
        except TaskNotFound as error:
            raise HTTPException(status_code=404, detail={"code": "task_not_found"}) from error
        except (ValueError, LookupError, OSError) as error:
            raise HTTPException(
                status_code=409, detail={"code": "replace_rejected", "message": str(error)}
            ) from error

    @app.post(
        "/api/v1/tasks/{task_id}/replacement-plan",
        response_model=TaskResponse,
        dependencies=[Depends(authenticate)],
    )
    def set_replacement_plan(task_id: UUID, payload: ArchivePlanCreate) -> TaskResponse:
        try:
            plan = ArchivePlan(
                kind=MediaKind(payload.kind),
                title=payload.title,
                year=payload.year,
                season=payload.season,
                episode=payload.episode,
                episode_end=payload.episode_end,
                confirmed_identity=payload.confirmed_identity,
                replace_existing=payload.replace_existing,
                target_path=payload.target_path,
            )
            return TaskResponse.model_validate(service.set_replacement_plan(task_id, plan))
        except TaskNotFound as error:
            raise HTTPException(status_code=404, detail={"code": "task_not_found"}) from error
        except ConcurrentTaskUpdate as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "task_changed", "message": "retry with latest task state"},
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=409, detail={"code": "replacement_plan_rejected", "message": str(error)}
            ) from error

    @app.get(
        "/api/v1/library/files",
        response_model=list[LibraryFileResponse],
        dependencies=[Depends(authenticate)],
    )
    def list_library_files(
        kind: str = "series", query: str = "", limit: int = 100
    ) -> list[LibraryFileResponse]:
        if kind not in {"movie", "series"} or not 1 <= limit <= 500 or len(query) > 300:
            raise HTTPException(status_code=422, detail={"code": "invalid_library_query"})
        return [
            LibraryFileResponse(path=str(path), file_name=path.name, file_size=path.stat().st_size)
            for path in import_service.archive.list_files(MediaKind(kind), query, limit)
        ]

    @app.get(
        "/api/v1/tasks", response_model=list[TaskResponse], dependencies=[Depends(authenticate)]
    )
    def list_tasks(limit: int = 100) -> list[TaskResponse]:
        bounded = max(1, min(limit, 500))
        return [TaskResponse.model_validate(task) for task in repository.list(bounded)]

    @app.get(
        "/api/v1/tasks/page",
        response_model=TaskPageResponse,
        dependencies=[Depends(authenticate)],
    )
    def page_tasks(offset: int = 0, limit: int = 20, states: str | None = None) -> TaskPageResponse:
        if offset < 0:
            raise HTTPException(status_code=422, detail={"code": "invalid_task_offset"})
        bounded_limit = max(1, min(limit, 100))
        try:
            parsed_states = tuple(
                dict.fromkeys(
                    TaskState(value.strip()) for value in (states or "").split(",") if value.strip()
                )
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"code": "invalid_task_state"}) from error
        tasks, total = repository.page(
            limit=bounded_limit,
            offset=offset,
            states=parsed_states,
        )
        return TaskPageResponse(
            items=[TaskResponse.model_validate(task) for task in tasks],
            total=total,
            offset=offset,
            limit=bounded_limit,
        )

    @app.get(
        "/api/v1/tasks/summary",
        response_model=TaskSummaryResponse,
        dependencies=[Depends(authenticate)],
    )
    def task_summary() -> TaskSummaryResponse:
        counts = repository.counts_by_state()
        attention_states = {TaskState.NEEDS_SELECTION, TaskState.FAILED}
        completed_states = {
            TaskState.IMPORTED,
            TaskState.PROVISIONAL,
            TaskState.CANCELLED,
        }
        total = sum(counts.values())
        attention = sum(counts.get(state, 0) for state in attention_states)
        completed = sum(counts.get(state, 0) for state in completed_states)
        return TaskSummaryResponse(
            total=total,
            active=total - attention - completed,
            attention=attention,
            completed=completed,
        )

    @app.get(
        "/api/v1/tasks/{task_id}", response_model=TaskResponse, dependencies=[Depends(authenticate)]
    )
    def get_task(task_id: UUID) -> TaskResponse:
        task = repository.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail={"code": "task_not_found"})
        return TaskResponse.model_validate(task)

    @app.get(
        "/api/v1/tasks/{task_id}/events",
        response_model=list[TaskEventResponse],
        dependencies=[Depends(authenticate)],
    )
    def task_events(task_id: UUID) -> list[TaskEventResponse]:
        if repository.get(task_id) is None:
            raise HTTPException(status_code=404, detail={"code": "task_not_found"})
        return [
            TaskEventResponse.model_validate(event, from_attributes=True)
            for event in repository.events(task_id)
        ]

    def perform_action(task_id: UUID, target: TaskState, action: TaskAction) -> TaskResponse:
        try:
            return TaskResponse.model_validate(
                service.transition(task_id, target, reason=action.reason), from_attributes=True
            )
        except TaskNotFound as error:
            raise HTTPException(status_code=404, detail={"code": "task_not_found"}) from error
        except InvalidStateTransition as error:
            raise HTTPException(
                status_code=409, detail={"code": "invalid_state_transition", "message": str(error)}
            ) from error

    @app.post(
        "/api/v1/tasks/{task_id}/pause",
        response_model=TaskResponse,
        dependencies=[Depends(authenticate)],
    )
    def pause_task(task_id: UUID, action: TaskAction) -> TaskResponse:
        return perform_action(task_id, TaskState.PAUSED, action)

    @app.post(
        "/api/v1/tasks/{task_id}/resume",
        response_model=TaskResponse,
        dependencies=[Depends(authenticate)],
    )
    def resume_task(task_id: UUID, action: TaskAction) -> TaskResponse:
        return perform_action(task_id, TaskState.QUEUED, action)

    @app.post(
        "/api/v1/tasks/{task_id}/cancel",
        response_model=TaskResponse,
        dependencies=[Depends(authenticate)],
    )
    def cancel_task(task_id: UUID, action: TaskAction) -> TaskResponse:
        return perform_action(task_id, TaskState.CANCELLED, action)

    @app.post(
        "/api/v1/tasks/{task_id}/retry",
        response_model=TaskResponse,
        dependencies=[Depends(authenticate)],
    )
    def retry_task(task_id: UUID, action: TaskAction) -> TaskResponse:
        return perform_action(task_id, TaskState.RETRYING, action)

    return app
