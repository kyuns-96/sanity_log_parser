"""Personal model credentials in an encrypted, owner-bound WS_CONFIG ledger.

Only authenticated self-service routes and authorized job dispatch use this
store. It never returns a token to HTTP clients. Removal appends a tombstone;
previous ciphertext remains subject to the existing immutable DB contract.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import re

from cryptography.fernet import Fernet, InvalidToken

from common.sanity_clustering import check
from common.sanity_common import ReviewError, _decode_json, canonical_json
from common.sanity_config import load_environment
from common.sanity_db import lock_key, uuid_value
from oa.sanity_workspace import WorkspaceStore, record_id

NAMESPACE = "oa-ai-user-token-v1"
PREFIX = "ait1.user."


def token_source(settings):
    source = settings.get("SANITY_OA_AI_TOKEN_SOURCE", "server")
    check(source in ("server", "user"), "AI_CONFIG", "Invalid OA token source")
    return source


def expiry_hint(token):
    """Unverified exp is a display/expiry hint, never an authentication result."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        part = parts[1]
        claims = _decode_json(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)).decode("utf-8"))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if type(exp) is int and 0 <= exp <= 253402300799:
            return datetime.fromtimestamp(exp, timezone.utc)
    except (ReviewError, ValueError, UnicodeError, OverflowError, OSError, RecursionError):
        pass
    return None


