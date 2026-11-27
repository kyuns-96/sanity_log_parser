"""Encrypted, owner-bound personal Confluence PAT ledger stored in WS_CONFIG.

Never returns plaintext tokens to browsers. Uses Fernet encryption keyed by
CONFLUENCE_PAT_STORE_SECRET or SANITY_OA_AI_TOKEN_STORE_SECRET.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from common.sanity_clustering import check
from common.sanity_common import ReviewError, canonical_json, decode_json
from common.sanity_config import load_environment
from common.sanity_db import lock_key
from oa.sanity_ws_config import read_latest_revisioned_ws_config, write_revisioned_ws_config

NAMESPACE = "oa-confluence-user-pat-v1"
PREFIX = "cfpat1."


def _resolve_fernet(settings: dict[str, str], *, allow_local_fallback: bool = False) -> Fernet:
    raw = settings.get("CONFLUENCE_PAT_STORE_SECRET") or settings.get("SANITY_OA_AI_TOKEN_STORE_SECRET") or ""
    if not raw and allow_local_fallback:
        seed = (settings.get("SANITY_DASHBOARD_TOKEN") or "local-dev-confluence-pat-key").encode("utf-8")
        raw = base64.urlsafe_b64encode(hashlib.sha256(seed).digest()).decode("ascii")
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ReviewError(
            "AI_TOKEN_STORE_NOT_CONFIGURED",
            "Configure CONFLUENCE_PAT_STORE_SECRET or SANITY_OA_AI_TOKEN_STORE_SECRET on OA.",
        ) from None


class ConfluencePatStore:
    def __init__(self, db, settings=None, *, local_mode: bool = False):
        self.db = db
        self.settings = load_environment() if settings is None else settings
        self.cipher = _resolve_fernet(self.settings, allow_local_fallback=local_mode)

    def _prefix(self, user_id: str) -> str:
        check(isinstance(user_id, str) and 1 <= len(user_id) <= 200, "UNAUTHORIZED")
        return PREFIX + hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:48] + "."

    def _latest(self, user_id: str):
        row, value = read_latest_revisioned_ws_config(self.db, self._prefix(user_id), error_code="AI_TOKEN_UNAVAILABLE")
        if row is None:
            return None
        check(
            isinstance(value, dict)
            and value.get("namespace") == NAMESPACE
            and value.get("user_id") == user_id
            and isinstance(value.get("revision"), int),
            "AI_TOKEN_UNAVAILABLE",
        )
        return row

    def status(self, user_id: str) -> dict:
        row = self._latest(user_id)
        registered = bool(row and row["payload"].get("ciphertext"))
        return {
            "registered": registered,
            "revision": int(row["payload"]["revision"]) if row else 0,
            "updated_at": row["created_at"].isoformat() if (row and row.get("created_at")) else None,
        }

    def get_pat(self, user_id: str) -> str:
        row = self._latest(user_id)
        if not row or not row["payload"].get("ciphertext"):
            raise ReviewError("CONFLUENCE_PAT_REQUIRED", "Register your personal Confluence PAT before publishing.")
        try:
            plain = decode_json(self.cipher.decrypt(row["payload"]["ciphertext"].encode("ascii")).decode("utf-8"))
            token = plain.get("token") if isinstance(plain, dict) else None
            check(
                isinstance(token, str)
                and 1 <= len(token) <= 4096
                and plain.get("user_id") == user_id
                and plain.get("revision") == row["payload"]["revision"],
                "AI_TOKEN_UNAVAILABLE",
            )
            return token
        except (InvalidToken, ValueError, TypeError, UnicodeError):
            raise ReviewError("AI_TOKEN_UNAVAILABLE", "Stored Confluence PAT could not be decrypted.") from None

    def save(self, user_id: str, token: str, expected_revision: int) -> dict:
        cleaned = (token or "").strip()
        check(1 <= len(cleaned) <= 4096 and all(33 <= ord(ch) <= 126 for ch in cleaned), "INVALID_REQUEST", "Invalid PAT format.")
        prefix = self._prefix(user_id)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("confluence-pat:" + user_id),))
        row = self._latest(user_id)
        cur_rev = int(row["payload"]["revision"]) if row else 0
        check(cur_rev == expected_revision, "REVISION_CONFLICT", "Confluence PAT revision changed; reload first.")
        next_rev = cur_rev + 1
        ciphertext = self.cipher.encrypt(
            canonical_json({"user_id": user_id, "revision": next_rev, "token": cleaned})
        ).decode("ascii")
        payload = {
            "namespace": NAMESPACE,
            "schema_version": 1,
            "user_id": user_id,
            "revision": next_rev,
            "ciphertext": ciphertext,
        }
        write_revisioned_ws_config(self.db, prefix, next_rev, payload)
        return {"registered": True, "revision": next_rev}

    def remove(self, user_id: str, expected_revision: int) -> dict:
        prefix = self._prefix(user_id)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("confluence-pat:" + user_id),))
        row = self._latest(user_id)
        cur_rev = int(row["payload"]["revision"]) if row else 0
        check(cur_rev == expected_revision, "REVISION_CONFLICT", "Confluence PAT revision changed; reload first.")
        next_rev = cur_rev + 1
        payload = {
            "namespace": NAMESPACE,
            "schema_version": 1,
            "user_id": user_id,
            "revision": next_rev,
            "ciphertext": None,
        }
        write_revisioned_ws_config(self.db, prefix, next_rev, payload)
        return {"registered": False, "revision": next_rev}
