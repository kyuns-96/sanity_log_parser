"""Durable, bounded OA inference jobs in an immutable WS_CONFIG event ledger.

The API only captures work. ``run_once`` commits RUNNING before network I/O and
uses fresh authorization transactions to save and apply its result. A crashed
or timed-out call is never replayed. This is the whole-input first-cycle path;
it does not partition large reports or silently reduce model coverage.
"""
from __future__ import annotations
from oa import sanity_ai_store as _ai_store

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import time
from uuid import uuid4

from common.sanity_ai_contracts import validate_review_v2
from common.sanity_ai_execution import worker_site
from common.sanity_ai_retry import (
    MAX_RETRIES, receipt as failure_receipt, retry_at, validation_failure,
)
from common.sanity_clustering import check
from common.sanity_clustering_advice import evidence, identity_fields_valid, validate_clustering_advice
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import lock_key, uuid_value
from common.sanity_preserved_ai import available as preserved_available, receipt as preserved_receipt
from oa.sanity_access import access_lock
from oa.sanity_ai_batch_jobs import (
    Ledger, load_result as batch_load_result, public as batch_public, run_step as batch_run_step,
)
from oa.sanity_ai_cases import CaseRetriever
from oa.sanity_ai_connections import ConnectionStore, is_vwp
from oa.sanity_ai_criteria import load_criteria
from oa.sanity_ai_deletion import delete as delete_job
from oa.sanity_ai_live import capture as live_capture, observer as live_observer
from oa.sanity_ai_model import PROVIDER
from oa.sanity_ai_monitor import Monitor
from oa.sanity_ai_parallel_jobs import cancel as parallel_cancel, run_step as parallel_run_step
from oa.sanity_ai_parallel_ledger import ParallelLedger
from oa.sanity_ai_resume import resume as resume_job, validate_resume
from oa.sanity_ai_rolling_jobs import run_step as rolling_run_step
from oa.sanity_ai_scheduler import Settings as SchedulerSettings, release, reserve
from oa.sanity_ai_store import (
    APPLY_STALE_ERRORS, INTENT_ACTIVE as PREPARING, INTENT_PREFIX, JOB_ACTIVE as ACTIVE,
    JOB_NAMESPACE as NAMESPACE, JOB_PREFIX as PREFIX, JOB_TERMINAL as TERMINAL,
    STALE_AI_ERRORS, ensure_not_deleted, error_code, job_lock, parse_timestamp,
    public_event, report_header as header, worker_load,
)
from oa.sanity_ai_submissions import Submissions, prepare_one
from oa.sanity_ai_task_defaults import task_selection
from oa.sanity_ai_tokens import UserTokenStore
from oa.sanity_ai_unknown_recovery import (
    retry_unknown as unknown_retry, unknown_availability as check_unknown_availability, validate_recovery,
)
from oa.sanity_ai_validation import (
    case_pins as _case_pins, source_pin as _source_pin,
    validate_job_data as _validate_data, validate_retry as _validate_retry,
)
from oa.sanity_preserved_text import enrich
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from oa.sanity_ws_config import verify_ws_config_row

PARALLEL_EXECUTION = 'parallel-v2'
TRANSITIONS = {
    None: {"PENDING"}, "PENDING": {"RUNNING", "FAILED", "STALE", "CANCELLED"},
    "RUNNING": {"PENDING", "RESULT_READY", "FAILED", "STALE", "UNKNOWN", "CANCELLED"},
    "RESULT_READY": {"COMPLETED", "FAILED", "STALE", "CANCELLED"},
    "FAILED": {"PENDING", "RUNNING"}, "CANCELLED": {"PENDING", "RUNNING"}, "UNKNOWN": {"RUNNING"},
}
MAX_EVENT_BYTES = 64 * 1024 * 1024
REVIEW_LOCK = lock_key("workspace-review-write")
_UNSET = object()


