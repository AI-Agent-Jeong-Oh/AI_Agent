"""Local-only application login credentials.

This is an application access gate, not file encryption or an online identity. Passwords
never leave the process and only salted PBKDF2 digests are stored in the user-data folder.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_LEGACY_VERSION = 1
_VERSION = 2
_ALGORITHM = "pbkdf2-sha256"
_ITERATIONS = 310_000
_SALT_BYTES = 16
_DIGEST_BYTES = 32
_MAX_USERNAME_LENGTH = 80
_MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_LENGTH = 1024
_MAX_ACCOUNTS = 50


@dataclass(frozen=True, slots=True)
class LocalAccount:
    username: str
    salt: bytes
    password_hash: bytes

    def __post_init__(self) -> None:
        _validate_username(self.username)
        if len(self.salt) != _SALT_BYTES:
            raise ValueError("invalid credential salt")
        if len(self.password_hash) != _DIGEST_BYTES:
            raise ValueError("invalid credential digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "salt": base64.b64encode(self.salt).decode("ascii"),
            "password_hash": base64.b64encode(self.password_hash).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LocalAccount":
        allowed = {"username", "salt", "password_hash"}
        if not isinstance(data, dict) or set(data) != allowed:
            raise ValueError("credentials contain missing or unknown fields")
        if not isinstance(data["username"], str):
            raise ValueError("invalid credential username")
        try:
            salt = base64.b64decode(data["salt"], validate=True)
            digest = base64.b64decode(data["password_hash"], validate=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid credential encoding") from exc
        return cls(username=data["username"], salt=salt, password_hash=digest)


class LocalAuthStore:
    """Local operator accounts stored separately from cases and model settings."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def account_exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> LocalAccount:
        """Return the first account for compatibility with the former single-user API."""
        accounts = self.list_accounts()
        if not accounts:
            raise ValueError("등록된 로컬 사용자가 없습니다")
        return accounts[0]

    def list_accounts(self) -> tuple[LocalAccount, ...]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("로그인 정보 파일을 읽을 수 없습니다") from exc
        return self._accounts_from_data(data)

    def list_usernames(self) -> tuple[str, ...]:
        return tuple(account.username for account in self.list_accounts())

    def create(self, username: str, password: str) -> LocalAccount:
        normalized = _validate_username(username)
        _validate_password(password)
        accounts = list(self.list_accounts())
        if any(item.username.casefold() == normalized.casefold() for item in accounts):
            raise ValueError("이미 등록된 사용자 이름입니다")
        if len(accounts) >= _MAX_ACCOUNTS:
            raise ValueError(f"로컬 사용자는 최대 {_MAX_ACCOUNTS}명까지 등록할 수 있습니다")
        salt = secrets.token_bytes(_SALT_BYTES)
        account = LocalAccount(
            username=normalized,
            salt=salt,
            password_hash=_derive(password, salt),
        )
        accounts.append(account)
        self._write(accounts)
        return account

    def authenticated_account(self, username: str, password: str) -> LocalAccount | None:
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        if len(username) > _MAX_USERNAME_LENGTH or len(password) > _MAX_PASSWORD_LENGTH:
            return None
        normalized = username.strip().casefold()
        for account in self.list_accounts():
            if account.username.casefold() != normalized:
                continue
            candidate = _derive(password, account.salt)
            if hmac.compare_digest(candidate, account.password_hash):
                return account
            return None
        return None

    def authenticate(self, username: str, password: str) -> bool:
        return self.authenticated_account(username, password) is not None

    @staticmethod
    def _accounts_from_data(data: Any) -> tuple[LocalAccount, ...]:
        if not isinstance(data, dict):
            raise ValueError("invalid credential document")
        if data.get("version") == _LEGACY_VERSION:
            allowed = {
                "version",
                "algorithm",
                "iterations",
                "username",
                "salt",
                "password_hash",
            }
            if set(data) != allowed:
                raise ValueError("credentials contain missing or unknown fields")
            if data["algorithm"] != _ALGORITHM or data["iterations"] != _ITERATIONS:
                raise ValueError("unsupported credential algorithm")
            account_data = {
                "username": data["username"],
                "salt": data["salt"],
                "password_hash": data["password_hash"],
            }
            return (LocalAccount.from_dict(account_data),)

        allowed = {"version", "algorithm", "iterations", "accounts"}
        if set(data) != allowed or data.get("version") != _VERSION:
            raise ValueError("unsupported credential version")
        if data["algorithm"] != _ALGORITHM or data["iterations"] != _ITERATIONS:
            raise ValueError("unsupported credential algorithm")
        if not isinstance(data["accounts"], list) or not data["accounts"]:
            raise ValueError("credentials do not contain an account")
        if len(data["accounts"]) > _MAX_ACCOUNTS:
            raise ValueError("too many local accounts")
        accounts = tuple(LocalAccount.from_dict(item) for item in data["accounts"])
        folded_names = [account.username.casefold() for account in accounts]
        if len(folded_names) != len(set(folded_names)):
            raise ValueError("duplicate local account names")
        return accounts

    def _write(self, accounts: list[LocalAccount]) -> None:
        data = {
            "version": _VERSION,
            "algorithm": _ALGORITHM,
            "iterations": _ITERATIONS,
            "accounts": [account.to_dict() for account in accounts],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            temporary.chmod(0o600)
            temporary.replace(self.path)
            self.path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)


def _validate_username(username: str) -> str:
    if not isinstance(username, str):
        raise ValueError("사용자 이름을 입력해 주세요")
    normalized = username.strip()
    if not normalized:
        raise ValueError("사용자 이름을 입력해 주세요")
    if len(normalized) > _MAX_USERNAME_LENGTH:
        raise ValueError(f"사용자 이름은 {_MAX_USERNAME_LENGTH}자 이하로 입력해 주세요")
    if any(character in normalized for character in "\r\n\t"):
        raise ValueError("사용자 이름에는 줄바꿈이나 탭을 사용할 수 없습니다")
    return normalized


def _validate_password(password: str) -> None:
    if not isinstance(password, str):
        raise ValueError("비밀번호를 입력해 주세요")
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise ValueError(f"비밀번호는 {_MIN_PASSWORD_LENGTH}자 이상 입력해 주세요")
    if len(password) > _MAX_PASSWORD_LENGTH:
        raise ValueError("비밀번호가 너무 깁니다")


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS, dklen=_DIGEST_BYTES
    )
