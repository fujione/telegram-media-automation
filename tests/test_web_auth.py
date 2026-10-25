from pathlib import Path

import pytest

from telegram_media_automation.infrastructure.web_auth import (
    derive_web_session_key,
    issue_web_session,
    load_admin_credentials,
    verify_admin_password,
    verify_web_session,
    write_admin_credentials,
)


def test_password_store_contains_only_a_salted_hash(tmp_path: Path) -> None:
    path = tmp_path / "admin.json"
    write_admin_credentials(path, "owner", "strong-password")

    contents = path.read_text(encoding="utf-8")
    assert "strong-password" not in contents
    assert verify_admin_password(path, "owner", "strong-password")
    assert not verify_admin_password(path, "owner", "wrong-password")
    assert not verify_admin_password(path, "someone", "strong-password")
    assert load_admin_credentials(path).username == "owner"  # type: ignore[union-attr]


def test_admin_credentials_validate_and_refuse_implicit_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "admin.json"
    with pytest.raises(ValueError):
        write_admin_credentials(path, "x", "strong-password")
    with pytest.raises(ValueError):
        write_admin_credentials(path, "owner", "short")

    write_admin_credentials(path, "owner", "strong-password")
    with pytest.raises(FileExistsError):
        write_admin_credentials(path, "other", "another-password")


def test_signed_web_session_rejects_tampering_and_wrong_key() -> None:
    session = issue_web_session("owner", "signing-key", 3600)
    assert verify_web_session(session, "signing-key") == "owner"
    assert verify_web_session(session, "different-key") is None
    assert verify_web_session(f"x{session}", "signing-key") is None
    assert verify_web_session("not-a-session", "signing-key") is None


def test_password_change_rotates_the_derived_session_key(tmp_path: Path) -> None:
    path = tmp_path / "admin.json"
    write_admin_credentials(path, "owner", "strong-password")
    first = derive_web_session_key(path, "api-token")

    write_admin_credentials(path, "owner", "different-password", overwrite=True)

    assert derive_web_session_key(path, "api-token") != first
