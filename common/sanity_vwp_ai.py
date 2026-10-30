"""Public VWP inference profile contract; no cross-server credentials."""
import hashlib
import re
from datetime import datetime, timezone
from common.sanity_common import ReviewError, canonical_json, _decode_json
from common.sanity_db import MAX_BYTES

VERSION = 1
CATALOG_BYTES = 512 * 1024
CATALOG_TTL = 1200
PUBLIC_PROFILE_KEYS = frozenset(("id", "label", "api", "models", "auth_mode", "client_name", "client_version",
    "session_mode", "session_id", "timeout", "max_input_bytes", "max_output_bytes", "allowed_user_ids", "sha256"))


def require(value, code="AI_VWP_PROTOCOL"):
    if not value:
        raise ReviewError(code, "Invalid or unavailable VWP inference contract")


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value) is not None


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        require(result.tzinfo is not None)
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ReviewError("AI_VWP_PROTOCOL", "Invalid VWP timestamp") from None


def strict_json(raw, limit=MAX_BYTES):
    require(isinstance(raw, bytes) and 0 < len(raw) <= limit)
    try:
        return _decode_json(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise ReviewError("AI_VWP_PROTOCOL", "Invalid bounded JSON") from None


def validate_profile(profile):
    require(isinstance(profile, dict) and set(profile) in (PUBLIC_PROFILE_KEYS, PUBLIC_PROFILE_KEYS | {'token_source'}))
    require(profile.get('token_source', 'user') == 'user')
    require(identifier(profile["id"]) and isinstance(profile["label"], str)
            and 1 <= len(profile["label"]) <= 100 and profile["label"].isprintable())
    require(profile["api"] in ("chat_completions", "responses")
            and profile["auth_mode"] in ("bearer", "bearer_x_client", "x_client_api_key"))
    require(isinstance(profile["models"], list) and 1 <= len(profile["models"]) <= 100
            and all(isinstance(v, str) and 1 <= len(v) <= 200 and not any(c.isspace() or ord(c) < 33 for c in v)
                    for v in profile["models"]) and len(set(profile["models"])) == len(profile["models"]))
    require(type(profile["timeout"]) is int and 1 <= profile["timeout"] <= 600)
    require(all(type(profile[k]) is int and 1 <= profile[k] <= MAX_BYTES
                for k in ("max_input_bytes", "max_output_bytes")))
    require(isinstance(profile["allowed_user_ids"], list) and 1 <= len(profile["allowed_user_ids"]) <= 500
            and all(isinstance(v, str) and 1 <= len(v) <= 200 and v.isprintable() for v in profile["allowed_user_ids"])
            and len(set(profile["allowed_user_ids"])) == len(profile["allowed_user_ids"]))
    require(isinstance(profile["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", profile["sha256"]) is not None)
    gateway = profile["auth_mode"] == "bearer_x_client"
    require(all(isinstance(profile[k], str) for k in ("client_name", "client_version", "session_mode", "session_id")))
    if gateway:
        require(profile["api"] == "chat_completions" and profile["session_mode"] in ("per_request", "fixed")
                and all(1 <= len(profile[k]) <= 200 and profile[k].isprintable() for k in ("client_name", "client_version")))
        require((profile["session_mode"] == "fixed" and 1 <= len(profile["session_id"]) <= 200
                 and profile["session_id"].isprintable()) or
                (profile["session_mode"] == "per_request" and profile["session_id"] == ""))
    else:
        require(profile["client_name"] == profile["client_version"] == profile["session_id"] == ""
                and profile["session_mode"] == "disabled")
    return profile
