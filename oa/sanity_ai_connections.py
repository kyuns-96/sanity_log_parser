"""Administrator-owned inference connections; encrypted secrets and immutable revisions."""
from copy import deepcopy

from cryptography.fernet import Fernet, InvalidToken

from common.sanity_clustering import check
from common.sanity_common import ReviewError, _decode_json, canonical_json, digest
from common.sanity_config import load_environment
from common.sanity_db import lock_key, uuid_value
from common.sanity_privacy import SecretGuard
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_model import OAAdvisor, OASettings
from oa.sanity_ai_tokens import UserTokenStore
from oa.sanity_workspace import WorkspaceStore, record_id

NAMESPACE, PREFIX = "oa-ai-connections-v1", "aic1."
CONFIG_KEYS = frozenset(("label", "enabled", "endpoint", "fallback_endpoint", "api", "auth_mode",
    "model", "models", "issuance_url", "json_mode", "timeout", "max_input_bytes", "max_output_bytes",
    "client_name", "client_version", "session_mode", "session_id", "batch"))


def validate_config(config):
    check(isinstance(config, dict) and set(config) == CONFIG_KEYS, "AI_CONNECTION_CONFIG")
    check(isinstance(config["label"], str) and 1 <= len(config["label"]) <= 100
          and config["label"] == config["label"].strip() and config["label"].isprintable(), "AI_CONNECTION_CONFIG")
    check(type(config["enabled"]) is bool and config["auth_mode"] in
          ("bearer", "bearer_x_client", "x_client_api_key"), "AI_CONNECTION_CONFIG")
    from oa.ai_tokens_api import _issuance_url
    check(isinstance(config["issuance_url"], str) and (not config["issuance_url"] or
          _issuance_url({"SANITY_OA_AI_TOKEN_ISSUE_URL": config["issuance_url"]}) is not None), "AI_CONNECTION_CONFIG")
    check(isinstance(config["models"], list) and 1 <= len(config["models"]) <= 100, "AI_CONNECTION_CONFIG")
    batch = config["batch"]
    check(isinstance(batch, dict) and set(batch) == set(BatchSettings().public())
          and batch.get("tokenizer_mode") == "utf8_bytes" and batch.get("tokenizer_sha256") == "", "AI_BATCH_CONFIG")
    try:
        settings = BatchSettings(**batch)
    except (TypeError, ValueError):
        raise ReviewError("AI_BATCH_CONFIG", "Invalid connection batch settings") from None
    # Validate enabled and disabled entries equally, before they can be used.
    OASettings(enabled=True, endpoint=config["endpoint"], fallback_endpoint=config["fallback_endpoint"],
        token_source="user", token="", model=config["model"], models=tuple(config["models"]),
        api=config["api"], auth_mode=config["auth_mode"], json_mode=config["json_mode"],
        timeout=config["timeout"], max_input_bytes=config["max_input_bytes"], max_output_bytes=config["max_output_bytes"],
        client_api_key="validation-placeholder" if config["auth_mode"] == "bearer_x_client" else "",
        client_name=config["client_name"], client_version=config["client_version"],
        session_mode=config["session_mode"], session_id=config["session_id"], batch=settings)
    return deepcopy(config)


