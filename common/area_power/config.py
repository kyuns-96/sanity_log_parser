from dataclasses import dataclass, field
import os

from .errors import ConfigurationError


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    db_schema: str = field(repr=False)
    max_upload_bytes: int = 33554432
    max_uncompressed_bytes: int = 268435456
    mail_recipients: tuple[str, ...] = ()
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
        )
