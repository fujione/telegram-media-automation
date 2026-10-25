import json

from telegram_media_automation.application.diagnostics import build_diagnostic_report


def test_diagnostic_export_is_allowlisted_and_preserves_real_storage_counts() -> None:
    secret = "socks5://private-user:private-password@192.0.2.12:1234"
    report = build_diagnostic_report(
        version="test",
        schema_version=14,
        health={
            "status": "not_ready",
            "telegram_status": "telegram_proxy_failed",
            "checks": {"database": True},
            "raw_exception": secret,
        },
        tasks={"total": 32, "active": 0, "private_token": secret},
        directories={
            "movies": {
                "exists": True,
                "writable": True,
                "free_bytes": 2000000000000,
                "path": secret,
            },
            secret: {"path": secret},
        },
        scans=[
            {
                "id": "private-subscription-id",
                "title": secret,
                "query": secret,
                "last_error_code": secret,
                "last_attempt_at": secret,
                "report": {
                    "state": "failed",
                    "message": secret,
                    "outcome": secret,
                    "failed_channels": [secret],
                    "task_id": secret,
                },
            }
        ],
    )
    encoded = json.dumps(report)
    for value in [secret, "private-user", "private-password", "private-subscription-id"]:
        assert value not in encoded
    assert report["storage"]["movies"]["free_bytes"] == 2000000000000
    assert report["subscriptions"][0]["failed_channel_count"] == 1
    assert report["subscriptions"][0]["last_attempt_at"] is None
    assert report["health"]["telegram_status"] == "telegram_proxy_failed"
