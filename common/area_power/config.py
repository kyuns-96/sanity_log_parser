from dataclasses import dataclass, field
import os
from urllib.parse import urlparse

from .errors import ConfigurationError


def _clean_dashboard_url(raw: str | None) -> str | None:
    value = (raw or "").strip().rstrip("/")
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    db_schema: str = field(repr=False)
    max_upload_bytes: int = 33554432
    max_uncompressed_bytes: int = 268435456
    mail_recipients: tuple[str, ...] = ()
    dashboard_url: str | None = None
    worker_poll_seconds: float = 2.0

    def __post_init__(self):
        if not self.database_url or not self.db_schema or self.db_schema == "<schema>":
            raise ConfigurationError("AREA_POWER_DB_DSN and private AREA_POWER_DB_SCHEMA must be configured internally")
        if "\x00" in self.db_schema:
            raise ConfigurationError("AREA_POWER_DB_SCHEMA is invalid")
        if self.max_upload_bytes <= 0 or self.max_uncompressed_bytes <= 0:
            raise ConfigurationError("Upload limits must be positive")

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ.get("AREA_POWER_DB_DSN", ""), db_schema=os.environ.get("AREA_POWER_DB_SCHEMA", ""),
            mail_recipients=tuple(item.strip() for item in os.environ.get("AREA_POWER_MAIL_RECIPIENTS", "").split(",") if item.strip()),
            dashboard_url=_clean_dashboard_url(os.environ.get("AREA_POWER_DASHBOARD_URL")),
        )
