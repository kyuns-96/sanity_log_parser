"""Load only application settings from the repository-local .env; never log values."""
from pathlib import Path
import os

from sanity_common import ReviewError

ENV_FILE = Path(__file__).resolve().parent / ".env"
ENV_KEYS = ("SANITY_DB_ACCESS", "SANITY_DB_SCHEMA", "SANITY_VWP_DSN", "SANITY_OA_DSN",
            "DBAAS_BASE_URL", "DBASS_BASE_URL", "DBAAS_TOKEN", "DBAAS_CA_PATH",
            "SANITY_DASHBOARD_TOKEN", "SANITY_DASHBOARD_REVIEWER")


def load_environment(env_file=None):
    path = ENV_FILE if env_file is None else Path(env_file)
    values = {}
    try:
        if path.exists():
            from dotenv import dotenv_values
            # Explicit path only; no parent-directory discovery, interpolation or env mutation.
            parsed = dotenv_values(path, interpolate=False, encoding="utf-8")
            values = {key: parsed[key] for key in ENV_KEYS if key in parsed}
    except ImportError:
        raise ReviewError("DATABASE_DRIVER_MISSING", "Install requirements.txt to load the local environment file") from None
    except (OSError, UnicodeError, ValueError):
        raise ReviewError("DATABASE_CONFIG", "Cannot read the local environment file") from None
    # Exported values, including an explicit empty value, take precedence over .env.
    values.update({key: os.environ[key] for key in ENV_KEYS if key in os.environ})
    return values


def access_mode(settings):
    mode = settings.get("SANITY_DB_ACCESS", "dbaas_http")
    if mode not in ("dbaas_http", "postgresql"):
        raise ReviewError("DATABASE_CONFIG", "SANITY_DB_ACCESS must be dbaas_http or postgresql")
    return mode