class ConnectionStore:
    def __init__(self, db, settings=None):
        self.db = db
        self.settings = load_environment() if settings is None else settings

    def _cipher(self):
        try:
            return Fernet(self.settings.get("SANITY_OA_AI_TOKEN_STORE_SECRET", "").encode("ascii"))
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise ReviewError("AI_TOKEN_STORE_NOT_CONFIGURED", "Configure the AI credential encryption key") from None

    def _validate(self, row):
        value = row["payload"]
        check(isinstance(value, dict) and set(value) == {"namespace", "connection_id", "revision", "actor_id",
              "config", "client_key_ciphertext", "sha256"} and value["namespace"] == NAMESPACE
              and type(value["revision"]) is int and 1 <= value["revision"] <= 9999999999
              and isinstance(value["actor_id"], str) and 1 <= len(value["actor_id"]) <= 200,
              "AI_CONNECTION_CORRUPT")
        connection_id = str(uuid_value(value["connection_id"]))
        key = PREFIX + connection_id + "." + f"{value['revision']:010d}"
        check(row["workspace_key"] == key and row["record_id"] == record_id("WS_CONFIG", key)
              and row["state"] == {} and row["version"] == 0 and row["status"] == "RECORDED"
              and row["created_at"] == row["updated_at"] and row["project"] is None
              and row["workspace_project_ref"] is None and row["workspace_block_ref"] is None
              and value["sha256"] == digest({k:v for k,v in value.items() if k != "sha256"}), "AI_CONNECTION_CORRUPT")
        validate_config(value["config"])
        encrypted = value["client_key_ciphertext"]
        check((value["config"]["auth_mode"] == "bearer_x_client" and isinstance(encrypted, str)
               and 1 <= len(encrypted) <= 20000) or
              (value["config"]["auth_mode"] != "bearer_x_client" and encrypted is None), "AI_CONNECTION_CORRUPT")
        return value

    def rows(self):
        rows = self.db.execute("SELECT * FROM (SELECT DISTINCT ON (payload->>'connection_id') * FROM {oa} "
            "WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s ORDER BY payload->>'connection_id',workspace_key DESC) "
            "latest ORDER BY workspace_key", (PREFIX + "%",)).fetchall()
        check(len(rows) <= 100, "AI_CONNECTION_LIMIT")
        return [self._validate(row) for row in rows]

    def get(self, connection_id, *, enabled=True, lock=True):
        connection_id = str(uuid_value(connection_id))
        if lock:
            self.db.execute("SELECT pg_advisory_xact_lock_shared(%s)", (lock_key(PREFIX + connection_id),))
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                              "ORDER BY workspace_key DESC LIMIT 1", (PREFIX + connection_id + ".%",)).fetchone()
        check(row is not None, "AI_CONNECTION_UNAVAILABLE")
        value = self._validate(row)
        check(value["connection_id"] == connection_id and (not enabled or value["config"]["enabled"]),
              "AI_CONNECTION_UNAVAILABLE")
        return value

    def _client_key(self, value):
        if value["client_key_ciphertext"] is None:
            return ""
        try:
            content = _decode_json(self._cipher().decrypt(value["client_key_ciphertext"].encode("ascii")).decode("utf-8"))
            check(set(content) == {"connection_id", "revision", "key"}
                  and content["connection_id"] == value["connection_id"] and content["revision"] == value["revision"]
                  and isinstance(content["key"], str) and 1 <= len(content["key"]) <= 8192
                  and all(33 <= ord(ch) <= 126 for ch in content["key"]), "AI_TOKEN_UNAVAILABLE")
            return content["key"]
        except (InvalidToken, ValueError, UnicodeError):
            raise ReviewError("AI_TOKEN_UNAVAILABLE", "Connection credentials are unavailable") from None

    @staticmethod
    def public(value):
        config = value["config"]
        return {"id":value["connection_id"], "revision":value["revision"], "label":config["label"],
                "auth_mode":config["auth_mode"], "model":config["model"], "models":config["models"],
                "issuance_url":config["issuance_url"] or None}

    @staticmethod
    def admin_public(value):
        return {"id":value["connection_id"], "revision":value["revision"], "config":deepcopy(value["config"]),
                "client_key_registered":value["client_key_ciphertext"] is not None}

    def credential_scope(self, value):
        return digest({"connection_id":value["connection_id"],
            "client_key_sha256":digest(self._client_key(value)) if value["client_key_ciphertext"] else None,
            **{key:value["config"][key] for key in
            ("endpoint", "fallback_endpoint", "api", "auth_mode", "issuance_url", "client_name")}})

    def tokens(self, value):
        return UserTokenStore(self.db, self.settings, connection_id=value["connection_id"],
                              credential_scope=self.credential_scope(value))

    def user_connections(self, user_id, *, include_disabled=False):
        result = []
        for value in self.rows():
            if not include_disabled and not value["config"]["enabled"]:continue
            entry = {**self.public(value), "enabled":value["config"]["enabled"]}
            try:
                status = self.tokens(value).status(user_id)
                error = ("AI_CREDENTIAL_SCOPE_CHANGED" if status.get("connection_changed") else
                         "AI_USER_TOKEN_EXPIRED" if status["expired"] else None if status["registered"] else "AI_USER_TOKEN_REQUIRED")
            except ReviewError as failure:
                if failure.code not in ("AI_TOKEN_STORE_NOT_CONFIGURED", "AI_TOKEN_UNAVAILABLE"):raise
                status, error = {"enabled":True, "registered":False, "revision":0}, failure.code
            result.append({**entry, "credential":status, "credential_ready":error is None, "credential_error":error})
        return result

    def advisor(self, connection_id, model_id=None, *, transport=None):
        value = self.get(connection_id)
        config = value["config"]
        advisor = OAAdvisor(OASettings(enabled=True, endpoint=config["endpoint"], fallback_endpoint=config["fallback_endpoint"],
            token_source="user", token="", model=config["model"], models=tuple(config["models"]), api=config["api"],
            auth_mode=config["auth_mode"], json_mode=config["json_mode"], timeout=config["timeout"],
            max_input_bytes=config["max_input_bytes"], max_output_bytes=config["max_output_bytes"],
            ca_path=self.settings.get("SANITY_OA_AI_CA_PATH", ""), client_api_key=self._client_key(value),
            client_name=config["client_name"], client_version=config["client_version"],
            session_mode=config["session_mode"], session_id=config["session_id"], batch=BatchSettings(**config["batch"]),
            connection_id=value["connection_id"], connection_revision=value["revision"], connection_label=config["label"]),
            transport=transport)
        return advisor.for_model(model_id) if model_id is not None else advisor

    def save(self, actor, connection_id, config, expected_revision, client_api_key=None):
        check(getattr(actor, "auth_method", None) == "sso" and getattr(actor, "access", None) is not None
              and actor.access.db is self.db, "FORBIDDEN")
        actor.access._admin(actor)
        connection_id = str(uuid_value(connection_id))
        config = validate_config(config)
        check(type(expected_revision) is int and 0 <= expected_revision < 9999999999, "INVALID_REQUEST")
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(PREFIX + "catalog"),))
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(PREFIX + connection_id),))
        values = {value["connection_id"]:value for value in self.rows()}
        previous = values.get(connection_id)
        check((previous["revision"] if previous else 0) == expected_revision, "REVISION_CONFLICT")
        check(previous is not None or len(values) < 100, "AI_CONNECTION_LIMIT")
        self._cipher()  # Even Bearer-only connections require encrypted user credentials.
        key = ""
        if config["auth_mode"] == "bearer_x_client":
            key = client_api_key if client_api_key is not None else self._client_key(previous) if previous else ""
            check(isinstance(key, str) and 1 <= len(key) <= 8192 and all(33 <= ord(ch) <= 126 for ch in key), "AI_CONNECTION_CONFIG")
        else:
            check(client_api_key in (None, ""), "AI_CONNECTION_CONFIG", "This connection does not use a shared client key")
        SecretGuard({"AI_CLIENT_SECRET":key}).ensure_public({"label":config["label"], "models":config["models"],
                                                          "issuance_url":config["issuance_url"]})
        revision = expected_revision + 1
        encrypted = self._cipher().encrypt(canonical_json({"connection_id":connection_id,"revision":revision,"key":key})).decode("ascii") if key else None
        value = {"namespace":NAMESPACE, "connection_id":connection_id,"revision":revision,"actor_id":actor.user_id,
                 "config":config,"client_key_ciphertext":encrypted}
        value["sha256"] = digest(value)
        WorkspaceStore(self.db).put("WS_CONFIG", PREFIX + connection_id + "." + f"{revision:010d}", value)
        return self.admin_public(value)