class AIJobService:
    """Caller owns a READ COMMITTED transaction and the shared access lock."""

    def __init__(self, db, ws, advisor=None, criteria_provider=None):
        self.db, self.ws = db, ws
        self.connections = ConnectionStore(db)
        if advisor is None:
            advisor = self.connections.default_advisor()
        self.advisor = advisor
        self.criteria_provider = load_criteria if criteria_provider is None else criteria_provider

    def _connection_advisor(self, connection_id, model_id=None):
        return self.connections.advisor(connection_id, model_id,
            transport=getattr(self.advisor, "transport", None))

    def _description(self, model_id=None, connection_id=None):
        if connection_id is not None:
            return self._connection_advisor(connection_id, model_id).describe()
        if self.advisor is None:
            return {"enabled": False, "model": "", "provider": PROVIDER}
        advisor = self.advisor if model_id is None else self.advisor.for_model(model_id)
        value = advisor.describe()
        check(value.get("provider") == PROVIDER and value.get("enabled") is True
              and isinstance(value.get("config_sha256"), str), "AI_CONFIG")
        return value

    def _job_advisor(self, event):
        try:
            connection_id = event["data"]["command"].get("connection_id")
            if connection_id:
                return self._connection_advisor(connection_id, event["data"]["model"])
            check(self.advisor is not None, "AI_CONFIG_CHANGED")
            return self.advisor.for_model(event["data"]["model"])
        except ReviewError as error:
            if error.code not in ("AI_MODEL_NOT_ALLOWED", "AI_CONNECTION_UNAVAILABLE"):
                raise
            raise ReviewError("AI_CONFIG_CHANGED", "The selected OA model is no longer available") from None

    def public(self, event):
        if 'batch' in event['data']:
            return batch_public(self, event)
        return public_event(event)

    def _personal_tokens(self, event=None, *, connection_id=None):
        if connection_id or event and event["data"]["command"].get("connection_id"):
            return True
        return getattr(getattr(self.advisor, "settings", None), "token_source", "server") == "user"

    @staticmethod
    def _bind_advisor(advisor, event, call_id):
        binder = getattr(advisor, "bind_execution", None)
        return advisor if binder is None else binder(call_id=call_id, job_id=event["job_id"],
            actor_id=event["data"]["command"]["user_id"], scope_sha256=digest(event["data"]["pins"]["source"]["scope"]))

    def _user_token(self, user_id, connection_id=None):
        if connection_id:
            return self.connections.tokens(self.connections.get(connection_id)).get_token(user_id)
        return UserTokenStore(self.db).get_token(user_id)

    def _vwp_credential_pin(self, user_id, connection_id):
        if not connection_id: return None
        value = self.connections.get(connection_id)
        if not is_vwp(value['config']): return None
        self.connections.authorize_user(connection_id, user_id)
        store = self.connections.tokens(value)
        _, revision = store.get_token(user_id)
        return {'revision': revision, 'scope': store.credential_scope}

    def _check_credential_pin(self, event):
        data = event['data']; command = data['command']; connection = command.get('connection_id')
        if not connection or not is_vwp(self.connections.get(connection)['config']): return
        check(isinstance(data.get('credential_pin'), dict), 'AI_CREDENTIAL_CHANGED')
        try: current = self._vwp_credential_pin(command['user_id'], connection)
        except ReviewError as error:
            if error.code in ('AI_USER_TOKEN_REQUIRED', 'AI_USER_TOKEN_EXPIRED', 'AI_CREDENTIAL_SCOPE_CHANGED', 'AI_TOKEN_UNAVAILABLE', 'AI_TOKEN_STORE_NOT_CONFIGURED'):
                raise ReviewError('AI_CREDENTIAL_CHANGED', 'The personal AI credential changed') from None
            raise
        check(data.get('credential_pin') is not None and current == data['credential_pin'], 'AI_CREDENTIAL_CHANGED')

    def _store(self, command):
        if command.get("group_id") is not None:
            return WorkspaceGroupReviewStore(self.db, command["group_id"], command.get("expected_cluster_revision"),
                                             ws=self.ws, criteria_provider=self.criteria_provider)
        return WorkspaceClusteringStore(self.db, ws=self.ws)

    def _context(self, command, *, write):
        store = self._store(command)
        context = store.context(command["user_id"], command["block_id"], command["version_id"], command["report_id"], write=write)
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
            key = PREFIX + job_id + "." + str(index).zfill(4)
            event = verify_ws_config_row(row, key, error_code="AI_JOB_CORRUPT")
            check(set(event) == {"namespace", "schema_version", "job_id", "sequence",
                "previous_sha256", "created_at", "status", "data", "sha256"}, "AI_JOB_CORRUPT")
            check(event["namespace"] == NAMESPACE and type(event["schema_version"]) is int and event["schema_version"] == 1
                  and event["job_id"] == job_id and type(event["sequence"]) is int and event["sequence"] == index
                  and event["previous_sha256"] == (previous["sha256"] if previous else None)
                  and event["sha256"] == digest({k: v for k, v in event.items() if k != "sha256"})
                  and event["status"] in TRANSITIONS.get(previous["status"] if previous else None, set()), "AI_JOB_CORRUPT")
            parse_timestamp(event["created_at"])
            _validate_data(event)
            if previous is None: check("resume_count" not in event["data"], "AI_JOB_CORRUPT")
            if previous:
                if previous["status"] in ("FAILED", "CANCELLED"):
                    validate_resume(self, previous, event["status"], event["data"])
                elif previous['status'] == 'UNKNOWN':
                    validate_recovery(self, previous, event['status'], event['data'])
                else:
                    check(event["data"].get("resume_count", 0) == previous["data"].get("resume_count", 0), "AI_JOB_CORRUPT")
                    for key in ("resume_request_id", "resumed_by", "resume_checkpoint"):
                        check(event["data"].get(key) == previous["data"].get(key), "AI_JOB_CORRUPT")
                if event["status"] == "PENDING" and previous["status"] == "RUNNING": _validate_retry(previous, event["data"])
                check(parse_timestamp(event["created_at"]) >= parse_timestamp(previous["created_at"]), "AI_JOB_CORRUPT")
                for key in ("command", "target", "pins", "retrieval", "model", "created_at", "worker_site", "fail_safe", "total_records", "credential_pin"):
                    check(event["data"].get(key) == previous["data"].get(key), "AI_JOB_CORRUPT")
                check(('batch' in event['data']) == ('batch' in previous['data']), 'AI_JOB_CORRUPT')
                if 'batch' in event['data']:
                    check(all(event['data']['batch'].get(key) == previous['data']['batch'].get(key)
                              for key in ('execution', 'max_concurrency')), 'AI_JOB_CORRUPT')
                if 'plan' in previous['data'].get('batch', {}):
                    check(event['data']['batch'] == previous['data']['batch'], 'AI_JOB_CORRUPT')
            check(event["data"]["command"]["request_id"] == job_id, "AI_JOB_CORRUPT")
            previous = event
        check(previous is not None or not required, "NOT_FOUND")
        return previous

    def _append(self, previous, status, data, *, now=None):
        now = now or _ai_store.db_now(self.db)
        check(status in TRANSITIONS.get(previous["status"] if previous else None, set()), "AI_JOB_STATE")
        if previous and status == "PENDING" and previous["status"] == "RUNNING": _validate_retry(previous, data)
        if previous and previous["status"] in ("FAILED", "CANCELLED"):
            validate_resume(self, previous, status, data)
        if previous and previous['status'] == 'UNKNOWN':
            validate_recovery(self, previous, status, data)
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
        live_capture(self, event)
        return event

    def _latest(self, *, target=None, work=False, now=None, exclude=None):
        query = "SELECT payload FROM (SELECT DISTINCT ON (payload->>'job_id') payload,created_at,workspace_key " \
            "FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s AND payload->>'namespace'=%s " \
            "ORDER BY payload->>'job_id',workspace_key DESC) jobs WHERE TRUE "
        params = [PREFIX + "%", NAMESPACE]
        if target is not None:
            query += "AND payload->'data'->>'target'=%s "
            params.append(target)
        if exclude:
            query += "AND NOT (payload->>'job_id'=ANY(%s)) "
            params.append(exclude)
        if work:
            query += "AND COALESCE(payload->'data'->>'worker_site',%s)=%s " \
                "AND (NOT (payload->'data' ? 'retry_after') OR (payload->'data'->>'retry_after')::timestamptz<=COALESCE(%s::timestamptz,clock_timestamp())) " \
                "AND (payload->>'status' IN ('PENDING','RESULT_READY') OR (payload->>'status'='RUNNING' " \
                "AND (payload->'data' ? 'batch' OR (payload->'data'->>'deadline')::timestamptz<=COALESCE(%s::timestamptz,clock_timestamp())))) ORDER BY (payload->'data'->>'created_at')::timestamptz,workspace_key LIMIT 1"
            params.extend((worker_site(), worker_site(), now, now))
        else:
            query += "ORDER BY created_at DESC,workspace_key DESC LIMIT 1"
        row = self.db.execute(query, tuple(params)).fetchone()
        return worker_load(self, row["payload"]["job_id"]) if row else None

    @staticmethod
    def _target(report_id, group_id):
        return digest([report_id, group_id])

    def capability(self, user_id, block_id, version_id, report_id, group_id=None, expected_cluster_revision=None):
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id,
                   "report_id": report_id, "group_id": group_id, "expected_cluster_revision": expected_cluster_revision}
        context = header(self, command)
        monitor = Monitor(self)
        target = self._target(report_id, group_id)
        latest = monitor.latest(target=target) or monitor.legacy(target)
        latest_job = monitor.unknown_availability(latest, user_id, context) if latest else None
        # Final group membership/current-revision checks happen on the worker.
        supported = bool(context["ready"] and context["can_review"] and context["record_count"] and context["group_ready"])
        connections, first, task_default = task_selection(self.connections, user_id, 'REVIEW' if group_id is not None else 'CLUSTER')
        for connection in connections:
            connection.update(available=bool(supported and connection["credential_ready"]), enabled=True,
                              supported=supported, credential_required=connection.get("credential_required", True))
        credential = {key: first[key] for key in ("credential_required", "credential_ready", "credential_error") if key in first}
        return {"available": first.get("available", False), "supported": supported,
                "enabled": bool(connections), "model": first.get("model", ""), "remaining": None,
                "models": first.get("models", []),
                "connections": connections, "connection_id": first.get("id", ""),
                "provider": "VWP_OPENAI_COMPATIBLE" if first.get("execution_site") == "vwp" else PROVIDER,
                "synthetic_only": False, "job": latest_job, **credential,
                **({'task_default': task_default} if task_default is not None else {})}

    def request(self, *args, **kwargs):
        return Submissions(self).submit(*args, **kwargs)

    def resume(self, user_id, job_id, request_id):
        return resume_job(self, user_id, job_id, request_id)

    def retry_unknown(self, user_id, job_id, request_id, expected_event_sha256, expected_step_sha256, acknowledge_duplicate):
        return unknown_retry(self, user_id, job_id, request_id, expected_event_sha256, expected_step_sha256, acknowledge_duplicate)

    def delete(self, user_id, job_id):
        return delete_job(self, user_id, job_id)

    def enqueue(self, user_id, block_id, version_id, report_id, *, expected_revision, request_id,
                group_id=None, expected_cluster_revision=None, model_id=None, connection_id=None,
                _worker_site=None, _requested_at=None, _credential_pin=None):
        check(type(expected_revision) is int and expected_revision >= 0, "INVALID_REQUEST")
        check(group_id is None or isinstance(group_id, str) and 1 <= len(group_id) <= 100, "INVALID_REQUEST")
        check(group_id is None or type(expected_cluster_revision) is int and expected_cluster_revision >= 1, "INVALID_REQUEST")
        check(group_id is not None or expected_cluster_revision is None, "INVALID_REQUEST")
        request_id = str(uuid_value(request_id))
        command = {"user_id": user_id, "block_id": block_id, "version_id": version_id, "report_id": report_id,
                   "expected_revision": expected_revision, "request_id": request_id,
                   "group_id": group_id, "expected_cluster_revision": expected_cluster_revision}
        if model_id is not None:
            command["model_id"] = model_id
        if connection_id is not None:
            connection_id = str(uuid_value(connection_id))
            command["connection_id"] = connection_id
        self._lock(request_id)
        ensure_not_deleted(self, request_id)
        target = self._target(report_id, group_id)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(INTENT_PREFIX + target),))
        store, context = self._context(command, write=True)
        previous = self._load(request_id, required=False)
        if previous:
            check(previous["data"]["command"] == command, "REQUEST_CONFLICT")
            return self.public(previous)
        intent = Submissions(self).latest(target=target)
        check(not intent or intent["status"] not in PREPARING or intent["id"] == request_id, "AI_JOB_ACTIVE")
        model = self._description(model_id, connection_id)
        check(model["enabled"], "AI_NOT_CONFIGURED")
        if connection_id:
            self.connections.authorize_user(connection_id, user_id)
        pin = self._vwp_credential_pin(user_id, connection_id)
        if _credential_pin is not None: check(pin == _credential_pin, 'AI_CREDENTIAL_CHANGED')
        if self._personal_tokens(connection_id=connection_id):
            self._user_token(user_id, connection_id)
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
        if model.get('batch', {}).get('enabled'):
            case_budget = min(case_budget, max(1, model['batch']['input_tokens'] // 4))
        selected = CaseRetriever(self.db, workspace=self.ws).select(context, stage=stage, max_bytes=case_budget)
        criteria = self.criteria_provider(context) if group_id is not None else []
        fields = []
        if group_id is None:
            old, _ = store.current(report_id)
            fields = identity_fields_valid(old["identity_fields"] if old else ["object", "check_point", "mode"], evidence(context))
        pins = {"source": _source_pin(context), "cases": _case_pins(selected["cases"]),
                "criteria_sha256": digest(criteria), "config_sha256": model["config_sha256"],
                "identity_fields": fields, "case_budget": case_budget, "stage": stage}
        now = _ai_store.db_now(self.db)
        data = {"command": command, "target": target, "pins": pins, "retrieval": selected["metadata"],
                "model": model["model"], "created_at": now.isoformat(), "model_called": False}
        if pin is not None: data['credential_pin'] = pin
        if _worker_site is not None:
            check(_worker_site == worker_site() == "vwp", "AI_WORKER_SITE")
            data.update(worker_site=_worker_site, created_at=_requested_at or now.isoformat(), fail_safe=True,
                        total_records=len(context.get("group_evidence") or evidence(context)))
        elif model.get("execution_site") == "vwp": data["worker_site"] = "vwp"
        if connection_id: data["connection_label"] = model["connection_label"]
        if model.get('batch', {}).get('enabled'):
            data['batch'] = {'version': 2 if group_id is not None or preserved_available(context) else 1}
            width = model['batch'].get('max_concurrency', 1)
            scheduler = SchedulerSettings(self.db).current()
            if scheduler['revision']:
                width = scheduler['limits']['max_concurrency']
            if width > 1:
                data['batch'].update(execution=PARALLEL_EXECUTION, max_concurrency=width)
        return self.public(self._append(None, "PENDING", data, now=now))

    def status(self, user_id, job_id):
        job_id = str(uuid_value(job_id, "job_id"))
        # This integrity/audit method validates the authoritative history.
        # Web polling uses capability/Monitor's bounded display snapshots.
        event = self._load(job_id, required=False)
        if event is None:
            store = Submissions(self); intent = store.load(job_id)
            header(self, {**intent["data"]["command"], "user_id": user_id})
            return store.public(intent)
        command = event['data']['command']
        meta = header(self, {**command, "user_id": user_id})
        job = self.public(event)
        if event['status'] == 'UNKNOWN':
            available, error = check_unknown_availability(self, event, user_id, meta, job)
            job = {**job, 'can_retry_unknown': available, **({'unknown_recovery_error': error} if error else {})}
        return Monitor(self).connection_availability(job)

    def cancel(self, user_id, job_id):
        job_id = str(uuid_value(job_id, "job_id"))
        job_lock(self, job_id)
        submissions = Submissions(self)
        intent = submissions.load(job_id, required=False)
        event = self._load(job_id, required=False)
        if event is None:
            check(intent is not None, "AI_JOB_NOT_FOUND")
            header(self, {**intent["data"]["command"], "user_id": user_id}, write=True)
            if intent["status"] not in PREPARING: return submissions.public(intent)
            return submissions.public(submissions.append(intent, "CANCELLED", {**intent["data"], "cancelled_by": user_id}))
        command = event["data"]["command"]
        header(self, {**command, "user_id": user_id}, write=True)
        if event["status"] in TERMINAL:
            return self.public(event)
        if event['data'].get('batch', {}).get('execution') in ('parallel-v1', 'parallel-v2'):
            return parallel_cancel(self, event, user_id)
        return self.public(self._append(event, "CANCELLED", {**event["data"], "cancelled_by": user_id}))

    def _inputs(self, event):
        self._check_credential_pin(event)
        data, model = event["data"], self._job_advisor(event).describe()
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
        if self._personal_tokens(event):
            _, credential_revision = self._user_token(command["user_id"], command.get("connection_id"))
            if 'credential_revision' in data.get('batch', {}):
                check(data['batch']['credential_revision'] == credential_revision, 'AI_CREDENTIAL_CHANGED')
            if "result" in data:
                check(data["result"]["metadata"].get("credential_revision") == credential_revision,
                      "AI_CREDENTIAL_CHANGED")
        return store, context, selected["cases"], criteria

    def _failed(self, event, error, *, status="STALE", called=_UNSET, now=None):
        changes = {"error": error_code(error)}
        diagnostic = validation_failure(error)
        data = {k: v for k, v in event['data'].items() if k != 'validation_failure'}
        if diagnostic is not None: changes['validation_failure'] = diagnostic
        reason = getattr(error, 'unknown_reason', None)
        if status == 'UNKNOWN' and reason in ('READ_TIMEOUT', 'WRITE_TIMEOUT', 'TRANSPORT_ERROR', 'RESPONSE_DEADLINE', 'WORKER_LOST', 'RESERVED_EXPIRED'):
            changes['unknown_reason'] = reason
        if called is not _UNSET:
            changes["model_called"] = called
            changes["model_attempted"] = called is not False
        return self._append(event, status, {**data, **changes}, now=now)

    def _apply(self, event):
        try:
            store, context, cases, criteria = self._inputs(event)
        except ReviewError as error:
            return self._failed(event, error)
        data, command = event["data"], event["data"]["command"]
        if 'batch' in data:
            try: result = batch_load_result(self, event, context, cases, criteria)
            except ReviewError as error: return self._failed(event, error, status='FAILED')
        else:
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
            stale = error.code in APPLY_STALE_ERRORS
            return self._failed(event, error, status="STALE" if stale else "FAILED")
        event_id = applied.get("id") or applied.get("event", {}).get("id")
        check(event_id == command["request_id"], "AI_JOB_CORRUPT")
        return self._append(event, "COMPLETED", {**data, "event_id": event_id})


@contextmanager
def transaction(db_factory, workspace_factory, advisor, criteria_provider):
    # Each phase obtains a new connection: nested transaction/savepoint commit
    # is never mistaken for the durable boundary preceding model dispatch.
    with db_factory() as db:
        status = getattr(db.conn, "get_transaction_status", None)
        check(status is None or status() == 0, "AI_TRANSACTION_ACTIVE")
        with db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
            access_lock(db)
            yield AIJobService(db, workspace_factory(db), advisor=advisor, criteria_provider=criteria_provider)


def run_once(db_factory, workspace_factory, advisor, criteria_provider=None, *, now=None, job_id=None):
    """Process at most one queued job; never keep a connection across HTTP.

    ``db_factory`` must yield a fresh OA Database; ``workspace_factory`` must
    supply a fresh ProductionWorkspaceStore in production. They are injected
    in tests so only isolated synthetic PostgreSQL and model transports run.
    """
    def open_service():
        return transaction(db_factory, workspace_factory, advisor, criteria_provider)

    dispatch = None
    with open_service() as service:
        # Skip a batch in backoff so another job can use the single worker.
        # The authoritative child ledger, not an observational snapshot,
        # determines its retry deadline.
        excluded = []
        event = None
        while len(excluded) < 1000:
            candidate = (service._load(job_id, required=False) if job_id is not None else
                         service._latest(work=True, now=now, exclude=excluded))
            if candidate is None: break
            if candidate['status'] == 'RUNNING' and candidate['data'].get('fail_safe') and 'batch' in candidate['data']:
                try:
                    if candidate['data']['batch'].get('execution') in ('parallel-v1', 'parallel-v2'):
                        step, _ = ParallelLedger(service, candidate).last()
                        slots = step['state'].get('wave', []) if step else []
                        current_time = now or _ai_store.db_now(service.db)
                        active_slots = [s for s in slots if s['status'] == 'RESERVED']
                        active_wait = bool(active_slots and parse_timestamp(candidate['data']['deadline']) > current_time
                            and all(parse_timestamp(s['deadline']) > current_time for s in active_slots))
                        waiting = active_wait or bool(slots and all(s['status'] == 'DONE' or s['status'] == 'RETRY' and s.get('retry_after')
                            and parse_timestamp(s['retry_after']) > (now or _ai_store.db_now(service.db)) for s in slots)
                            and any(s['status'] == 'RETRY' for s in slots))
                    else:
                        step, _ = Ledger(service, candidate).last()
                        waiting = bool(step and step['status'] == 'READY' and step['state'].get('retry_after') and
                            parse_timestamp(step['state']['retry_after']) > (now or _ai_store.db_now(service.db)))
                except ReviewError: waiting = False
                if waiting:
                    if job_id is not None: return service.public(candidate)
                    excluded.append(candidate['job_id'])
                    continue
            event = candidate
            break
        batch_job = event['job_id'] if event and 'batch' in event['data'] else None
    if event is None:
        return prepare_one(open_service, identity=job_id)
    if batch_job is not None:
        if event['data']['batch'].get('execution') == 'parallel-v2':
            return rolling_run_step(open_service, batch_job, now=now)
        if event['data']['batch'].get('execution') in ('parallel-v1', 'parallel-v2'):
            return parallel_run_step(open_service, batch_job, now=now)
        return batch_run_step(open_service, batch_job, now=now)
    with open_service() as service:
        service._lock(event["job_id"])
        event = worker_load(service, event["job_id"])
        if event is None:
            return None
        if event["status"] == "RESULT_READY":
            return public_event(service._apply(event))
        if event["status"] == "RUNNING":
            if parse_timestamp(event["data"]["deadline"]) <= (now or _ai_store.db_now(service.db)):
                return public_event(service._failed(event, ReviewError("AI_OUTCOME_UNKNOWN", "Call completion is unknown"),
                                               status="UNKNOWN", called=None, now=now))
            return public_event(event)
        if event["status"] != "PENDING":
            return public_event(event)
        try:
            _, context, cases, criteria = service._inputs(event)
            dispatch_advisor, credential_revision = service._job_advisor(event), None
            if service._personal_tokens(event):
                token, credential_revision = service._user_token(event["data"]["command"]["user_id"], event["data"]["command"].get("connection_id"))
                dispatch_advisor = dispatch_advisor.with_user_token(token)
            call_id = str(uuid4())
            dispatch_advisor = service._bind_advisor(dispatch_advisor, event, call_id)
            dispatch_advisor = dispatch_advisor.with_observer(live_observer(open_service, event, call_id))
        except ReviewError as error:
            return public_event(service._failed(event, error, now=now))
        started = now or _ai_store.db_now(service.db)
        deadline = started + timedelta(seconds=dispatch_advisor.settings.timeout + 60)
        if not reserve(service, event['job_id'], call_id, deadline.isoformat()): return public_event(event)
        running = {k: v for k, v in event["data"].items() if k not in ("error", "retry_after", "retry_failure", "validation_failure")}
        event = service._append(event, "RUNNING", {**running, "call_token": call_id,
                   "model_attempted": True, "model_called": None,
                   "started_at": started.isoformat(), "deadline": deadline.isoformat()}, now=started)
        dispatch = {"event": event, "context": context, "cases": cases, "criteria": criteria,
                    "advisor": dispatch_advisor, "credential_revision": credential_revision}
    # All DB handles and transaction-level authorization/review locks are gone.
    call_started = time.monotonic()
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
              and result["metadata"].get("provider") == dispatch["advisor"].describe()["provider"], "INVALID_AI_RESPONSE")
        preserved_receipt(result["metadata"], advice, context)
        result = {"advice": advice, "metadata": result["metadata"]}
        if dispatch["credential_revision"] is not None:
            result["metadata"] = {**result["metadata"], "credential_revision": dispatch["credential_revision"]}
        failure = None
    except Exception as error:
        result, failure = None, error
    call_seconds = time.monotonic() - call_started
    with open_service() as service:
        service._lock(event["job_id"])
        release(service, event['job_id'], event['data']['call_token'])
        current = worker_load(service, event["job_id"])
        if current is None:
            return None
        if current["status"] != "RUNNING" or current["data"].get("call_token") != event["data"]["call_token"]:
            return public_event(current)  # A late response never revives a terminal job.
        if parse_timestamp(current["data"]["deadline"]) <= _ai_store.db_now(service.db):
            return public_event(service._failed(current, ReviewError("AI_OUTCOME_UNKNOWN", "Call completion exceeded its deadline"),
                                           status="UNKNOWN", called=True))
        if failure is not None:
            code = error_code(failure)
            predispatch = code in {"AI_INPUT_LIMIT", "AI_NOT_CONFIGURED", "AI_CONFIG", "INVALID_AI_INPUT", "INVALID_AI_CRITERIA"}
            unknown = code == "AI_OUTCOME_UNKNOWN" or not isinstance(failure, ReviewError)
            called = getattr(failure, "model_called", not predispatch)
            called = called if type(called) is bool else None
            proof = failure_receipt(failure) if current["data"].get("fail_safe") else None
            if proof is not None and current["data"].get("retry_count", 0) < MAX_RETRIES:
                count = current["data"].get("retry_count", 0) + 1
                data = {k: v for k, v in current["data"].items() if k not in ("call_token", "started_at", "deadline")}
                data.update(error=code, model_called=proof["model_called"], model_attempted=proof["model_called"],
                            retry_failure=proof, retry_count=count, retry_after=retry_at(_ai_store.db_now(service.db), count, failure))
                diagnostic = validation_failure(failure)
                if diagnostic is not None: data['validation_failure'] = diagnostic
                else: data.pop('validation_failure', None)
                return public_event(service._append(current, "PENDING", data))
            if proof is not None: current = {**current, "data": {**current["data"], "retry_failure": proof}}
            return public_event(service._failed(current, failure, status="UNKNOWN" if unknown else "STALE" if code in STALE_AI_ERRORS else "FAILED", called=called))
        try:
            service._inputs(current)
            if dispatch["credential_revision"] is not None:
                _, revision = service._user_token(event["data"]["command"]["user_id"], event["data"]["command"].get("connection_id"))
                check(revision == dispatch["credential_revision"], "AI_CREDENTIAL_CHANGED")
        except ReviewError as error:
            return public_event(service._failed(current, error, called=True))
        try:
            service._append(current, "RESULT_READY", {**current["data"], "model_called": True, "result": result, "call_seconds": call_seconds})
        except ReviewError as error:
            if error.code != "AI_OUTPUT_LIMIT":
                raise
            return public_event(service._failed(current, error, status="FAILED", called=True))
    with open_service() as service:
        service._lock(event["job_id"])
        current = worker_load(service, event["job_id"])
        if current is None:
            return None
        return public_event(service._apply(current)) if current["status"] == "RESULT_READY" else public_event(current)
