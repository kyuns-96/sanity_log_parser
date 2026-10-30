"""VWP-hosted inference with an OA-owned job ledger and public model catalog."""
from __future__ import annotations
import time
from copy import copy
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_config import load_environment
from common.sanity_db import Database, uuid_value
from common.sanity_ai_execution import worker_site
from common.sanity_privacy import SecretGuard
from common.sanity_vwp_ai import VERSION, CATALOG_BYTES, CATALOG_TTL, require, strict_json, timestamp, validate_profile
from oa.sanity_ai_model import OAAdvisor
from oa.sanity_workspace import WorkspaceStore, record_id

PROVIDER = "VWP_OPENAI_COMPATIBLE"
PREFIX, NAMESPACE = "vwpai.catalog.", "vwp-ai-catalog-v1"

def now(db):
    return db.execute("SELECT clock_timestamp() AS at").fetchone()["at"]

class Catalog:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def validate(value):
        require(isinstance(value, dict) and set(value) == {"schema_version", "catalog_id", "created_at", "expires_at", "profiles"}
                and type(value["schema_version"]) is int and value["schema_version"] == VERSION
                and len(canonical_json(value)) <= CATALOG_BYTES)
        require(str(uuid_value(value["catalog_id"])) == value["catalog_id"])
        require(0 < (timestamp(value["expires_at"]) - timestamp(value["created_at"])).total_seconds() <= CATALOG_TTL)
        require(isinstance(value["profiles"], list) and len(value["profiles"]) <= 50)
        for profile in value["profiles"]: validate_profile(profile)
        require(len({p["id"] for p in value["profiles"]}) == len(value["profiles"]))
        return value

    def _latest(self):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
            "ORDER BY workspace_key DESC LIMIT 1", (PREFIX + "%",)).fetchone()
        if row is None: return None
        value = row["payload"]
        require(isinstance(value, dict) and set(value) == {"namespace", "catalog", "sha256"}
                and value["namespace"] == NAMESPACE and value["sha256"] == digest(value["catalog"])
                and row["record_id"] == record_id("WS_CONFIG", row["workspace_key"])
                and row["state"] == {} and row["version"] == 0 and row["created_at"] == row["updated_at"],
                "AI_VWP_CATALOG_CORRUPT")
        return self.validate(value["catalog"])

    def profiles(self):
        value = self._latest()
        require(value is not None and timestamp(value["expires_at"]) > now(self.db), "AI_VWP_PROFILE_UNAVAILABLE")
        return value["profiles"]

    def get(self, profile_id, *, actor_id=None):
        profile = next((p for p in self.profiles() if p["id"] == profile_id), None)
        require(profile is not None, "AI_VWP_PROFILE_UNAVAILABLE")
        require(actor_id is None or actor_id in profile["allowed_user_ids"], "FORBIDDEN")
        return profile

    def admin_public(self):
        try:
            return [{k:v for k,v in p.items() if k not in ("allowed_user_ids", "client_name", "client_version", "session_mode", "session_id")}
                    for p in self.profiles()]
        except ReviewError as error:
            if error.code != "AI_VWP_PROFILE_UNAVAILABLE": raise
            return []

def publish_catalog(db_factory=None, profiles_factory=None):
    require(worker_site() == "vwp", "AI_WORKER_SITE")
    from common.sanity_vwp_ai_profiles import ProfileStore
    if profiles_factory is None:
        settings = load_environment()
        profiles = ProfileStore.load(settings) if settings.get("SANITY_VWP_AI_PROFILES_FILE") else ProfileStore([])
    else: profiles = profiles_factory()
    db_factory = db_factory or (lambda: Database("oa"))
    with db_factory() as db, db.transaction():
        at = now(db); identity = str(uuid4())
        catalog = {"schema_version":VERSION, "catalog_id":identity, "created_at":at.isoformat(),
                   "expires_at":(at + timedelta(seconds=CATALOG_TTL)).isoformat(),
                   "profiles":sorted(profiles.public.values(), key=lambda p:p["id"])}
        Catalog.validate(catalog)
        value = {"namespace":NAMESPACE, "catalog":catalog, "sha256":digest(catalog)}
        key = PREFIX + at.strftime("%Y%m%d%H%M%S%f") + "." + identity
        WorkspaceStore(db).put("WS_CONFIG", key, value)
    return {"status":"CATALOG_PUBLISHED", "profiles":len(catalog["profiles"])}

