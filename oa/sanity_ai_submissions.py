from oa import sanity_ai_store as _ai_store
"""Small web submissions; complete input preparation belongs to the VWP worker."""
from datetime import timedelta
import re

from common.sanity_ai_execution import worker_site
from common.sanity_common import canonical_json
from common.sanity_db import lock_key, uuid_value
from common.sanity_vwp_ai import require
from oa.sanity_ai_live import record as live_record
from oa.sanity_ai_store import (
    INTENT_ACTIVE as ACTIVE, INTENT_NAMESPACE as NAMESPACE, INTENT_PREFIX as PREFIX,
    INTENT_TERMINAL as TERMINAL, JOB_ACTIVE, ensure_not_deleted,
    error_code, job_lock, parse_timestamp, report_header as header, worker_load,
)
from oa.sanity_ws_config import verify_ws_config_row


class Submissions:
    def __init__(self, service):
        self.service, self.db = service, service.db

    def load(self, identity, *, required=True):
        identity = str(uuid_value(identity))
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                               "ORDER BY workspace_key", (PREFIX + identity + ".%",)).fetchall()
        if not rows:
            require(not required, "AI_JOB_NOT_FOUND")
            return None
        previous = None
        transitions = {None: {"QUEUED"}, "QUEUED": {"PREPARING", "CANCELLED"},
            "PREPARING": {"PREPARING", "PREPARED", "FAILED", "STALE", "CANCELLED"}}
        for row in rows:
            event = verify_ws_config_row(row, error_code="AI_SUBMISSION_CORRUPT")
            require({"namespace", "id", "sequence", "at", "status", "data"} <= set(event)
                    and event["namespace"] == NAMESPACE and event["id"] == identity
                    and event["status"] in transitions.get(previous["status"] if previous else None, set())
                    and type(event["sequence"]) is int and event["sequence"] == (previous["sequence"] + 1 if previous else 1)
                    and row["workspace_key"] == PREFIX + identity + "." + f"{event['sequence']:06d}",
                    "AI_SUBMISSION_CORRUPT")
            data = event["data"]
            required_keys = {"command", "target", "snapshot", "model", "config_sha256", "created_at", "worker_site", "attempts"}
            require(isinstance(data, dict) and required_keys <= set(data) <= required_keys | {"lease_until", "error", "connection_label", "cancelled_by", "credential_pin"}
                    and isinstance(data["command"], dict) and data["command"].get("request_id") == identity and data["worker_site"] == "vwp"
                    and type(data["attempts"]) is int and 0 <= data["attempts"] <= 2, "AI_SUBMISSION_CORRUPT")
            command = data["command"]
            command_keys = {"user_id", "block_id", "version_id", "report_id", "expected_revision", "request_id", "group_id", "expected_cluster_revision"}
            require(command_keys <= set(command) <= command_keys | {"model_id", "connection_id"}
                    and all(isinstance(command[k], str) and 1 <= len(command[k]) <= 100 for k in
                            ("user_id", "block_id", "version_id", "report_id", "request_id"))
                    and type(command["expected_revision"]) is int and command["expected_revision"] >= 0
                    and data["target"] == self.service._target(command["report_id"], command["group_id"])
                    and isinstance(data["model"], str) and 1 <= len(data["model"]) <= 200
                    and isinstance(data["config_sha256"], str) and re.fullmatch('[0-9a-f]{64}', data["config_sha256"])
                    and isinstance(data["snapshot"], dict) and set(data["snapshot"]) == {"record_id", "snapshot_xid", "version", "updated_at"}, "AI_SUBMISSION_CORRUPT")
            parse_timestamp(event["at"]); parse_timestamp(data["created_at"])
            if 'credential_pin' in data:
                pin = data['credential_pin']
                require(isinstance(pin, dict) and set(pin) == {'revision', 'scope'}
                    and type(pin['revision']) is int and pin['revision'] >= 1
                    and isinstance(pin['scope'], str) and re.fullmatch(r'[0-9a-f]{64}', pin['scope'])
                    and 'connection_id' in command, 'AI_SUBMISSION_CORRUPT')
            if "lease_until" in data: parse_timestamp(data["lease_until"])
            if event["status"] == "PREPARING": require("lease_until" in data and data["attempts"] >= 1, "AI_SUBMISSION_CORRUPT")
            if previous:
                require(all(data[k] == previous["data"][k] for k in
                    ("command", "target", "snapshot", "model", "config_sha256", "created_at", "worker_site")), "AI_SUBMISSION_CORRUPT")
                require(data.get('credential_pin') == previous['data'].get('credential_pin'), 'AI_SUBMISSION_CORRUPT')
                require(parse_timestamp(event["at"]) >= parse_timestamp(previous["at"])
                    and data["attempts"] == previous["data"]["attempts"] + (1 if event["status"] == "PREPARING" else 0), "AI_SUBMISSION_CORRUPT")
            else: require(data["attempts"] == 0, "AI_SUBMISSION_CORRUPT")
            previous = event
        return previous

    def append(self, previous, status, data):
        event = {"namespace": NAMESPACE, "id": data["command"]["request_id"],
            "sequence": previous["sequence"] + 1 if previous else 1,
            "at": _ai_store.db_now(self.db).isoformat(),
            "status": status, "data": data}
        require(len(canonical_json(event)) <= 32768, "AI_INPUT_LIMIT")
        key = PREFIX + event["id"] + "." + f"{event['sequence']:06d}"
        self.service.ws.put("WS_CONFIG", key, event)
        # Canonical preparation writes its own PENDING snapshot atomically.
        if status != "PREPARED":
            live_record(self.service, event["id"], data["command"], data["target"], self.public(event))
        return event

    @staticmethod
    def public(event):
        data = event["data"]
        return {"id": event["id"], "status": "PENDING" if event["status"] in ACTIVE else event["status"],
            "preparation_phase": event["status"], "error": data.get("error"), "model": data["model"],
            "created_at": data["created_at"], "finished_at": event["at"] if event["status"] in TERMINAL else None,
            "expected_revision": data["command"]["expected_revision"], "input_case_count": 0, "event_id": None,
            "model_called": False, "model_attempted": False, "worker_site": "vwp",
            "merge_iteration": 0, "current_group_count": 1 if data['command']['group_id'] is not None else None,
            **({"connection_id": data["command"]["connection_id"], "connection_label": data["connection_label"]}
               if "connection_id" in data["command"] else {})}

    def latest(self, *, target=None, pending=False):
        query = "SELECT payload FROM (SELECT DISTINCT ON (payload->>'id') payload,created_at,workspace_key FROM {oa} " \
            "WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s AND payload->>'namespace'=%s " \
            "ORDER BY payload->>'id',workspace_key DESC) latest WHERE TRUE "
        params = [PREFIX + "%", NAMESPACE]
        if target is not None:
            query += "AND payload->'data'->>'target'=%s "; params.append(target)
        if pending:
            query += "AND (payload->>'status'='QUEUED' OR (payload->>'status'='PREPARING' " \
                     "AND (payload->'data'->>'lease_until')::timestamptz<=clock_timestamp())) ORDER BY (payload->'data'->>'created_at')::timestamptz,workspace_key LIMIT 1"
        else: query += "ORDER BY created_at DESC,workspace_key DESC LIMIT 1"
        row = self.db.execute(query, params).fetchone()
        return worker_load(self.service, row["payload"]["id"], self.load) if row else None

    def submit(self, user_id, block_id, version_id, report_id, *, expected_revision, request_id,
               group_id=None, expected_cluster_revision=None, model_id=None, connection_id=None):
        identity = str(uuid_value(request_id))
        require(type(expected_revision) is int and expected_revision >= 0, "INVALID_REQUEST")
        require((group_id is None and expected_cluster_revision is None) or
                (isinstance(group_id, str) and type(expected_cluster_revision) is int and expected_cluster_revision >= 1), "INVALID_REQUEST")
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
            "expected_revision": expected_revision, "request_id": identity, "group_id": group_id,
            "expected_cluster_revision": expected_cluster_revision}
        if model_id is not None: command["model_id"] = model_id
        if connection_id is not None: command["connection_id"] = str(uuid_value(connection_id))
        require(all(isinstance(command[k], str) and 1 <= len(command[k]) <= 100 for k in
                    ("user_id", "block_id", "version_id", "report_id")), "INVALID_REQUEST")
        header(self.service, command, write=True)
        job_lock(self.service, identity)
        ensure_not_deleted(self.service, identity)
        previous = self.load(identity, required=False)
        if previous is not None:
            require(previous["data"]["command"] == command, "REQUEST_CONFLICT")
            canonical = self.service._load(identity, required=False)
            return self.service.public(canonical) if canonical else self.public(previous)
        canonical = self.service._load(identity, required=False)
        if canonical:
            require(canonical["data"]["command"] == command, "REQUEST_CONFLICT")
            return self.service.public(canonical)
        target = self.service._target(report_id, group_id)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(PREFIX + target),))
        active = self.service._latest(target=target)
        intent = self.latest(target=target)
        require(not active or active["status"] not in JOB_ACTIVE, "AI_JOB_ACTIVE")
        require(not intent or intent["status"] not in ACTIVE, "AI_JOB_ACTIVE")
        value = header(self.service, command, write=True)
        require(value["ready"] and value["can_review"] and value["group_ready"], "CLUSTER_NOT_CONFIRMED" if group_id else "CLUSTER_BLOCKED")
        model = self.service._description(model_id, command.get("connection_id"))
        require(model["enabled"], "AI_NOT_CONFIGURED")
        if connection_id: self.service.connections.authorize_user(command["connection_id"], user_id)
        pin = self.service._vwp_credential_pin(user_id, command.get('connection_id'))
        if self.service._personal_tokens(connection_id=command.get("connection_id")):
            self.service._user_token(user_id, command.get("connection_id"))
        data = {"command": command, "target": target, "snapshot": value["snapshot"], "model": model["model"],
            "config_sha256": model["config_sha256"], "created_at": _ai_store.db_now(self.db).isoformat(), "worker_site": "vwp", "attempts": 0}
        if pin is not None: data['credential_pin'] = pin
        if connection_id: data["connection_label"] = model["connection_label"]
        return self.public(self.append(None, "QUEUED", data))


