"""Password hashing, local administrator storage and signed Web sessions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

_CREDENTIAL_VERSION = 1
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SESSION_VERSION = 1
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


@dataclass(frozen=True, slots=True)
class AdminCredentials:
    username: str
    salt: bytes
    password_hash: bytes


def validate_admin_credentials(username: str, password: str) -> tuple[str, str]:
    normalized_username = username.strip()
    if not _USERNAME_PATTERN.fullmatch(normalized_username):
        raise ValueError("username must contain 3-64 letters, numbers, dots, dashes or underscores")
    if len(password) < 10:
        raise ValueError("password must contain at least 10 characters")
    if len(password.encode("utf-8")) > 1024:
        raise ValueError("password is too long")
    return normalized_username, password


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )


def write_admin_credentials(
    path: Path,
    username: str,
    password: str,
    *,
    overwrite: bool = False,
) -> Path:
    normalized_username, validated_password = validate_admin_credentials(username, password)
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(16)
    document = {
        "version": _CREDENTIAL_VERSION,
        "username": normalized_username,
        "password": {
            "algorithm": "scrypt",
            "n": _SCRYPT_N,
            "r": _SCRYPT_R,
            "p": _SCRYPT_P,
            "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
            "hash": base64.urlsafe_b64encode(_password_hash(validated_password, salt)).decode(
                "ascii"
            ),
        },
    }
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        if overwrite:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target)
            except FileExistsError as error:
                raise FileExistsError(
                    f"administrator credentials already exist: {target}"
                ) from error
            temporary.unlink()
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_admin_credentials(path: Path) -> AdminCredentials | None:
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        password = document["password"]
        if (
            document["version"] != _CREDENTIAL_VERSION
            or password["algorithm"] != "scrypt"
            or password["n"] != _SCRYPT_N
            or password["r"] != _SCRYPT_R
            or password["p"] != _SCRYPT_P
        ):
            raise ValueError("unsupported administrator credential format")
        username = str(document["username"])
        validate_admin_credentials(username, "validation-only")
        return AdminCredentials(
            username=username,
            salt=base64.urlsafe_b64decode(password["salt"]),
            password_hash=base64.urlsafe_b64decode(password["hash"]),
        )
    except (KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("administrator credential file is invalid") from error


def verify_admin_password(path: Path, username: str, password: str) -> bool:
    credentials = load_admin_credentials(path)
    if credentials is None:
        return False
    supplied_username = username.strip()
    username_valid = hmac.compare_digest(supplied_username, credentials.username)
    supplied_hash = _password_hash(password, credentials.salt)
    return username_valid and hmac.compare_digest(supplied_hash, credentials.password_hash)


def derive_web_session_key(path: Path, api_token: str) -> str:
    credentials = load_admin_credentials(path)
    if credentials is None:
        raise ValueError("administrator credentials are not configured")
    return hmac.new(
        api_token.encode("utf-8"), credentials.password_hash, hashlib.sha256
    ).hexdigest()


def issue_web_session(username: str, signing_key: str, lifetime_seconds: int) -> str:
    payload = {
        "v": _SESSION_VERSION,
        "sub": username,
        "exp": int(time.time()) + lifetime_seconds,
        "nonce": secrets.token_urlsafe(12),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(signing_key.encode("utf-8"), encoded, hashlib.sha256).digest()
    return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).decode('ascii')}"


def verify_web_session(value: str, signing_key: str) -> str | None:
    try:
        encoded_text, signature_text = value.split(".", 1)
        encoded = encoded_text.encode("ascii")
        supplied_signature = base64.urlsafe_b64decode(signature_text)
        expected_signature = hmac.new(signing_key.encode("utf-8"), encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        padding = b"=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        if payload.get("v") != _SESSION_VERSION or int(payload.get("exp", 0)) < int(time.time()):
            return None
        username = str(payload.get("sub", ""))
        if not _USERNAME_PATTERN.fullmatch(username):
            return None
        return username
    except (binascii.Error, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
