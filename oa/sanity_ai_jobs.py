"""Durable, bounded OA inference jobs in an immutable WS_CONFIG event ledger.

The API only captures work. ``run_once`` commits RUNNING before network I/O and
uses fresh authorization transactions to save and apply its result. A crashed
or timed-out call is never replayed. This is the whole-input first-cycle path;
it does not partition large reports or silently reduce model coverage.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4

from common.sanity_ai_contracts import validate_review_v2
from common.sanity_clustering import check
from common.sanity_clustering_advice import evidence, identity_fields_valid, validate_clustering_advice
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_config import load_environment
from common.sanity_db import Database, lock_key, uuid_value
from oa.sanity_access import access_lock
from oa.sanity_ai_cases import CaseRetriever
from oa.sanity_ai_tokens import UserTokenStore
from oa.sanity_ai_model import OAAdvisor, OASettings, PROVIDER
from oa.sanity_workspace import record_id
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore

NAMESPACE, PREFIX = "oa-ai-v1", "ai1.job."
ACTIVE = frozenset(("PENDING", "RUNNING", "RESULT_READY"))
TERMINAL = frozenset(("COMPLETED", "FAILED", "STALE", "UNKNOWN", "CANCELLED"))
TRANSITIONS = {None: {"PENDING"}, "PENDING": {"RUNNING", "FAILED", "STALE", "CANCELLED"},
               "RUNNING": {"RESULT_READY", "FAILED", "STALE", "UNKNOWN", "CANCELLED"},
               "RESULT_READY": {"COMPLETED", "FAILED", "STALE", "CANCELLED"}}
MAX_EVENT_BYTES = 64 * 1024 * 1024
REVIEW_LOCK = lock_key("workspace-review-write")
_UNSET = object()
_NULL_COLUMNS = ("project", "run_id", "bundle_id", "review_unit_id", "review_revision", "request_id",
    "entity_key", "classification_revision", "payload_bytes", "payload_sha256", "workspace_project_ref",
    "workspace_block_ref", "workspace_version_ref", "workspace_review_report_ref", "workspace_review_version_ref",
    "workspace_review_event_ref", "workspace_pipeline_report_ref", "workspace_pipeline_version_ref",
    "workspace_pipeline_event_ref", "workspace_pipeline_cluster_ref")


def _now():
    return datetime.now(timezone.utc)


def _db_now(db):
    return db.execute("SELECT clock_timestamp() AS now").fetchone()["now"]


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        check(parsed.tzinfo is not None, "AI_JOB_CORRUPT")
        return parsed
    except (ValueError, TypeError):
        raise ReviewError("AI_JOB_CORRUPT", "Invalid job timestamp") from None


def _error_code(error):
    code = getattr(error, "code", "AI_INTERNAL_ERROR")
    return code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) else "AI_INTERNAL_ERROR"


def _public(event):
    data = event["data"]
    return {"id": event["job_id"], "status": event["status"], "error": data.get("error"),
            "model": data["model"], "created_at": data["created_at"], "finished_at": data.get("finished_at"),
            "expected_revision": data["command"]["expected_revision"],
            "input_case_count": len(data["pins"]["cases"]), "event_id": data.get("event_id"),
            "model_called": data.get("model_called", False), "model_attempted": data.get("model_attempted", False)}


def _source_pin(context):
    return {"scope": context["scope"], "source_sha256": context["source_sha256"],
            "report_id": context["report"]["id"], "version_id": context["report"]["version_id"],
            "group_id": context.get("group_id"), "cluster_revision": context.get("cluster_revision"),
            "cluster_event_id": context.get("cluster_event_id"),
            "membership_sha256": context.get("membership_sha256"),
            "clustering_rationale_sha256": digest(context.get("clustering_rationale"))}


def _case_pins(cases):
    return [{**{key: case[key] for key in ("id", "report_id", "revision", "source_sha256")},
             **({"group_id": case["group_id"]} if case.get("group_id") else {}), "sha256": digest(case)}
            for case in cases]


def _validate_data(event):
    """Fail closed on malformed application-owned ledger links, before use."""
    data = event.get("data")
    required = {"command", "target", "pins", "retrieval", "model", "created_at", "model_called"}
    optional = {"model_attempted", "call_token", "started_at", "deadline", "result", "error", "finished_at", "event_id", "cancelled_by"}
    check(isinstance(data, dict) and required <= set(data) <= required | optional, "AI_JOB_CORRUPT")
    command, pins = data["command"], data["pins"]
    check(isinstance(command, dict) and set(command) == {"user_id", "block_id", "version_id", "report_id", "expected_revision",
        "request_id", "group_id", "expected_cluster_revision"}, "AI_JOB_CORRUPT")
    check(all(isinstance(command[key], str) and 1 <= len(command[key]) <= 100 for key in
              ("user_id", "block_id", "version_id", "report_id", "request_id"))
          and command["request_id"] == event["job_id"] and type(command["expected_revision"]) is int
          and command["expected_revision"] >= 0, "AI_JOB_CORRUPT")
    group = command["group_id"]
    check((group is None and command["expected_cluster_revision"] is None) or
          (isinstance(group, str) and 1 <= len(group) <= 100 and type(command["expected_cluster_revision"]) is int
           and command["expected_cluster_revision"] >= 1), "AI_JOB_CORRUPT")
    check(data["target"] == digest([command["report_id"], group]) and isinstance(data["retrieval"], dict)
          and isinstance(data["model"], str) and 1 <= len(data["model"]) <= 200
          and (data["model_called"] is None or type(data["model_called"]) is bool)
          and type(data.get("model_attempted", False)) is bool, "AI_JOB_CORRUPT")
    check(isinstance(pins, dict) and set(pins) == {"source", "cases", "criteria_sha256", "config_sha256",
          "identity_fields", "case_budget", "stage"} and pins["stage"] == ("REVIEW" if group is not None else "CLUSTER")
          and isinstance(pins["source"], dict) and isinstance(pins["cases"], list)
          and len(pins["cases"]) <= 100 and all(isinstance(case, dict) for case in pins["cases"])
          and isinstance(pins["identity_fields"], list) and type(pins["case_budget"]) is int
          and 1 <= pins["case_budget"] <= 1024 * 1024, "AI_JOB_CORRUPT")
    source = pins["source"]
    check(source.get("report_id") == command["report_id"] and source.get("version_id") == command["version_id"]
          and source.get("group_id") == group and isinstance(source.get("scope"), dict)
          and source["scope"].get("block_id") == command["block_id"], "AI_JOB_CORRUPT")
    for key in ("config_sha256", "criteria_sha256"):
        check(isinstance(pins[key], str) and re.fullmatch(r"[0-9a-f]{64}", pins[key]), "AI_JOB_CORRUPT")
    _timestamp(data["created_at"])
    if "error" in data:
        check(_error_code(ReviewError(data["error"], "")) == data["error"], "AI_JOB_CORRUPT")
    if "call_token" in data:
        check(isinstance(data["call_token"], str) and re.fullmatch(r"[0-9a-f-]{36}", data["call_token"])
              and _timestamp(data.get("deadline")) > _timestamp(data.get("started_at")), "AI_JOB_CORRUPT")
    if event["status"] in ("RUNNING", "RESULT_READY", "COMPLETED"):
        check("call_token" in data, "AI_JOB_CORRUPT")
    if event["status"] in ("RESULT_READY", "COMPLETED"):
        check(isinstance(data.get("result"), dict) and set(data["result"]) == {"advice", "metadata"}
              and all(isinstance(value, dict) for value in data["result"].values()) and data["model_called"] is True, "AI_JOB_CORRUPT")
    if event["status"] == "COMPLETED":
        check(data.get("event_id") == event["job_id"], "AI_JOB_CORRUPT")
    if event["status"] in TERMINAL:
        _timestamp(data.get("finished_at"))


class AIJobService:
    """Caller owns a READ COMMITTED transaction and the shared access lock."""

    def __init__(self, db, ws, advisor=None, criteria_provider=None):
        self.db, self.ws = db, ws
        if advisor is None:
            settings = OASettings.from_env(load_environment())
            advisor = OAAdvisor(settings) if settings.enabled else None
        self.advisor = advisor
        if criteria_provider is None:
            from oa.sanity_ai_criteria import load_criteria
            criteria_provider = load_criteria
        self.criteria_provider = criteria_provider

    def _description(self):
        if self.advisor is None:
            return {"enabled": False, "model": "", "provider": PROVIDER}
        value = self.advisor.describe()
        check(value.get("provider") == PROVIDER and value.get("enabled") is True
              and isinstance(value.get("config_sha256"), str), "AI_CONFIG")
        return value

    def _personal_tokens(self):
        return getattr(getattr(self.advisor, "settings", None), "token_source", "server") == "user"

    def _user_token(self, user_id):
        return UserTokenStore(self.db).get_token(user_id)

    def _store(self, command):
        if command.get("group_id") is not None:
            return WorkspaceGroupReviewStore(self.db, command["group_id"], command.get("expected_cluster_revision"),
                                             ws=self.ws, criteria_provider=self.criteria_provider)
        return WorkspaceClusteringStore(self.db, ws=self.ws)

    def _context(self, command, *, write):
        store = self._store(command)
        context = store.context(command["user_id"], command["block_id"], command["version_id"], command["report_id"], write=write)
        from oa.sanity_preserved_text import enrich
        return store, enrich(self.db, context)

    def _lock(self, job_id):
        # Match human edits' lock order. RBAC's shared lock is taken by caller.
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (REVIEW_LOCK,))
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(PREFIX + job_id),))

    def _load(self, job_id, *, required=True):
        job_id = str(uuid_value(job_id, "job_id"))
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                               "ORDER BY workspace_key", (PREFIX + job_id + ".%",)).fetchall()
        previous = None
        for index, row in enumerate(rows, 1):
            event = row["payload"]
            key = PREFIX + job_id + "." + str(index).zfill(4)
            check(isinstance(event, dict) and set(event) == {"namespace", "schema_version", "job_id", "sequence",
                "previous_sha256", "created_at", "status", "data", "sha256"}, "AI_JOB_CORRUPT")
            check(row["workspace_key"] == key and row["record_id"] == record_id("WS_CONFIG", key)
                  and row["state"] == {} and row["version"] == 0 and row["status"] == "RECORDED"
                  and row["updated_at"] == row["created_at"] and all(row.get(k) is None for k in _NULL_COLUMNS), "AI_JOB_CORRUPT")
            check(event["namespace"] == NAMESPACE and type(event["schema_version"]) is int and event["schema_version"] == 1
                  and event["job_id"] == job_id and type(event["sequence"]) is int and event["sequence"] == index
                  and event["previous_sha256"] == (previous["sha256"] if previous else None)
                  and event["sha256"] == digest({k: v for k, v in event.items() if k != "sha256"})
                  and event["status"] in TRANSITIONS.get(previous["status"] if previous else None, set()), "AI_JOB_CORRUPT")
            _timestamp(event["created_at"])
            _validate_data(event)
            if previous:
                check(_timestamp(event["created_at"]) >= _timestamp(previous["created_at"]), "AI_JOB_CORRUPT")
                for key in ("command", "target", "pins", "retrieval", "model", "created_at"):
                    check(event["data"][key] == previous["data"][key], "AI_JOB_CORRUPT")
            check(event["data"]["command"]["request_id"] == job_id, "AI_JOB_CORRUPT")
            previous = event
        check(previous is not None or not required, "NOT_FOUND")
        return previous

    def _append(self, previous, status, data, *, now=None):
        now = now or _db_now(self.db)
        check(status in TRANSITIONS.get(previous["status"] if previous else None, set()), "AI_JOB_STATE")
        event = {"namespace": NAMESPACE, "schema_version": 1, "job_id": data["command"]["request_id"],
                 "sequence": previous["sequence"] + 1 if previous else 1,
                 "previous_sha256": previous["sha256"] if previous else None,
                 "created_at": now.isoformat(), "status": status, "data": deepcopy(data)}
        if status in TERMINAL:
            event["data"]["finished_at"] = now.isoformat()
        _validate_data(event)
        event["sha256"] = digest(event)
        check(len(canonical_json(event)) <= MAX_EVENT_BYTES, "AI_OUTPUT_LIMIT")
        key = PREFIX + event["job_id"] + "." + str(event["sequence"]).zfill(4)
        self.ws.put("WS_CONFIG", key, event)
        return event

    def _latest(self, *, target=None, work=False, now=None):
        query = "SELECT payload FROM (SELECT DISTINCT ON (payload->>'job_id') payload,created_at,workspace_key " \
            "FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s AND payload->>'namespace'=%s " \
            "ORDER BY payload->>'job_id',workspace_key DESC) jobs WHERE TRUE "
        params = [PREFIX + "%", NAMESPACE]
        if target is not None:
            query += "AND payload->'data'->>'target'=%s "
            params.append(target)
        if work:
            query += "AND (payload->>'status' IN ('PENDING','RESULT_READY') OR (payload->>'status'='RUNNING' " \
                "AND (payload->'data'->>'deadline')::timestamptz<=COALESCE(%s::timestamptz,clock_timestamp()))) ORDER BY created_at,workspace_key LIMIT 1"
            params.append(now)
        else:
            query += "ORDER BY created_at DESC,workspace_key DESC LIMIT 1"
        row = self.db.execute(query, tuple(params)).fetchone()
        return self._load(row["payload"]["job_id"]) if row else None

    @staticmethod
    def _target(report_id, group_id):
        return digest([report_id, group_id])

    def capability(self, user_id, block_id, version_id, report_id, group_id=None, expected_cluster_revision=None):
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id,
                   "report_id": report_id, "group_id": group_id, "expected_cluster_revision": expected_cluster_revision}
        _, context = self._context(command, write=False)
        model = self._description()
        latest = self._latest(target=self._target(report_id, group_id))
        supported = bool(context["ready"] and context["can_review"] and
                         (context.get("group_evidence") if group_id is not None else evidence(context)))
        credential = {}
        if self._personal_tokens():
            error_code = None
            try:
                self._user_token(user_id)
            except ReviewError as error:
                if error.code not in {"AI_USER_TOKEN_REQUIRED", "AI_USER_TOKEN_EXPIRED", "AI_TOKEN_UNAVAILABLE",
                                      "AI_TOKEN_STORE_NOT_CONFIGURED", "AI_PERSONAL_TOKEN_DISABLED"}:
                    raise
                error_code = error.code
            credential = {"credential_required": True, "credential_ready": error_code is None,
                          "credential_error": error_code}
        return {"available": bool(model["enabled"] and supported and credential.get("credential_ready", True)), "supported": supported,
                "enabled": model["enabled"], "model": model["model"], "remaining": None,
                "provider": PROVIDER, "synthetic_only": False, "job": _public(latest) if latest else None, **credential}

    def enqueue(self, user_id, block_id, version_id, report_id, *, expected_revision, request_id,
                group_id=None, expected_cluster_revision=None):
        check(type(expected_revision) is int and expected_revision >= 0, "INVALID_REQUEST")
        check(group_id is None or isinstance(group_id, str) and 1 <= len(group_id) <= 100, "INVALID_REQUEST")
        check(group_id is None or type(expected_cluster_revision) is int and expected_cluster_revision >= 1, "INVALID_REQUEST")
        check(group_id is not None or expected_cluster_revision is None, "INVALID_REQUEST")
        request_id = str(uuid_value(request_id))
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
                   "expected_revision": expected_revision, "request_id": request_id,
                   "group_id": group_id, "expected_cluster_revision": expected_cluster_revision}
        self._lock(request_id)
        store, context = self._context(command, write=True)
        previous = self._load(request_id, required=False)
        if previous:
            check(previous["data"]["command"] == command, "REQUEST_CONFLICT")
            return _public(previous)
        model = self._description()
        check(model["enabled"], "AI_NOT_CONFIGURED")
        if self._personal_tokens():
            self._user_token(user_id)
        check(context["ready"] and context["can_review"], "CLUSTER_NOT_CONFIRMED" if group_id else "CLUSTER_BLOCKED")
        _, revision = store.current(report_id)
        check(revision == expected_revision, "REVISION_CONFLICT")
        if group_id is not None:
            check(context["cluster_revision"] == expected_cluster_revision, "REVISION_CONFLICT")
        target = self._target(report_id, group_id)
        latest = self._latest(target=target)
        check(not latest or latest["status"] not in ACTIVE, "AI_JOB_ACTIVE")
        stage = "REVIEW" if group_id is not None else "CLUSTER"
        case_budget = max(1, min(1024 * 1024, model.get("max_input_bytes", 1024 * 1024) // 3))
        selected = CaseRetriever(self.db, workspace=self.ws).select(context, stage=stage, max_bytes=case_budget)
        criteria = self.criteria_provider(context) if group_id is not None else []
        fields = []
        if group_id is None:
            old, _ = store.current(report_id)
            fields = identity_fields_valid(old["identity_fields"] if old else ["object", "check_point", "mode"], evidence(context))
        pins = {"source": _source_pin(context), "cases": _case_pins(selected["cases"]),
                "criteria_sha256": digest(criteria), "config_sha256": model["config_sha256"],
                "identity_fields": fields, "case_budget": case_budget, "stage": stage}
        now = _db_now(self.db)
        data = {"command": command, "target": target, "pins": pins, "retrieval": selected["metadata"],
                "model": model["model"], "created_at": now.isoformat(), "model_called": False}
        return _public(self._append(None, "PENDING", data, now=now))

    def status(self, user_id, job_id):
        event = self._load(job_id)
        command = {**event["data"]["command"], "user_id": user_id}
        self.ws.report(user_id, command["block_id"], command["version_id"], command["report_id"], hydrate_source=False)
        return _public(event)

    def cancel(self, user_id, job_id):
        job_id = str(uuid_value(job_id, "job_id"))
        self._lock(job_id)
        event = self._load(job_id)
        command = event["data"]["command"]
        self.ws.access(user_id, command["block_id"], write=True)
        self.ws.report(user_id, command["block_id"], command["version_id"], command["report_id"], hydrate_source=False)
        if event["status"] in TERMINAL:
            return _public(event)
        return _public(self._append(event, "CANCELLED", {**event["data"], "cancelled_by": user_id}))

    def _inputs(self, event):
        data, model = event["data"], self._description()
        pins, command = data["pins"], data["command"]
        check(model.get("enabled") and model.get("config_sha256") == pins["config_sha256"], "AI_CONFIG_CHANGED")
        store, context = self._context(command, write=True)
        check(context["ready"] and context["can_review"] and _source_pin(context) == pins["source"], "AI_INPUT_STALE")
        _, revision = store.current(command["report_id"])
        check(revision == command["expected_revision"], "REVISION_CONFLICT")
        selected = CaseRetriever(self.db, workspace=self.ws).resolve_references(context, pins["cases"],
                    stage=pins["stage"], max_bytes=pins["case_budget"])
        check(not selected["stale_ids"] and not selected["over_budget_ids"] and
              _case_pins(selected["cases"]) == pins["cases"], "AI_CASES_STALE")
        criteria = self.criteria_provider(context) if command["group_id"] is not None else []
        check(digest(criteria) == pins["criteria_sha256"], "AI_CRITERIA_STALE")
        if self._personal_tokens():
            _, credential_revision = self._user_token(command["user_id"])
            if "result" in data:
                check(data["result"]["metadata"].get("credential_revision") == credential_revision,
                      "AI_CREDENTIAL_CHANGED")
        return store, context, selected["cases"], criteria

    def _failed(self, event, error, *, status="STALE", called=_UNSET, now=None):
        changes = {"error": _error_code(error)}
        if called is not _UNSET:
            changes["model_called"] = called
            changes["model_attempted"] = called is not False
        return self._append(event, status, {**event["data"], **changes}, now=now)

    def _apply(self, event):
        try:
            store, context, cases, criteria = self._inputs(event)
        except ReviewError as error:
            return self._failed(event, error)
        data, command = event["data"], event["data"]["command"]
        result = data["result"]
        kwargs = {"expected_revision": command["expected_revision"], "request_id": command["request_id"],
                  "advice": result["advice"], "cases": cases, "metadata": result["metadata"],
                  "retrieval": data["retrieval"]}
        if command["group_id"] is not None:
            kwargs["criteria"] = criteria
        else:
            kwargs["identity_fields"] = data["pins"]["identity_fields"]
        # Result publication and COMPLETED are one transaction. A crash rolls
        # both back and leaves RESULT_READY available without another API call.
        try:
            with self.db.transaction():
                applied = store.apply_ai(command["user_id"], command["block_id"], command["version_id"], command["report_id"], **kwargs)
        except ReviewError as error:
            stale = error.code in {"REVISION_CONFLICT", "PROPOSAL_STALE", "CLUSTER_NOT_CONFIRMED", "AI_CASES_STALE", "AI_CRITERIA_STALE"}
            return self._failed(event, error, status="STALE" if stale else "FAILED")
        event_id = applied.get("id") or applied.get("event", {}).get("id")
        check(event_id == command["request_id"], "AI_JOB_CORRUPT")
        return self._append(event, "COMPLETED", {**data, "event_id": event_id})


@contextmanager
def _transaction(db_factory, workspace_factory, advisor, criteria_provider):
    # Each phase obtains a new connection: nested transaction/savepoint commit
    # is never mistaken for the durable boundary preceding model dispatch.
    with db_factory() as db:
        status = getattr(db.conn, "get_transaction_status", None)
        check(status is None or status() == 0, "AI_TRANSACTION_ACTIVE")
        with db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
            access_lock(db)
            yield AIJobService(db, workspace_factory(db), advisor=advisor, criteria_provider=criteria_provider)


def run_once(db_factory, workspace_factory, advisor, criteria_provider=None, *, now=None):
    """Process at most one queued job; never keep a connection across HTTP.

    ``db_factory`` must yield a fresh OA Database; ``workspace_factory`` must
    supply a fresh ProductionWorkspaceStore in production. They are injected
    in tests so only isolated synthetic PostgreSQL and model transports run.
    """
    dispatch = None
    with _transaction(db_factory, workspace_factory, advisor, criteria_provider) as service:
        event = service._latest(work=True, now=now)
        if event is None:
            return None
        service._lock(event["job_id"])
        event = service._load(event["job_id"])
        if event["status"] == "RESULT_READY":
            return _public(service._apply(event))
        if event["status"] == "RUNNING":
            if _timestamp(event["data"]["deadline"]) <= (now or _db_now(service.db)):
                return _public(service._failed(event, ReviewError("AI_OUTCOME_UNKNOWN", "Call completion is unknown"),
                                               status="UNKNOWN", called=None, now=now))
            return _public(event)
        if event["status"] != "PENDING":
            return _public(event)
        try:
            _, context, cases, criteria = service._inputs(event)
            dispatch_advisor, credential_revision = advisor, None
            if service._personal_tokens():
                token, credential_revision = service._user_token(event["data"]["command"]["user_id"])
                dispatch_advisor = advisor.with_user_token(token)
        except ReviewError as error:
            return _public(service._failed(event, error, now=now))
        started = now or _db_now(service.db)
        deadline = started + timedelta(seconds=service._description().get("timeout", 120) + 60)
        event = service._append(event, "RUNNING", {**event["data"], "call_token": str(uuid4()),
                   "model_attempted": True, "model_called": None,
                   "started_at": started.isoformat(), "deadline": deadline.isoformat()}, now=started)
        dispatch = {"event": event, "context": context, "cases": cases, "criteria": criteria,
                    "advisor": dispatch_advisor, "credential_revision": credential_revision}
    # All DB handles and transaction-level authorization/review locks are gone.
    try:
        event, context, cases, criteria = (dispatch[k] for k in ("event", "context", "cases", "criteria"))
        stage = event["data"]["pins"]["stage"]
        if stage == "CLUSTER":
            result = dispatch["advisor"].cluster(context, cases, event["data"]["pins"]["identity_fields"])
            advice = validate_clustering_advice(result["advice"], context, cases, require_rationales=True)
        else:
            result = dispatch["advisor"].review(context, cases, criteria)
            advice = validate_review_v2(result["advice"], context, cases, criteria)
        check(isinstance(result["metadata"], dict) and result["metadata"].get("model_called") is True
              and result["metadata"].get("provider") == PROVIDER, "INVALID_AI_RESPONSE")
        from common.sanity_preserved_ai import receipt
        receipt(result["metadata"], advice, context)
        result = {"advice": advice, "metadata": result["metadata"]}
        if dispatch["credential_revision"] is not None:
            result["metadata"] = {**result["metadata"], "credential_revision": dispatch["credential_revision"]}
        failure = None
    except Exception as error:
        result, failure = None, error
    with _transaction(db_factory, workspace_factory, advisor, criteria_provider) as service:
        service._lock(event["job_id"])
        current = service._load(event["job_id"])
        if current["status"] != "RUNNING" or current["data"].get("call_token") != event["data"]["call_token"]:
            return _public(current)  # A late response never revives a terminal job.
        if _timestamp(current["data"]["deadline"]) <= _db_now(service.db):
            return _public(service._failed(current, ReviewError("AI_OUTCOME_UNKNOWN", "Call completion exceeded its deadline"),
                                           status="UNKNOWN", called=True))
        if failure is not None:
            code = _error_code(failure)
            predispatch = code in {"AI_INPUT_LIMIT", "AI_NOT_CONFIGURED", "AI_CONFIG", "INVALID_AI_INPUT", "INVALID_AI_CRITERIA"}
            unknown = code == "AI_OUTCOME_UNKNOWN" or not isinstance(failure, ReviewError)
            called = getattr(failure, "model_called", not predispatch)
            called = called if type(called) is bool else None
            return _public(service._failed(current, failure, status="UNKNOWN" if unknown else "FAILED", called=called))
        try:
            service._inputs(current)
            if dispatch["credential_revision"] is not None:
                _, revision = service._user_token(event["data"]["command"]["user_id"])
                check(revision == dispatch["credential_revision"], "AI_CREDENTIAL_CHANGED")
        except ReviewError as error:
            return _public(service._failed(current, error, called=True))
        try:
            service._append(current, "RESULT_READY", {**current["data"], "model_called": True, "result": result})
        except ReviewError as error:
            if error.code != "AI_OUTPUT_LIMIT":
                raise
            return _public(service._failed(current, error, status="FAILED", called=True))
    with _transaction(db_factory, workspace_factory, advisor, criteria_provider) as service:
        service._lock(event["job_id"])
        current = service._load(event["job_id"])
        return _public(service._apply(current)) if current["status"] == "RESULT_READY" else _public(current)
