import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .config import get_settings

MIN_PASSWORD_LENGTH = 12
TOKEN_TTL = timedelta(hours=8)
COOKIE_NAME = "access_token"
ALGORITHM = "HS256"

_hasher = PasswordHasher()
# Verified against when the username is unknown, so timing does not reveal existence.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))


def hash_password(plain: str) -> str:
    if len(plain) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    """Always performs one argon2 verification, even for unknown users (hashed=None)."""
    try:
        return _hasher.verify(hashed or _DUMMY_HASH, plain) and hashed is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, get_settings().SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, get_settings().SECRET_KEY, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
        return int(payload["sub"])
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


class LoginRateLimiter:
    """In-memory: max failed attempts per (ip, username) within a sliding window."""

    def __init__(self, max_failures: int = 5, window_seconds: float = 15 * 60) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: tuple[str, str], now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def is_blocked(self, ip: str, username: str) -> bool:
        with self._lock:
            return len(self._prune((ip, username.lower()), time.monotonic())) >= self.max_failures

    def record_failure(self, ip: str, username: str) -> None:
        with self._lock:
            key = (ip, username.lower())
            now = time.monotonic()
            self._prune(key, now)
            self._failures.setdefault(key, []).append(now)

    def reset(self, ip: str, username: str) -> None:
        with self._lock:
            self._failures.pop((ip, username.lower()), None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginRateLimiter()