def prepare_one(open_service, *, identity=None):
    if worker_site() != "vwp":
        return None
    with open_service() as service:
        store = Submissions(service)
        event = store.load(identity, required=False) if identity is not None else store.latest(pending=True)
        if event is None: return None
        service._lock(event["id"]); event = worker_load(service, event["id"], store.load)
        if event is None: return None
        if event["status"] not in ACTIVE: return None
        if event["status"] == "PREPARING" and parse_timestamp(event["data"]["lease_until"]) > _ai_store.db_now(service.db): return None
        if event["data"]["attempts"] >= 2:
            return store.public(store.append(event, "FAILED", {**event["data"], "error": "AI_PREPARATION_LIMIT"}))
        claimed = store.append(event, "PREPARING", {**event["data"], "attempts": event["data"]["attempts"] + 1,
            "lease_until": (_ai_store.db_now(service.db) + timedelta(seconds=300)).isoformat()})
    try:
        with open_service() as service:
            service._lock(claimed["id"]); store = Submissions(service); event = worker_load(service, claimed["id"], store.load)
            if event is None: return None
            if event["sequence"] != claimed["sequence"]: return None
            data = event["data"]; command = data["command"]
            require(header(service, command, write=True)["snapshot"] == data["snapshot"], "AI_INPUT_STALE")
            service._check_credential_pin(event)
            require(service._description(data["model"], command.get("connection_id"))["config_sha256"] == data["config_sha256"], "AI_CONFIG_CHANGED")
            kwargs = {k: v for k, v in command.items() if k not in ("user_id", "block_id", "version_id", "report_id", "model_id")}
            result = service.enqueue(command["user_id"], command["block_id"], command["version_id"], command["report_id"],
                **kwargs, model_id=data["model"], _worker_site="vwp", _requested_at=data["created_at"],
                _credential_pin=data.get('credential_pin'))
            store.append(event, "PREPARED", data)
            return result
    except Exception as error:
        code = "AI_RESOURCE_LIMIT" if isinstance(error, MemoryError) else error_code(error)
        status = "STALE" if code in {"AI_INPUT_STALE", "AI_CONFIG_CHANGED", "AI_CONNECTION_UNAVAILABLE", "AI_CREDENTIAL_CHANGED", "REVISION_CONFLICT", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND", "RESOURCE_NOT_FOUND"} else "FAILED"
        with open_service() as service:
            service._lock(claimed["id"]); store = Submissions(service); event = worker_load(service, claimed["id"], store.load)
            if event is None: return None
            canonical = service._load(claimed["id"], required=False)
            if canonical: return service.public(canonical)
            if event["status"] != "PREPARING" or event["sequence"] != claimed["sequence"]: return store.public(event)
            return store.public(store.append(event, status, {**event["data"], "error": code}))
