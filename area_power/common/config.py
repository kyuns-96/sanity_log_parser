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



@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    db_schema: str = field(repr=False)
    max_upload_bytes: int = 33554432
    max_uncompressed_bytes: int = 268435456
    ai_base_url: str = ""
    ai_api_key: str = field(default="", repr=False)
    ai_model: str = ""
    ai_timeout_seconds: float = 90.0
    ai_context_max_chars: int = 48000
    mail_recipients: tuple[str, ...] = ()
    worker_poll_seconds: float = 2.0

    def __post_init__(self):
        if not self.database_url or not self.db_schema or self.db_schema == "<schema>":
            raise ConfigurationError("AREA_POWER_DB_DSN and private AREA_POWER_DB_SCHEMA must be configured internally")
        if "\x00" in self.db_schema:
            raise ConfigurationError("AREA_POWER_DB_SCHEMA is invalid")
        if self.max_upload_bytes <= 0 or self.max_uncompressed_bytes <= 0:
            raise ConfigurationError("Upload limits must be positive")
        if self.ai_context_max_chars < 4096:
            raise ConfigurationError("AREA_POWER_AI_CONTEXT_MAX_CHARS must be at least 4096")
    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ.get("AREA_POWER_DB_DSN", ""), db_schema=os.environ.get("AREA_POWER_DB_SCHEMA", ""),
            max_upload_bytes=_positive_int("AREA_POWER_MAX_UPLOAD_BYTES", 33554432),
            max_uncompressed_bytes=_positive_int("AREA_POWER_MAX_UNCOMPRESSED_BYTES", 268435456),
            ai_base_url=os.environ.get("AREA_POWER_AI_BASE_URL", ""), ai_api_key=os.environ.get("AREA_POWER_AI_API_KEY", ""),
            ai_model=os.environ.get("AREA_POWER_AI_MODEL", ""), ai_timeout_seconds=_positive_float("AREA_POWER_AI_TIMEOUT_SECONDS", 90),
            ai_context_max_chars=_positive_int("AREA_POWER_AI_CONTEXT_MAX_CHARS", 48000),
            mail_recipients=tuple(item.strip() for item in os.environ.get("AREA_POWER_MAIL_RECIPIENTS", "").split(",") if item.strip()),
            worker_poll_seconds=_positive_float("AREA_POWER_WORKER_POLL_SECONDS", 2),
        )
