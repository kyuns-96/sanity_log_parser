"""VWP-local, operator-owned model profiles; credentials never leave VWP."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

from common.sanity_common import ReviewError, digest
from common.sanity_config import load_environment
from common.sanity_privacy import SecretGuard
from common.sanity_vwp_ai import CATALOG_BYTES, PUBLIC_PROFILE_KEYS, require, strict_json, validate_profile

PRIVATE_KEYS = frozenset(("endpoint", "fallback_endpoint", "token", "client_api_key", "ca_path"))


def _endpoint(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(c.isspace() or ord(c) < 33 for c in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme in ("http", "https") and bool(url.hostname) and url.port != 0 and not any(
            (url.username, url.password, url.query, url.fragment))
    except ValueError:
        return False


def validate_private_profile(value):
    require(isinstance(value, dict) and set(value) == (PUBLIC_PROFILE_KEYS - {"sha256"}) | PRIVATE_KEYS,
            "AI_VWP_PROFILE_CONFIG")
    require(_endpoint(value["endpoint"]) and (value["fallback_endpoint"] == "" or
            _endpoint(value["fallback_endpoint"]) and value["fallback_endpoint"] != value["endpoint"]),
            "AI_VWP_PROFILE_CONFIG")
    require(isinstance(value["ca_path"], str) and (not value["ca_path"] or Path(value["ca_path"]).is_absolute()),
            "AI_VWP_PROFILE_CONFIG")
    require(isinstance(value["token"], str) and 1 <= len(value["token"]) <= 8192
            and all(33 <= ord(c) <= 126 for c in value["token"]), "AI_VWP_PROFILE_CONFIG")
    key = value["client_api_key"]
    require(isinstance(key, str) and ((value["auth_mode"] == "bearer_x_client" and 1 <= len(key) <= 8192
            and all(33 <= ord(c) <= 126 for c in key)) or
            (value["auth_mode"] != "bearer_x_client" and key == "")), "AI_VWP_PROFILE_CONFIG")
    public = {k: value[k] for k in PUBLIC_PROFILE_KEYS if k != "sha256"}
    public["sha256"] = digest(value)
    validate_profile(public)
    SecretGuard({"VWP_MODEL_SECRET":value["token"], "VWP_CLIENT_SECRET":key,
                 "VWP_ENDPOINT_SECRET":value["endpoint"], "VWP_BACKUP_SECRET":value["fallback_endpoint"]}).ensure_public(public)
    return public


class ProfileStore:
    def __init__(self, profiles):
        require(isinstance(profiles, list) and len(profiles) <= 50, "AI_VWP_PROFILE_CONFIG")
        self.private, self.public = {}, {}
        for profile in profiles:
            public = validate_private_profile(profile)
            require(public["id"] not in self.public, "AI_VWP_PROFILE_CONFIG")
            self.private[public["id"]] = profile
            self.public[public["id"]] = public

    @classmethod
    def load(cls, settings=None):
        settings = load_environment() if settings is None else settings
        path = settings.get("SANITY_VWP_AI_PROFILES_FILE", "")
        require(isinstance(path, str) and Path(path).is_absolute(), "AI_VWP_PROFILE_CONFIG")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                        info.st_uid in (0, os.geteuid()) and info.st_mode & 0o077 == 0 and
                        0 < info.st_size <= CATALOG_BYTES, "AI_VWP_PROFILE_CONFIG")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(CATALOG_BYTES + 1)
                after = os.fstat(fd)
                require((info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                        (after.st_dev, after.st_ino, after.st_uid, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        and after.st_nlink == 1 and after.st_mode & 0o077 == 0, "AI_VWP_PROFILE_CONFIG")
            finally:
                os.close(fd)
        except OSError:
            raise ReviewError("AI_VWP_PROFILE_CONFIG", "Cannot read a trusted VWP model profile file") from None
        value = strict_json(raw, CATALOG_BYTES)
        require(isinstance(value,dict) and set(value) == {"schema_version", "profiles"} and type(value["schema_version"]) is int
                and value["schema_version"] == 1, "AI_VWP_PROFILE_CONFIG")
        return cls(value["profiles"])

    def get(self, profile_id, expected_sha256):
        require(profile_id in self.public, "AI_VWP_PROFILE_UNAVAILABLE")
        public = self.public[profile_id]
        require(public["sha256"] == expected_sha256, "AI_CONFIG_CHANGED")
        return self.private[profile_id], public
