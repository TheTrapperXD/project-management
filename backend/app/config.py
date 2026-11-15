from functools import lru_cache
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = Field(min_length=1)
    SECRET_KEY: str = Field(min_length=32)
    CORS_ORIGIN: str = Field(min_length=1)
    # IANA zone that defines the pharmacy's business day (expiry, dispensing, reports).
    BUSINESS_TIMEZONE: str = "Asia/Bangkok"

    @field_validator("CORS_ORIGIN")
    @classmethod
    def _valid_cors_origin(cls, v: str) -> str:
        hint = "CORS_ORIGIN must be an origin like https://example.github.io (scheme and host only)"
        if v == "*":
            raise ValueError(f"{hint}; '*' is not allowed")
        try:
            parts = urlsplit(v)
            parts.port  # raises ValueError on a malformed port
        except ValueError as exc:
            raise ValueError(f"{hint}; got an unparseable value") from exc
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"{hint}; scheme must be http or https")
        if not parts.hostname:
            raise ValueError(f"{hint}; host is missing")
        if parts.path or "?" in v or "#" in v:
            raise ValueError(f"{hint}; no path, query, fragment or trailing slash")
        return v

    @field_validator("BUSINESS_TIMEZONE")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {v!r}") from exc
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
