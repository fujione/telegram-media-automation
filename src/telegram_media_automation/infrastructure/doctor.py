"""Local preflight checks that never disclose configured secrets."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import make_url

from telegram_media_automation.infrastructure.config import Settings
from telegram_media_automation.infrastructure.database import CURRENT_SCHEMA_VERSION, Database
from telegram_media_automation.infrastructure.web_auth import load_admin_credentials

CheckStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def healthy(self) -> bool:
        return all(check.status != "error" for check in self.checks)


def _directory_check(name: str, path: Path, minimum_free_bytes: int = 0) -> DoctorCheck:
    resolved = path.resolve()
    if not resolved.is_dir():
        return DoctorCheck(name, "error", f"目录不存在: {resolved}")
    try:
        with tempfile.NamedTemporaryFile(prefix=".mediactl-write-test-", dir=resolved):
            pass
        free_bytes = shutil.disk_usage(resolved).free
    except OSError:
        return DoctorCheck(name, "error", f"目录不可写或无法读取磁盘状态: {resolved}")
    if not os.access(resolved, os.W_OK):
        return DoctorCheck(name, "error", f"目录不可写: {resolved}")
    if free_bytes < minimum_free_bytes:
        return DoctorCheck(
            name,
            "error",
            f"可用空间 {free_bytes} 字节, 低于保留值 {minimum_free_bytes} 字节: {resolved}",
        )
    return DoctorCheck(name, "ok", f"可写, 可用空间 {free_bytes} 字节: {resolved}")


def _database_check(database_url: str) -> DoctorCheck:
    try:
        parsed = make_url(database_url)
    except Exception:
        return DoctorCheck("database", "error", "数据库 URL 无效")
    if not parsed.drivername.startswith("sqlite"):
        return DoctorCheck("database", "error", "当前版本只支持 SQLite 数据库")
    database_name = parsed.database
    if database_name and database_name != ":memory:":
        database_path = Path(database_name).resolve()
        if not database_path.is_file():
            return DoctorCheck("database", "error", f"数据库文件不存在: {database_path}")
    try:
        database = Database(database_url)
        if not database.ping():
            return DoctorCheck("database", "error", "数据库连接失败")
        version = database.schema_version()
    except Exception:
        return DoctorCheck("database", "error", "数据库未初始化或结构无法读取")
    if version != CURRENT_SCHEMA_VERSION:
        return DoctorCheck(
            "database",
            "error",
            f"数据库 schema 为 {version}, 程序要求 {CURRENT_SCHEMA_VERSION}",
        )
    return DoctorCheck("database", "ok", f"SQLite 连接正常, schema {version}")


def run_doctor(settings: Settings) -> DoctorReport:
    checks: list[DoctorCheck] = []
    try:
        api_token = settings.resolved_api_token()
    except OSError:
        api_token = None
    checks.append(
        DoctorCheck(
            "api_token",
            "ok" if api_token else "error",
            "管理密钥已配置" if api_token else "管理密钥未配置或 secret 文件不可读",
        )
    )

    try:
        admin = load_admin_credentials(settings.admin_credentials_file)
        admin_error = None
    except (OSError, ValueError) as error:
        admin = None
        admin_error = str(error)
    checks.append(
        DoctorCheck(
            "web_admin",
            "ok" if admin else "error",
            (
                f"Web 管理员已配置: {admin.username}"
                if admin
                else f"Web 管理员不可用: {admin_error or '未创建。请运行 mediactl set-admin'}"
            ),
        )
    )

    try:
        credentials = settings.resolved_telegram_credentials()
        credential_error = None
    except (OSError, ValueError) as error:
        credentials = None
        credential_error = str(error)
    checks.append(
        DoctorCheck(
            "telegram_credentials",
            "ok" if credentials else "error",
            (
                "Telegram API ID/Hash 已配置"
                if credentials
                else f"Telegram API ID/Hash 不可用: {credential_error or '未配置'}"
            ),
        )
    )

    try:
        proxy = settings.resolved_telegram_proxy()
        checks.append(
            DoctorCheck(
                "telegram_proxy",
                "ok",
                "Telegram 代理配置有效" if proxy else "未配置代理, 将直接连接 Telegram",
            )
        )
    except (OSError, ValueError) as error:
        checks.append(DoctorCheck("telegram_proxy", "error", f"Telegram 代理无效: {error}"))

    checks.append(_database_check(settings.database_url))
    checks.extend(
        (
            _directory_check(
                "download_incomplete",
                settings.download_incomplete,
                settings.minimum_free_bytes,
            ),
            _directory_check("download_inbox", settings.download_inbox),
            _directory_check("movie_root", settings.movie_root),
            _directory_check("tv_root", settings.tv_root),
            _directory_check("backup_dir", settings.backup_dir),
        )
    )
    checks.append(
        DoctorCheck(
            "telegram_session",
            "warning",
            (
                "Telegram Session 文件已存在; 授权状态需通过 Web 或 readiness 确认: "
                f"{settings.telegram_session.resolve()}"
                if settings.telegram_session.is_file()
                else "Telegram Session 尚未创建; 首次启动后可在 Web 中完成登录"
            ),
        )
    )
    if not settings.workers_enabled:
        checks.append(DoctorCheck("workers", "warning", "内置 worker 已关闭"))
    else:
        checks.append(DoctorCheck("workers", "ok", "内置 worker 已启用"))
    return DoctorReport(tuple(checks))