class UserTokenStore:
    def __init__(self, db, settings=None, *, connection_id=None, credential_scope=None):
        self.db = db
        self.settings = load_environment() if settings is None else settings
        self.connection_id = str(uuid_value(connection_id)) if connection_id is not None else None
        self.credential_scope = credential_scope
        check((self.connection_id is None and credential_scope is None) or
              (self.connection_id is not None and isinstance(credential_scope, str)
               and re.fullmatch(r"[0-9a-f]{64}", credential_scope)), "AI_CONFIG")
        self.namespace = "oa-ai-user-token-v2" if self.connection_id else NAMESPACE
        check(self.connection_id is not None or token_source(self.settings) == "user", "AI_PERSONAL_TOKEN_DISABLED")
        try:
            self.cipher = Fernet(self.settings.get("SANITY_OA_AI_TOKEN_STORE_SECRET", "").encode("ascii"))
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise ReviewError("AI_TOKEN_STORE_NOT_CONFIGURED", "Configure the personal AI token store") from None

    def _prefix(self, user_id):
        check(isinstance(user_id, str) and 1 <= len(user_id) <= 200, "UNAUTHORIZED")
        if self.connection_id:
            return "ait2." + hashlib.sha256(canonical_json([user_id, self.connection_id])).hexdigest() + "."
        return PREFIX + hashlib.sha256(user_id.encode("utf-8")).hexdigest() + "."

    def _latest(self, user_id):
        prefix = self._prefix(user_id)
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                              "ORDER BY workspace_key DESC LIMIT 1", (prefix + "%",)).fetchone()
        if row is None:
            return None
        value = row["payload"]
        fields = {"namespace", "schema_version", "user_id", "revision", "ciphertext"}
        if self.connection_id:fields.update(("connection_id", "credential_scope"))
        check(isinstance(value, dict) and set(value) == fields
              and value["namespace"] == self.namespace and value.get("connection_id") == self.connection_id
              and type(value["schema_version"]) is int and value["schema_version"] == 1
              and value["user_id"] == user_id and type(value["revision"]) is int
              and 1 <= value["revision"] <= 9999999999, "AI_TOKEN_UNAVAILABLE")
        key = prefix + f"{value['revision']:010d}"
        check(row["workspace_key"] == key and row["record_id"] == record_id("WS_CONFIG", key)
              and row["state"] == {} and row["version"] == 0 and row["status"] == "RECORDED" and row["created_at"] == row["updated_at"]
              and row["project"] is None and row["workspace_project_ref"] is None and row["workspace_block_ref"] is None,
              "AI_TOKEN_UNAVAILABLE")
        encrypted = value["ciphertext"]
        if self.connection_id:
            check(isinstance(value["credential_scope"], str) and re.fullmatch(r"[0-9a-f]{64}", value["credential_scope"]), "AI_TOKEN_UNAVAILABLE")
        check(encrypted is None or isinstance(encrypted, str) and 1 <= len(encrypted) <= 20000, "AI_TOKEN_UNAVAILABLE")
        return row

    def _decrypt(self, row):
        value = row["payload"]
        try:
            content = _decode_json(self.cipher.decrypt(value["ciphertext"].encode("ascii")).decode("utf-8"))
            fields = {"user_id", "revision", "token"}
            if self.connection_id:fields.update(("connection_id", "credential_scope"))
            check(isinstance(content, dict) and set(content) == fields
                  and content.get("connection_id") == self.connection_id
                  and content.get("credential_scope") == value.get("credential_scope")
                  and content["user_id"] == value["user_id"] and content["revision"] == value["revision"],
                  "AI_TOKEN_UNAVAILABLE")
            token = content["token"]
            check(isinstance(token, str) and 1 <= len(token) <= 8192
                  and all(33 <= ord(ch) <= 126 for ch in token), "AI_TOKEN_UNAVAILABLE")
            return token
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError):
            raise ReviewError("AI_TOKEN_UNAVAILABLE", "Personal AI token is unavailable") from None

    def status(self, user_id):
        row = self._latest(user_id)
        registered = bool(row and row["payload"]["ciphertext"] is not None)
        changed = bool(registered and self.connection_id and row["payload"]["credential_scope"] != self.credential_scope)
        expiry = expiry_hint(self._decrypt(row)) if registered and not changed else None
        now = datetime.now(timezone.utc)
        return {"enabled": True, "registered": registered, **({"connection_changed":changed} if self.connection_id else {}),
                "revision": row["payload"]["revision"] if row else 0,
                "updated_at": row["created_at"].isoformat() if row else None,
                "expires_at": expiry.isoformat() if expiry else None,
                "expired": bool(expiry and expiry <= now),
                "expires_soon": bool(expiry and now < expiry <= now + timedelta(days=30))}

    def get_token(self, user_id):
        # Hold through dispatch reservation/result publication, not HTTP I/O.
        # A token replacement/removal uses the matching exclusive lock.
        self.db.execute("SELECT pg_advisory_xact_lock_shared(%s)", (lock_key(self._prefix(user_id)),))
        row = self._latest(user_id)
        check(row is not None and row["payload"]["ciphertext"] is not None, "AI_USER_TOKEN_REQUIRED")
        if self.connection_id:
            check(row["payload"]["credential_scope"] == self.credential_scope, "AI_CREDENTIAL_SCOPE_CHANGED")
        token = self._decrypt(row)
        expiry = expiry_hint(token)
        check(expiry is None or expiry > datetime.now(timezone.utc), "AI_USER_TOKEN_EXPIRED")
        return token, row["payload"]["revision"]

    def save(self, user_id, token, expected_revision):
        check(type(expected_revision) is int and expected_revision >= 0, "INVALID_REQUEST")
        if token is not None:
            check(isinstance(token, str) and 1 <= len(token) <= 8192
                  and all(33 <= ord(ch) <= 126 for ch in token), "INVALID_AI_TOKEN")
            expiry = expiry_hint(token)
            check(expiry is None or expiry > datetime.now(timezone.utc), "AI_USER_TOKEN_EXPIRED")
        prefix = self._prefix(user_id)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(prefix),))
        row = self._latest(user_id)
        revision = row["payload"]["revision"] if row else 0
        check(revision == expected_revision, "REVISION_CONFLICT")
        check(revision < 9999999999, "AI_TOKEN_UNAVAILABLE")
        revision += 1
        binding = {"connection_id": self.connection_id, "credential_scope":self.credential_scope} if self.connection_id else {}
        encrypted = None if token is None else self.cipher.encrypt(canonical_json(
            {"user_id": user_id, "revision": revision, "token": token, **binding})).decode("ascii")
        WorkspaceStore(self.db).put("WS_CONFIG", prefix + f"{revision:010d}",
            {"namespace": self.namespace, "schema_version": 1, "user_id": user_id,
             "revision": revision, "ciphertext": encrypted, **binding})
        return self.status(user_id)
