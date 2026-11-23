"""Shared leaf durable primitives for OA AI jobs, submissions, recovery and deletion."""
from __future__ import annotations

from datetime import datetime
import re

from common.sanity_ai_retry import MAX_RETRIES, resumable
from common.sanity_clustering import check
from common.sanity_common import ReviewError
from common.sanity_db import lock_key, uuid_value
from common.sanity_vwp_ai import require
from oa.sanity_ai_progress import display_metrics
from oa.sanity_workspace_core import record_id

JOB_NAMESPACE, JOB_PREFIX = "oa-ai-v1", "ai1.job."
JOB_ACTIVE = frozenset(("PENDING", "RUNNING", "RESULT_READY"))
JOB_TERMINAL = frozenset(("COMPLETED", "FAILED", "STALE", "UNKNOWN", "CANCELLED"))

INTENT_PREFIX, INTENT_NAMESPACE = "ai.intent.", "oa-ai-submission-v1"
INTENT_ACTIVE = {"QUEUED", "PREPARING"}
INTENT_TERMINAL = {"PREPARED", "FAILED", "STALE", "CANCELLED"}

DELETE_PREFIX, DELETE_NAMESPACE = "ai.deleted.", "oa-ai-deleted-v1"
RESUME_PREFIX, RESUME_NAMESPACE = "ai.resume.", "oa-ai-resume-v1"
UNKNOWN_PREFIX, UNKNOWN_NAMESPACE = "ai.unknown.", "oa-ai-unknown-recovery-v1"

AVAILABILITY_ERRORS = frozenset((
    "AI_INPUT_STALE", "AI_CREDENTIAL_CHANGED", "AI_CONFIG_CHANGED", "AI_BATCH_LIMIT",
    "AI_VWP_PROFILE_UNAVAILABLE", "AI_CONNECTION_UNAVAILABLE", "AI_TOKEN_STORE_NOT_CONFIGURED",
    "FORBIDDEN", "UNAUTHORIZED", "AI_RESUME_UNAVAILABLE",
))

NULL_COLUMNS = (
    "project", "run_id", "bundle_id", "review_unit_id", "review_revision", "request_id",
    "entity_key", "classification_revision", "payload_bytes", "payload_sha256",
    "workspace_project_ref", "workspace_block_ref", "workspace_version_ref",
    "workspace_review_report_ref", "workspace_review_version_ref", "workspace_review_event_ref",
    "workspace_pipeline_report_ref", "workspace_pipeline_version_ref",
    "workspace_pipeline_event_ref", "workspace_pipeline_cluster_ref",
)


def db_now(db):
    return db.execute("SELECT clock_timestamp() AS now").fetchone()["now"]


def parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        check(parsed.tzinfo is not None, "AI_JOB_CORRUPT")
        return parsed
    except (ValueError, TypeError):
        raise ReviewError("AI_JOB_CORRUPT", "Invalid job timestamp") from None


def error_code(error):
    code = getattr(error, "code", "AI_INTERNAL_ERROR")
    return code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) else "AI_INTERNAL_ERROR"


def job_lock(service, identity):
    # Submission/cancellation change only this job. Waiting for the global
    # human-review lock would tie the OA web to another job's heavy preparation.
    service.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(JOB_PREFIX + identity),))


def public_event(event):
    data = event["data"]
    connection = (
        {"connection_id": data["command"]["connection_id"], "connection_label": data["connection_label"]}
        if "connection_id" in data["command"]
        else {}
    )
    return {
        "id": event["job_id"], "status": event["status"], "error": data.get("error"),
        **connection, **display_metrics(event),
        **({"completion_reason": data["completion_reason"]} if "completion_reason" in data else {}),
        "model": data["model"], "created_at": data["created_at"], "finished_at": data.get("finished_at"),
        "expected_revision": data["command"]["expected_revision"],
        "input_case_count": len(data["pins"]["cases"]), "event_id": data.get("event_id"),
        "model_called": data.get("model_called", False), "model_attempted": data.get("model_attempted", False),
        "resumable": resumable(event),
        **({"retry_limit": MAX_RETRIES} if data.get("fail_safe") else {}),
        **{
            k: data[k]
            for k in (
                "worker_site", "total_records", "started_at", "call_seconds", "retry_count",
                "retry_after", "resume_count", "unknown_reason", "validation_failure",
            )
            if k in data
        },
    }