class LocalClient:
    def __init__(self, profiles_factory=None, *, transport=None):
        self.profiles_factory, self.transport = profiles_factory, transport

    def validate_current(self,profile):
        require(worker_site()=="vwp","AI_WORKER_SITE")
        from common.sanity_vwp_ai_profiles import ProfileStore
        (self.profiles_factory or ProfileStore.load)().get(profile["id"],profile["sha256"])

    def call(self, data, profile, settings, binding, *, observer=None, personal_token=None):
        from common.sanity_vwp_ai_profiles import ProfileStore
        from common.sanity_vwp_ai_http import invoke
        factory = self.profiles_factory or ProfileStore.load
        try:
            require(worker_site() == "vwp", "AI_WORKER_SITE")
            private, public = factory().get(profile["id"], profile["sha256"])
            require(binding["actor_id"] in public["allowed_user_ids"], "FORBIDDEN")
            require(settings.api == public["api"] and settings.model in public["models"]
                    and all(getattr(settings,k) <= public[k] for k in ("timeout", "max_input_bytes", "max_output_bytes")), "AI_CONFIG_CHANGED")
            require(isinstance(personal_token,str) and 1<=len(personal_token)<=8192
                    and all(33<=ord(c)<=126 for c in personal_token), 'AI_USER_TOKEN_REQUIRED')
            private = {**private, 'token': personal_token}
            guard=SecretGuard({'VWP_MODEL_SECRET':personal_token, 'VWP_CLIENT_SECRET':private['client_api_key'],
                'VWP_ENDPOINT_SECRET':private['endpoint'], 'VWP_BACKUP_SECRET':private['fallback_endpoint']})
            body=strict_json(data,private['max_input_bytes'])
            guard.ensure_public(body)
            # The user message contains a serialized packet; inspect its decoded
            # strings too, so JSON escaping cannot hide a credential.
            messages=body.get('messages',body.get('input',[])) if isinstance(body,dict) else []
            if isinstance(messages,list):
                for message in messages:
                    content=message.get('content') if isinstance(message,dict) else None
                    if isinstance(content,str):
                        try: packet=strict_json(content.encode('utf-8'),private['max_input_bytes'])
                        except (ReviewError,UnicodeError):continue
                        guard.ensure_public(packet)
        except ReviewError as error:
            error.model_called=False
            raise
        started = time.monotonic()
        called = False
        try:
            raw, routing = invoke(data, private, timeout=settings.timeout, max_output_bytes=settings.max_output_bytes,
                                  transport=self.transport,observer=observer)
            called = True
            factory().get(profile["id"], profile["sha256"])
            return raw, {"routing":routing, "seconds":time.monotonic()-started}
        except ReviewError as error:
            error.model_called = True if called else getattr(error,"model_called",None)
            raise

class VwpAdvisor(OAAdvisor):
    def __init__(self, settings, profile, *, client=None, binding=None):
        # The reserved endpoint is never dispatched. _call requires the
        # VWP-hosted worker and uses its local HTTP executor/credentials.
        super().__init__(settings)
        self.profile, self.client, self.binding = profile, client or LocalClient(), binding
        self.personal_token = None

    def describe(self):
        if worker_site()=="vwp":self.client.validate_current(self.profile)
        value = super().describe()
        original = value.pop("config_sha256")
        return {**value, "provider":PROVIDER, "execution_site":"vwp", "auth_mode":self.profile['auth_mode'],
            "profile_id":self.profile["id"], "profile_sha256":self.profile["sha256"],
            "config_sha256":digest({"settings":original, "site":"vwp", "profile":self.profile["sha256"]})}

    def for_model(self, model_id):
        require(model_id in self.model_choices(), "AI_MODEL_NOT_ALLOWED")
        if model_id == self.settings.model:return self
        result = VwpAdvisor(replace(self.settings, model=model_id), self.profile, client=self.client, binding=self.binding)
        return result.with_user_token(self.personal_token) if self.personal_token is not None else result

    def with_user_token(self, token):
        require(self.profile.get('token_source','user') == 'user', 'AI_CONFIG_CHANGED')
        require(isinstance(token,str) and 1<=len(token)<=8192 and all(33<=ord(c)<=126 for c in token),
                'AI_USER_TOKEN_REQUIRED')
        result = copy(self)
        result.personal_token = token
        result._guard = SecretGuard({'PERSONAL_AI_TOKEN':token, 'OA_ENDPOINT_SECRET':self.settings.endpoint,
                                    'OA_FALLBACK_ENDPOINT_SECRET':self.settings.fallback_endpoint})
        return result

    def bind_execution(self, *, call_id, job_id, actor_id, scope_sha256):
        require(actor_id in self.profile["allowed_user_ids"], "FORBIDDEN")
        result = copy(self)
        result.binding = {"call_id":str(uuid_value(call_id)), "job_id":str(uuid_value(job_id)),
            "actor_id":actor_id, "scope_sha256":scope_sha256, "connection_id":self.settings.connection_id,
            "connection_revision":self.settings.connection_revision}
        return result

    def _request(self, packet, instruction, *, budget=None):
        body, tokens = self._body(packet, instruction, budget=budget)
        profile = self.profile
        if profile["auth_mode"] == "bearer_x_client":
            body["extraData"] = {"clientName":profile["client_name"], "clientVersion":profile["client_version"],
                "sessionId":str(uuid4()) if profile["session_mode"] == "per_request" else profile["session_id"]}
        raw = canonical_json(body)
        require(len(raw) <= self.settings.max_input_bytes, "AI_INPUT_LIMIT")
        return raw, {}, tokens

    def _call(self, packet, instruction, prompt_version, attempt, *, budget=None, prepared_request=None):
        require(self.binding is not None, "AI_VWP_DISPATCH_REQUIRED")
        data, headers, tokens = prepared_request or self._request(packet, instruction, budget=budget)
        require(headers == {}, "AI_VWP_PROTOCOL")
        attempt["model_called"] = None
        try:
            raw, receipt = self.client.call(data, self.profile, self.settings, self.binding,
                                            observer=self._observe,personal_token=self.personal_token)
            attempt["model_called"] = True
            attempt["response_complete"] = True
            value, metadata = self._decode_reply(data, raw, prompt_version, budget=budget, input_tokens=tokens,
                                                routing=receipt["routing"])
            metadata["execution"] = {"site":"vwp", "profile_id":self.profile["id"], "profile_sha256":self.profile["sha256"],
                                      "call_id":self.binding["call_id"], "job_id":self.binding["job_id"]}
            return value, metadata
        except ReviewError as error:
            if hasattr(error, "model_called"):
                attempt["model_called"] = error.model_called
            for key in ("response_complete","http_status","retry_after_seconds"):
                if hasattr(error,key):attempt[key]=getattr(error,key)
            raise
