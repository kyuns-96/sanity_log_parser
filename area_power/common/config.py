from dataclasses import dataclass, field
import os

from .errors import ConfigurationError


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _positive_float(name: str, default: float) -> float:
    from math import isfinite
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        raise ConfigurationError(f"{name} must be positive and finite") from None
    if not isfinite(value) or value <= 0:
        raise ConfigurationError(f"{name} must be positive and finite")
    return value


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ConfigurationError(f"{name} must be true or false")
    return value in {"true", "1"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    db_schema: str = field(repr=False)
    upload_token: str = field(default="", repr=False)
    max_upload_bytes: int = 33554432
    max_uncompressed_bytes: int = 268435456
    ai_base_url: str = ""
    ai_api_key: str = field(default="", repr=False)
    ai_model: str = ""
    ai_timeout_seconds: float = 90.0
    smtp_host: str = ""
    smtp_port: int = 587
    ai_context_max_chars: int = 48000
    smtp_from: str = ""
    smtp_username: str = field(default="", repr=False)
    smtp_password: str = field(default="", repr=False)
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_recipients: tuple[str, ...] = ()
    worker_poll_seconds: float = 2.0

    def __post_init__(self):
        if not self.database_url or not self.db_schema or self.db_schema == "<schema>":
            raise ConfigurationError("AREA_POWER_DB_DSN and private AREA_POWER_DB_SCHEMA must be configured internally")
        if "\x00" in self.db_schema:
            raise ConfigurationError("AREA_POWER_DB_SCHEMA is invalid")
        if self.max_upload_bytes <= 0 or self.max_uncompressed_bytes <= 0:
            raise ConfigurationError("Upload limits must be positive")
        if not 1 <= self.smtp_port <= 65535:
            raise ConfigurationError("AREA_POWER_SMTP_PORT must be between 1 and 65535")
        if self.smtp_ssl and self.smtp_starttls:
            raise ConfigurationError("Choose AREA_POWER_SMTP_SSL or AREA_POWER_SMTP_STARTTLS, not both")

        if self.ai_context_max_chars < 4096:
            raise ConfigurationError("AREA_POWER_AI_CONTEXT_MAX_CHARS must be at least 4096")
    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ.get("AREA_POWER_DB_DSN", ""), db_schema=os.environ.get("AREA_POWER_DB_SCHEMA", ""),
            upload_token=os.environ.get("AREA_POWER_UPLOAD_TOKEN", ""),
            max_upload_bytes=_positive_int("AREA_POWER_MAX_UPLOAD_BYTES", 33554432),
            max_uncompressed_bytes=_positive_int("AREA_POWER_MAX_UNCOMPRESSED_BYTES", 268435456),
            ai_base_url=os.environ.get("AREA_POWER_AI_BASE_URL", ""), ai_api_key=os.environ.get("AREA_POWER_AI_API_KEY", ""),
            ai_model=os.environ.get("AREA_POWER_AI_MODEL", ""), ai_timeout_seconds=_positive_float("AREA_POWER_AI_TIMEOUT_SECONDS", 90),
            smtp_host=os.environ.get("AREA_POWER_SMTP_HOST", ""), smtp_port=_positive_int("AREA_POWER_SMTP_PORT", 587),
            smtp_from=os.environ.get("AREA_POWER_SMTP_FROM", ""), smtp_username=os.environ.get("AREA_POWER_SMTP_USERNAME", ""),
            smtp_password=os.environ.get("AREA_POWER_SMTP_PASSWORD", ""), smtp_starttls=_bool("AREA_POWER_SMTP_STARTTLS", True),
            smtp_ssl=_bool("AREA_POWER_SMTP_SSL", False),
            ai_context_max_chars=_positive_int("AREA_POWER_AI_CONTEXT_MAX_CHARS", 48000),
            smtp_recipients=tuple(item.strip() for item in os.environ.get("AREA_POWER_SMTP_RECIPIENTS", "").split(",") if item.strip()),
            worker_poll_seconds=_positive_float("AREA_POWER_WORKER_POLL_SECONDS", 2),
        )
