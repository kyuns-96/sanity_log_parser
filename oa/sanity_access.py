"""Production SSO permissions in an append-only, namespaced WS_CONFIG ledger.

No DDL, synthetic users, or automatic privileged provisioning. Callers must keep
one transaction open and hold ``access_lock`` while using this store: shared for
business reads/writes, exclusive for management mutations. READ COMMITTED avoids
using a snapshot taken before an advisory-lock wait. Permissions are reconstructed
on every request; neither SSO sessions nor project payloads contain grants.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit
from uuid import UUID

from common.sanity_common import ReviewError
from common.sanity_db import json_value, lock_key, stable_uuid
from oa.sanity_auth import Actor

NAMESPACE = "oa-rbac-v1"
PREFIX = "rbac1.event."
ACCESS_LOCK_KEY = lock_key("oa-rbac-v1:all-access-control")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_EVENT_FIELDS = {"namespace", "schema_version", "revision", "kind", "command_id", "actor_id", "created_at", "data"}
_NULL_FIELDS = ("project", "run_id", "bundle_id", "review_unit_id", "review_revision", "request_id",
                "entity_key", "classification_revision", "payload_bytes", "payload_sha256",
                "workspace_project_ref", "workspace_block_ref", "workspace_version_ref",
                "workspace_review_report_ref", "workspace_review_version_ref", "workspace_review_event_ref",
                "workspace_pipeline_report_ref", "workspace_pipeline_version_ref", "workspace_pipeline_event_ref",
                "workspace_pipeline_cluster_ref")


def _error(code="AUTHORIZATION_STATE_CORRUPT"):
    raise ReviewError(code, "Access-control contract could not be satisfied")


def _identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _text(value, maximum, *, empty=False):
    return (isinstance(value, str) and (0 if empty else 1) <= len(value) <= maximum
            and value == value.strip() and re.search(r"[\x00-\x1f\x7f]", value) is None)


def _uuid(value):
    try:
        parsed = str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        _error("INVALID_REQUEST")
    return parsed


def _timestamp(value):
    if not isinstance(value, str) or _TIME.fullmatch(value) is None:
        _error()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _error()
    return parsed


def administrator_id(settings):
    """Unset disables production RBAC. IDs are opaque and case-sensitive."""
    value = settings.get("SANITY_ADMIN_USER_ID")
    if value is None or value == "":
        return None
    if not _identifier(value):
        _error("AUTHORIZATION_CONFIG")
    return value


def access_lock(db, exclusive=False):
    """Transaction advisory lock shared by every production RBAC entry point.

    The caller owns the transaction and must use READ COMMITTED. A service worker
    also takes the shared lock before checking scope and receiving a bundle.
    """
    if type(exclusive) is not bool:
        _error("AUTHORIZATION_CONFIG")
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    db.execute("SELECT " + function + "(%s)", (ACCESS_LOCK_KEY,))


def require_mutation_request(request):
    """Require an explicit same-origin JSON client action, without proxy trust.

    An absent Origin is allowed for non-browser clients. A supplied Origin must
    match the actual Host (no X-Forwarded-* trust); explicitly cross-site browser
    requests are rejected even if their cookies are otherwise valid.
    """
    if request.headers.get("x-sanity-request") != "1":
        _error("FORBIDDEN")
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.lower() not in {"same-origin", "none"}:
        _error("FORBIDDEN")
    origin = request.headers.get("origin")
    if origin is not None:
        try:
            parsed = urlsplit(origin)
            host = request.headers.get("host", "")
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username is not None
                    or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                    or parsed.netloc.lower() != host.lower()):
                _error("FORBIDDEN")
        except ValueError:
            _error("FORBIDDEN")


class AccessStore:
    def __init__(self, db, administrator_id):
        self.db, self.administrator_id = db, administrator_id
        if administrator_id is not None and not _identifier(administrator_id):
            _error("AUTHORIZATION_CONFIG")
        self._ready = False
        self._ingestion = False
        self._cached = None

    def require_ready(self, *, ingestion=False):
        if type(ingestion) is not bool:
            _error("AUTHORIZATION_CONFIG")
        if self.administrator_id is None and not ingestion:
            _error("AUTHORIZATION_NOT_IMPLEMENTED")
        if getattr(self.db, "side", None) != "oa":
            _error("AUTHORIZATION_CONFIG")
        if ingestion:
            self._ingestion = True
        if self._ready:
            return
        rows = self.db.execute(
            "SELECT pg_get_expr(i.indpred,i.indrelid) AS predicate, "
            "ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum,position) "
            "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
            "WHERE k.position<=i.indnkeyatts ORDER BY k.position) AS key_columns "
            "FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND t.relname='sanity_oa_data' AND i.indisvalid AND i.indisready "
            "AND i.indislive AND i.indisunique AND i.indimmediate AND i.indexprs IS NULL", (self.db.schema,)).fetchall()
        if not any(tuple(row["key_columns"]) == ("record_type", "workspace_key")
                   and row["predicate"] == "(workspace_key IS NOT NULL)" for row in rows):
            _error("AUTHORIZATION_SCHEMA")
        self._ready = True

    def _identity(self, user_id, *, require_active=True):
        if not _identifier(user_id):
            _error("AUTHORIZATION_STATE_CORRUPT")
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='SSO_IDENTITY' AND workspace_key=%s", (user_id,)).fetchone()
        if row is None:
            _error("ACCOUNT_NOT_LINKED")
        payload, state = row["payload"], row["state"]
        if (not isinstance(payload, dict) or set(payload) != {"issuer", "subject", "display_name", "first_seen_at"}
                or not isinstance(state, dict) or set(state) != {"enabled", "last_seen_at"}
                or payload["subject"] != user_id or not _text(payload["issuer"], 512)
                or not _text(payload["display_name"], 100) or type(state["enabled"]) is not bool
                or row["record_id"] != stable_uuid("sso-identity-v1", "SSO_IDENTITY", user_id)
                or any(row.get(field) is not None for field in _NULL_FIELDS)):
            _error()
        if _timestamp(state["last_seen_at"]) < _timestamp(payload["first_seen_at"]):
            _error()
        if require_active and not state["enabled"]:
            _error("ACCOUNT_DISABLED")
        return {"user_id": user_id, "display_name": payload["display_name"]}

    def require_actor(self, actor):
        if not isinstance(actor, Actor) or actor.auth_method != "sso":
            _error("FORBIDDEN")
        self.require_ready()
        self._identity(actor.user_id)
        return actor

    def is_admin(self, user_id):
        return self.administrator_id is not None and user_id == self.administrator_id

    def _admin(self, actor):
        self.require_actor(actor)
        if not self.is_admin(actor.user_id):
            _error("FORBIDDEN")

    @staticmethod
    def _role_scope(role, project_id, block_id, *, code="INVALID_REQUEST"):
        if (role not in ("TOP", "OWNER") or not _identifier(project_id)
                or (role == "TOP" and block_id is not None)
                or (role == "OWNER" and not _identifier(block_id))):
            _error(code)

    def _state(self):
        self.require_ready(ingestion=self._ingestion)
        if self._cached is not None:
            return self._cached
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s", (PREFIX + "%",)).fetchall()
        events = []
        for row in rows:
            event = row["payload"]
            if (not isinstance(event, dict) or set(event) != _EVENT_FIELDS or event["namespace"] != NAMESPACE
                    or type(event["schema_version"]) is not int or event["schema_version"] != 1
                    or type(event["revision"]) is not int or event["revision"] < 1
                    or not _identifier(event["actor_id"]) or not isinstance(event["kind"], str)
                    or not isinstance(event["data"], dict)
                    or row["state"] != {} or row["version"] != 0 or row["updated_at"] != row["created_at"]
                    or any(row.get(field) is not None for field in _NULL_FIELDS)):
                _error()
            try:
                command_id = str(UUID(event["command_id"]))
            except (ValueError, TypeError, AttributeError):
                _error()
            key = PREFIX + command_id
            if (command_id != event["command_id"] or row["workspace_key"] != key
                    or row["record_id"] != stable_uuid("workspace-v1", "WS_CONFIG", key)):
                _error()
            _timestamp(event["created_at"])
            events.append(event)
        events.sort(key=lambda e: e["revision"])
        projects, blocks, requests, grants, commands = {}, {}, {}, {}, {}
        for expected, event in enumerate(events, 1):
            if event["revision"] != expected or event["command_id"] in commands:
                _error()
            commands[event["command_id"]] = event
            data, kind = event["data"], event["kind"]
            if kind == "PROJECT":
                if (set(data) != {"project_id", "name", "description"} or not _identifier(data["project_id"])
                        or not _text(data["name"], 100) or not _text(data["description"], 1000, empty=True)
                        or data["project_id"] in projects):
                    _error()
                projects[data["project_id"]] = {"id": data["project_id"], "name": data["name"], "description": data["description"]}
            elif kind == "BLOCK":
                if (set(data) != {"project_id", "block_id", "name"} or not _identifier(data["block_id"])
                        or not _identifier(data["project_id"])
                        or not _text(data["name"], 100) or data["project_id"] not in projects
                        or data["block_id"] in blocks):
                    _error()
                blocks[data["block_id"]] = {"id": data["block_id"], "project_id": data["project_id"], "name": data["name"]}
            elif kind == "REQUEST":
                if set(data) != {"role", "project_id", "block_id", "reason"} or not _text(data["reason"], 2000):
                    _error()
                self._role_scope(data["role"], data["project_id"], data["block_id"], code="AUTHORIZATION_STATE_CORRUPT")
                if data["project_id"] not in projects or (data["block_id"] is not None and
                        (data["block_id"] not in blocks or blocks[data["block_id"]]["project_id"] != data["project_id"])):
                    _error()
                if (any(r["user_id"] == event["actor_id"] and r["status"] == "PENDING"
                        and all(r[k] == data[k] for k in ("role", "project_id", "block_id")) for r in requests.values())
                        or any(g["user_id"] == event["actor_id"] and all(g[k] == data[k]
                               for k in ("role", "project_id", "block_id")) for g in grants.values())):
                    _error()
                requests[event["command_id"]] = {"request_id": event["command_id"], "user_id": event["actor_id"],
                    **data, "status": "PENDING", "created_at": event["created_at"]}
            elif kind == "DECISION":
                if (set(data) != {"request_id", "decision", "reason", "expected_revision"}
                        or not isinstance(data["request_id"], str) or data["decision"] not in ("APPROVE", "REJECT")
                        or not _text(data["reason"], 2000)
                        or type(data["expected_revision"]) is not int or data["expected_revision"] != expected - 1):
                    _error()
                request = requests.get(data["request_id"])
                if request is None or request["status"] != "PENDING":
                    _error()
                request.update(status="APPROVED" if data["decision"] == "APPROVE" else "REJECTED",
                               decided_by=event["actor_id"], decision_reason=data["reason"])
                if data["decision"] == "APPROVE":
                    if any(all(g[k] == request[k] for k in ("user_id", "role", "project_id", "block_id")) for g in grants.values()):
                        _error()
                    grants[request["request_id"]] = {"grant_id": request["request_id"], **{k: request[k]
                        for k in ("user_id", "role", "project_id", "block_id")}}
            elif kind == "REVOKE":
                if (set(data) != {"grant_id", "reason", "expected_revision"} or not isinstance(data["grant_id"], str)
                        or not _text(data["reason"], 2000)
                        or type(data["expected_revision"]) is not int or data["expected_revision"] != expected - 1
                        or data["grant_id"] not in grants):
                    _error()
                del grants[data["grant_id"]]
            else:
                _error()
        self._validate_registry(projects, blocks)
        self._cached = {"revision": len(events), "projects": projects, "blocks": blocks,
                        "requests": requests, "grants": grants, "commands": commands}
        return self._cached

    def _validate_registry(self, projects, blocks):
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type IN ('WS_PROJECT','WS_BLOCK') "
                               "AND payload->>'registry_namespace'=%s", (NAMESPACE,)).fetchall()
        if len(rows) != len(projects) + len(blocks):
            _error()
        for row in rows:
            kind, key = row["record_type"], row["workspace_key"]
            if kind == "WS_PROJECT":
                project = projects.get(key)
                expected = ({"id": key, "project_id": key, "name": project["name"], "description": project["description"],
                             "tops": [], "registry_namespace": NAMESPACE} if project else None)
                project_ref = None
            else:
                block = blocks.get(key)
                expected = ({"id": key, "project_id": block["project_id"], "name": block["name"],
                             "owners": [], "registry_namespace": NAMESPACE} if block else None)
                project_ref = stable_uuid("workspace-v1", "WS_PROJECT", block["project_id"]) if block else None
            if (expected is None or row["payload"] != expected
                    or row["record_id"] != stable_uuid("workspace-v1", kind, key)
                    or row["state"] != {} or row["version"] != 0 or row["created_at"] != row["updated_at"]
                    or row["project"] != expected["project_id"] or row["workspace_project_ref"] != project_ref
                    or any(row.get(field) is not None for field in _NULL_FIELDS if field not in {"project", "workspace_project_ref"})):
                _error()

    def _retry(self, command_id, kind, actor, data):
        existing = self._state()["commands"].get(command_id)
        if existing is not None and (existing["kind"] != kind or existing["actor_id"] != actor.user_id or existing["data"] != data):
            _error("REQUEST_CONFLICT")
        return existing is not None

    def _append(self, command_id, kind, actor, data):
        revision = self._state()["revision"] + 1
        event = {"namespace": NAMESPACE, "schema_version": 1, "revision": revision, "kind": kind,
                 "command_id": command_id, "actor_id": actor.user_id,
                 "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "data": data}
        key = PREFIX + command_id
        self.db.execute("INSERT INTO {oa}(record_id,record_type,workspace_key,payload) VALUES (%s,'WS_CONFIG',%s,%s)",
                        (stable_uuid("workspace-v1", "WS_CONFIG", key), key, json_value(event)))
        self._cached = None
        return revision

    def catalog(self):
        state = self._state()
        return {"projects": sorted(state["projects"].values(), key=lambda p: p["id"]),
                "blocks": sorted(state["blocks"].values(), key=lambda b: (b["project_id"], b["id"]))}

    def snapshot(self, user_id):
        self._identity(user_id)
        state = self._state()
        return {"user_id": user_id, "is_admin": self.is_admin(user_id), "revision": state["revision"],
                "grants": [dict(g) for g in state["grants"].values() if g["user_id"] == user_id],
                "requests": [dict(r) for r in state["requests"].values() if r["user_id"] == user_id]}

    def admin_snapshot(self, actor):
        self._admin(actor)
        state = self._state()
        users = []
        for row in self.db.execute("SELECT workspace_key FROM {oa} WHERE record_type='SSO_IDENTITY' ORDER BY workspace_key").fetchall():
            try:
                users.append(self._identity(row["workspace_key"]))
            except ReviewError as exc:
                if exc.code != "ACCOUNT_DISABLED":
                    raise
        return {"revision": state["revision"], "requests": [dict(r) for r in state["requests"].values()],
                "grants": [dict(g) for g in state["grants"].values()], "users": users}

    def create_project(self, actor, request_id, project_id, name, description=""):
        self._admin(actor)
        if not _identifier(project_id) or not _text(name, 100) or not _text(description, 1000, empty=True):
            _error("INVALID_REQUEST")
        command_id = _uuid(request_id)
        data = {"project_id": project_id, "name": name, "description": description}
        if self._retry(command_id, "PROJECT", actor, data):
            return {"status": "ALREADY_CREATED", "project_id": project_id, "revision": self._state()["revision"]}
        if self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_PROJECT' AND workspace_key=%s", (project_id,)).fetchone():
            _error("REQUEST_CONFLICT")
        payload = {"id": project_id, "project_id": project_id, "name": name, "description": description,
                   "tops": [], "registry_namespace": NAMESPACE}
        self.db.execute("INSERT INTO {oa}(record_id,record_type,workspace_key,project,payload) VALUES (%s,'WS_PROJECT',%s,%s,%s)",
                        (stable_uuid("workspace-v1", "WS_PROJECT", project_id), project_id, project_id, json_value(payload)))
        revision = self._append(command_id, "PROJECT", actor, data)
        return {"status": "CREATED", "project_id": project_id, "revision": revision}

    def create_block(self, actor, request_id, project_id, block_id, name):
        self._admin(actor)
        if not _identifier(project_id) or not _identifier(block_id) or not _text(name, 100):
            _error("INVALID_REQUEST")
        command_id = _uuid(request_id)
        data = {"project_id": project_id, "block_id": block_id, "name": name}
        if self._retry(command_id, "BLOCK", actor, data):
            return {"status": "ALREADY_CREATED", "block_id": block_id, "revision": self._state()["revision"]}
        if project_id not in self._state()["projects"]:
            _error("RESOURCE_NOT_FOUND")
        if self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_BLOCK' AND workspace_key=%s", (block_id,)).fetchone():
            _error("REQUEST_CONFLICT")
        payload = {"id": block_id, "project_id": project_id, "name": name, "owners": [], "registry_namespace": NAMESPACE}
        self.db.execute("INSERT INTO {oa}(record_id,record_type,workspace_key,project,payload,workspace_project_ref) "
                        "VALUES (%s,'WS_BLOCK',%s,%s,%s,%s)",
                        (stable_uuid("workspace-v1", "WS_BLOCK", block_id), block_id, project_id, json_value(payload),
                         stable_uuid("workspace-v1", "WS_PROJECT", project_id)))
        revision = self._append(command_id, "BLOCK", actor, data)
        return {"status": "CREATED", "block_id": block_id, "revision": revision}

    def request(self, actor, request_id, role, project_id, block_id, reason):
        self.require_actor(actor)
        self._role_scope(role, project_id, block_id)
        if not _text(reason, 2000):
            _error("INVALID_REQUEST")
        command_id = _uuid(request_id)
        data = {"role": role, "project_id": project_id, "block_id": block_id, "reason": reason}
        if self._retry(command_id, "REQUEST", actor, data):
            return self.snapshot(actor.user_id)
        self._registered_role_scope(role, project_id, block_id)
        state = self._state()
        if any(r["user_id"] == actor.user_id and r["role"] == role and r["project_id"] == project_id
               and r["block_id"] == block_id and r["status"] == "PENDING" for r in state["requests"].values()):
            _error("REQUEST_CONFLICT")
        if any(g["user_id"] == actor.user_id and g["role"] == role and g["project_id"] == project_id
               and g["block_id"] == block_id for g in state["grants"].values()):
            _error("REQUEST_CONFLICT")
        self._append(command_id, "REQUEST", actor, data)
        return self.snapshot(actor.user_id)

    def _registered_role_scope(self, role, project_id, block_id):
        state = self._state()
        if project_id not in state["projects"] or (role == "OWNER" and
                (block_id not in state["blocks"] or state["blocks"][block_id]["project_id"] != project_id)):
            _error("RESOURCE_NOT_FOUND")

    def decide(self, actor, request_id, command_id, decision, reason, expected_revision):
        self._admin(actor)
        request_id, command_id = _uuid(request_id), _uuid(command_id)
        if (decision not in ("APPROVE", "REJECT") or not _text(reason, 2000)
                or type(expected_revision) is not int or expected_revision < 0):
            _error("INVALID_REQUEST")
        data = {"request_id": request_id, "decision": decision, "reason": reason, "expected_revision": expected_revision}
        if self._retry(command_id, "DECISION", actor, data):
            return self.admin_snapshot(actor)
        state = self._state()
        if state["revision"] != expected_revision:
            _error("REVISION_CONFLICT")
        request = state["requests"].get(request_id)
        if request is None:
            _error("RESOURCE_NOT_FOUND")
        if request["status"] != "PENDING":
            _error("REQUEST_CONFLICT")
        # Removing a stale request is safe after account deactivation; creating
        # a new grant still requires an active, strictly validated SSO account.
        self._identity(request["user_id"], require_active=decision == "APPROVE")
        if decision == "APPROVE" and any(all(g[k] == request[k] for k in ("user_id", "role", "project_id", "block_id"))
                                         for g in state["grants"].values()):
            _error("REQUEST_CONFLICT")
        self._append(command_id, "DECISION", actor, data)
        return self.admin_snapshot(actor)

    def revoke(self, actor, grant_id, command_id, reason, expected_revision):
        self._admin(actor)
        grant_id, command_id = _uuid(grant_id), _uuid(command_id)
        if not _text(reason, 2000) or type(expected_revision) is not int or expected_revision < 0:
            _error("INVALID_REQUEST")
        data = {"grant_id": grant_id, "reason": reason, "expected_revision": expected_revision}
        if self._retry(command_id, "REVOKE", actor, data):
            return self.admin_snapshot(actor)
        state = self._state()
        if state["revision"] != expected_revision:
            _error("REVISION_CONFLICT")
        if grant_id not in state["grants"]:
            _error("RESOURCE_NOT_FOUND")
        self._append(command_id, "REVOKE", actor, data)
        return self.admin_snapshot(actor)

    def validate_scope(self, scope):
        """Validate a sealed producer scope; no user grants are implied."""
        if (not isinstance(scope, dict) or set(scope) != {"schema_version", "project_id", "block_id"}
                or type(scope["schema_version"]) is not int or scope["schema_version"] != 1
                or not _identifier(scope["project_id"]) or not _identifier(scope["block_id"])):
            _error("RESOURCE_NOT_FOUND")
        self._registered_role_scope("OWNER", scope["project_id"], scope["block_id"])
        return dict(scope)

    def _scopes(self, user_id, *, write=False):
        self._identity(user_id)
        state = self._state()
        grants = [g for g in state["grants"].values() if g["user_id"] == user_id]
        return [{"schema_version": 1, "project_id": b["project_id"], "block_id": b["id"]}
                for b in state["blocks"].values() if any(
                    g["project_id"] == b["project_id"] and
                    ((g["role"] == "TOP" and not write) or (g["role"] == "OWNER" and g["block_id"] == b["id"]))
                    for g in grants)]

    def can_read_business(self, user_id):
        return bool(self._scopes(user_id))

    def can_write_business(self, user_id):
        return bool(self._scopes(user_id, write=True))

    def read_predicate(self, user_id, alias="r"):
        if not isinstance(alias, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias) is None:
            _error("AUTHORIZATION_CONFIG")
        scopes = self._scopes(user_id)
        if not scopes:
            return "FALSE", ()
        # Exact JSON equality excludes extra/missing keys and never interprets a
        # free-text run.project as a registered project or block identifier.
        return ("((" + alias + ".payload#>>'{{bundle,run,scope,schema_version}}')='1' AND (" + alias
                + ".payload#>'{{bundle,run,scope}}') IN (" + ",".join(["%s::jsonb"] * len(scopes)) + "))",
                tuple(json_value(scope) for scope in scopes))

    def require_bundle(self, user_id, bundle_id, write=False):
        self._identity(user_id)
        row = self.db.execute("SELECT payload#>'{{bundle,run,scope}}' AS scope FROM {oa} "
                               "WHERE record_type='RECEIVED_BUNDLE' AND bundle_id=%s", (bundle_id,)).fetchone()
        if row is None:
            _error("RESOURCE_NOT_FOUND")
        scope = self.validate_scope(row["scope"])
        if scope not in self._scopes(user_id):
            _error("RESOURCE_NOT_FOUND")
        if write and scope not in self._scopes(user_id, write=True):
            _error("FORBIDDEN")
        return scope
