"""Explicit allowlists for operational reports safe to preview and export."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any


def safe_timestamp(value: Any) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC).isoformat()
    except (TypeError, ValueError):
        return None


def count(value: Any) -> int:
    return min(max(value, 0), 2**63 - 1) if type(value) is int else 0


def build_diagnostic_report(
    *,
    version: str,
    schema_version: int,
    health: dict[str, Any],
    tasks: dict[str, Any],
    directories: dict[str, Any],
    scans: list[dict[str, Any]],
) -> dict[str, Any]:
    states = {
        "scanning",
        "succeeded",
        "partial",
        "failed",
        "rate_limited",
        "no_channels",
        "completed",
    }
    outcomes = {"waiting", "enqueued", "existing_task", "plan_changed"}
    errors = {"some_channels_failed", "all_channels_failed", "telegram_rate_limited", "no_channels"}
    telegram_states = {
        "authorized",
        "login_required",
        "not_configured",
        "telegram_timeout",
        "telegram_proxy_failed",
        "telegram_connection_failed",
        "telegram_rate_limited",
        "telegram_operation_failed",
    }
    observations = []
    for row in scans:
        report = row.get("report") or {}
        observations.append(
            {
                "reference": hashlib.sha256(str(row.get("id", "")).encode()).hexdigest()[:12],
                "enabled": bool(row.get("enabled")),
                "state": report.get("state") if report.get("state") in states else "unknown",
                "outcome": report.get("outcome") if report.get("outcome") in outcomes else None,
                "channel_count": count(report.get("channel_count")),
                "successful_channels": count(report.get("successful_channels")),
                "failed_channel_count": len(report.get("failed_channels") or []),
                "last_attempt_at": safe_timestamp(row.get("last_attempt_at")),
                "last_success_at": safe_timestamp(row.get("last_success_at")),
                "last_enqueued_at": safe_timestamp(row.get("last_enqueued_at")),
                "consecutive_failures": count(row.get("consecutive_failures")),
                "error_code": row.get("last_error_code")
                if row.get("last_error_code") in errors
                else None,
                "check_requested": bool(row.get("requested_at")),
            }
        )
    return {
        "format_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "application": {"version": version, "schema_version": schema_version},
        "health": {
            "status": health.get("status")
            if health.get("status") in {"ready", "not_ready"}
            else "unknown",
            "checks": {
                name: bool(health.get("checks", {}).get(name))
                for name in ["database", "configured", "telegram", "workers"]
            },
            "telegram_status": health.get("telegram_status")
            if health.get("telegram_status") in telegram_states
            else "unknown",
        },
        "tasks": {
            name: count(tasks.get(name)) for name in ["total", "active", "attention", "completed"]
        },
        "storage": {
            name: {
                "exists": bool(value.get("exists")),
                "writable": bool(value.get("writable")),
                "free_bytes": count(value.get("free_bytes"))
                if value.get("free_bytes") is not None
                else None,
            }
            for name, value in directories.items()
            if name in {"incomplete", "inbox", "movies", "tv", "backups"}
        },
        "subscriptions": observations,
        "privacy": {
            "titles": False,
            "queries": False,
            "channel_identifiers": False,
            "paths": False,
            "credentials": False,
            "raw_logs": False,
        },
    }
