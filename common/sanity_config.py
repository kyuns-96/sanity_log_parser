"""Load only application settings from the repository-local .env; never log values."""
from pathlib import Path
import os

from common.sanity_common import ReviewError

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
ENV_KEYS = ("SANITY_DB_ACCESS", "SANITY_DB_SCHEMA", "SANITY_DB_ALLOW_SINGLE_ACCOUNT", "SANITY_VWP_DSN", "SANITY_OA_DSN",
            "SANITY_DB_HOST", "SANITY_DB_PORT", "SANITY_DB_NAME", "SANITY_DB_USER_NAME", "SANITY_DB_PASSWORD",
            "SANITY_DB_SSLMODE", "SANITY_DB_SSLROOTCERT",
            "DBAAS_BASE_URL", "DBASS_BASE_URL", "DBAAS_TOKEN", "DBAAS_CA_PATH",
            "SANITY_AUTH_MODE", "SANITY_ADMIN_USER_ID", "SANITY_DASHBOARD_TOKEN", "SANITY_DASHBOARD_REVIEWER",
            "SANITY_AI_MODE", "SANITY_AI_URL", "SANITY_AI_TOKEN", "SANITY_AI_MODEL",
            "SANITY_AI_API", "SANITY_AI_JSON_MODE", "SANITY_AI_TIMEOUT", "SANITY_AI_CA_PATH", "SANITY_AI_ARGV",
            "SANITY_OA_AI_ENABLED", "SANITY_OA_AI_URL", "SANITY_OA_AI_TOKEN", "SANITY_OA_AI_MODEL",
            "SANITY_OA_AI_API", "SANITY_OA_AI_JSON_MODE", "SANITY_OA_AI_TIMEOUT", "SANITY_OA_AI_CA_PATH",
            "SANITY_OA_AI_MAX_INPUT_BYTES", "SANITY_OA_AI_MAX_OUTPUT_BYTES", "SANITY_OA_AI_CRITERIA_FILE")


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
    mode = settings.get("SANITY_DB_ACCESS", "postgresql")
    if mode not in ("dbaas_http", "postgresql"):
        raise ReviewError("DATABASE_CONFIG", "SANITY_DB_ACCESS must be dbaas_http or postgresql")
    return mode


def database_privilege_mode(settings):
    """Only an explicit true selects the shared-login permission contract."""
    value = settings.get("SANITY_DB_ALLOW_SINGLE_ACCOUNT", "false")
    if value not in ("false", "true"):
        raise ReviewError("DATABASE_CONFIG", "SANITY_DB_ALLOW_SINGLE_ACCOUNT must be false or true")
    return "single_account" if value == "true" else "restricted"


def postgres_parameters(settings, side):
    """Pass credentials as driver kwargs, never interpolate them into a DSN."""
    if side not in ("vwp", "oa"):
        raise ReviewError("DATABASE_CONFIG", "Unknown application side")
    # Existing explicit per-side DSNs remain authoritative for tests/deployments.
    # A connection failure must never retry using a different configuration.
    dsn = settings.get(f"SANITY_{side.upper()}_DSN")
    if dsn:
        return {"dsn": dsn}
    fields = {"host": "HOST", "dbname": "NAME", "user": "USER_NAME", "password": "PASSWORD"}
    params = {key: settings.get("SANITY_DB_" + suffix) for key, suffix in fields.items()}
    if any(not value for value in params.values()):
        raise ReviewError("DATABASE_NOT_CONFIGURED",
                          "Set SANITY_DB_HOST, SANITY_DB_NAME, SANITY_DB_USER_NAME and SANITY_DB_PASSWORD")
    if any(not isinstance(value, str) or "\x00" in value for value in params.values()):
        raise ReviewError("DATABASE_CONFIG", "Invalid PostgreSQL connection settings")
    port = settings.get("SANITY_DB_PORT", "5432")
    if (not isinstance(port, str) or not 1 <= len(port) <= 5 or not port.isascii()
            or not port.isdecimal() or not 1 <= int(port) <= 65535):
        raise ReviewError("DATABASE_CONFIG", "SANITY_DB_PORT must be between 1 and 65535")
    sslmode = settings.get("SANITY_DB_SSLMODE", "verify-full")
    if sslmode not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
        raise ReviewError("DATABASE_CONFIG", "Invalid SANITY_DB_SSLMODE")
    params.update(port=int(port), sslmode=sslmode)
    ca = settings.get("SANITY_DB_SSLROOTCERT")
    if ca:
        if not isinstance(ca, str) or "\x00" in ca or not Path(ca).is_absolute():
            raise ReviewError("DATABASE_CONFIG", "SANITY_DB_SSLROOTCERT must be an absolute CA file path")
        params["sslrootcert"] = ca
    return params