def report_header(service, command, *, write=False):
    """Authorize and read small projections, never source records or cases."""
    ws, db = service.ws, service.db
    actor = command["user_id"]
    block, _ = ws.access(actor, command["block_id"], write=write)
    version = ws.version(actor, command["block_id"], command["version_id"])
    row = db.execute(
        "SELECT record_id,xmin::text AS snapshot_xid,version,updated_at,workspace_block_ref,workspace_version_ref,"
        "payload->>'id' AS report_id,payload->>'block_id' AS block_id,payload->>'version_id' AS version_id,"
        "payload->>'project_id' AS project_id,payload->>'item_id' AS item_id,payload->>'family' AS family,"
        "payload->'record_count' AS record_count,payload->'analysis_ready' AS analysis_ready,"
        "payload->>'parse_status' AS parse_status FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key=%s",
        (command["report_id"],),
    ).fetchone()
    require(
        row is not None and row["workspace_block_ref"] == block["record_id"]
        and row["workspace_version_ref"] == version["record_id"] and row["block_id"] == command["block_id"]
        and row["version_id"] == command["version_id"] and row["report_id"] == command["report_id"]
        and row["project_id"] == block["payload"]["project_id"],
        "NOT_FOUND",
    )
    snapshot = {
        "record_id": str(row["record_id"]), "snapshot_xid": row["snapshot_xid"],
        "version": row["version"], "updated_at": row["updated_at"].isoformat(),
    }
    group_ready = True
    if command.get("group_id") is not None:
        cluster = db.execute(
            "SELECT h.version,e.payload->'confirmed' AS confirmed,EXISTS(SELECT 1 "
            "FROM jsonb_array_elements(e.payload->'groups') g WHERE g->>'id'=%s) AS member "
            "FROM {oa} h JOIN {oa} e ON e.record_type='WS_CLUSTER_EVENT' AND e.record_id=h.workspace_pipeline_event_ref "
            "WHERE h.record_type='WS_CLUSTER' AND h.workspace_key=%s AND h.workspace_pipeline_report_ref=%s",
            (command["group_id"], command["report_id"], row["record_id"]),
        ).fetchone()
        group_ready = bool(
            cluster and cluster["confirmed"] is True and cluster["member"]
            and (command.get("expected_cluster_revision") is None or command["expected_cluster_revision"] == cluster["version"])
        )
    return {
        "snapshot": snapshot,
        "ready": row["analysis_ready"] is True and row["parse_status"] == "COMPLETE",
        "group_ready": group_ready,
        "can_review": ws.can_write(actor, block),
        "record_count": row["record_count"], "report_id": row["report_id"],
        "project_id": row["project_id"], "item_id": row["item_id"], "family": row["family"],
    }


def deletion_available(service):
    if getattr(service.db, "side", None) != "oa" or getattr(service.db, "privilege_mode", None) != "single_account":
        return False
    row = service.db.execute(
        "SELECT has_table_privilege(current_user, quote_ident(%s) || '.sanity_oa_data', 'DELETE') AS allowed",
        (service.db.schema,),
    ).fetchone()
    return bool(row and row["allowed"])


def deletion_marker(service, identity):
    key = DELETE_PREFIX + str(uuid_value(identity, "job_id"))
    row = service.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (key,)).fetchone()
    if row is None:
        return None
    value = row["payload"]
    check(
        isinstance(value, dict) and {"namespace", "id", "actor_id"} <= set(value)
        and value["namespace"] == DELETE_NAMESPACE and value["id"] == str(identity)
        and isinstance(value["actor_id"], str) and 1 <= len(value["actor_id"]) <= 100
        and row["record_id"] == record_id("WS_CONFIG", key) and row["state"] == {} and row["version"] == 0
        and row["status"] == "RECORDED" and row["created_at"] == row["updated_at"]
        and all(row.get(k) is None for k in NULL_COLUMNS),
        "AI_JOB_CORRUPT",
    )
    return value


def ensure_not_deleted(service, identity):
    check(deletion_marker(service, identity) is None, "AI_JOB_DELETED")


def worker_load(service, identity, loader=None):
    event = (loader or service._load)(identity, required=False)
    check(event is not None or deletion_marker(service, identity) is not None, "AI_JOB_NOT_FOUND")
    return event


def resume_marker(service, request_id):
    key = RESUME_PREFIX + request_id
    row = service.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (key,)).fetchone()
    if not row:
        return None
    value = row["payload"]
    check(
        isinstance(value, dict) and {"namespace", "id", "job_id", "actor_id"} <= set(value)
        and value["namespace"] == RESUME_NAMESPACE and value["id"] == request_id
        and row["record_id"] == record_id("WS_CONFIG", key) and row["state"] == {} and row["version"] == 0
        and row["status"] == "RECORDED" and row["created_at"] == row["updated_at"]
        and all(row.get(k) is None for k in NULL_COLUMNS),
        "AI_JOB_CORRUPT",
    )
    return value


def unknown_marker(service, request_id):
    key = UNKNOWN_PREFIX + request_id
    row = service.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (key,)).fetchone()
    if not row:
        return None
    value = row["payload"]
    check(
        isinstance(value, dict) and {"namespace", "id", "job_id", "actor_id", "event_sha256", "step_sha256"} <= set(value)
        and value["namespace"] == UNKNOWN_NAMESPACE and value["id"] == request_id
        and row["record_id"] == record_id("WS_CONFIG", key) and row["state"] == {} and row["version"] == 0
        and row["status"] == "RECORDED" and row["created_at"] == row["updated_at"]
        and all(row.get(k) is None for k in NULL_COLUMNS),
        "AI_JOB_CORRUPT",
    )
    return value
