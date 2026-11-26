"""Shared preflight, single-lock transaction start, and event append for OA AI job recovery."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from common.sanity_ai_retry import MAX_RESUMES
from common.sanity_common import ReviewError
from common.sanity_db import lock_key, uuid_value
from common.sanity_submission import check
from oa import sanity_ai_store as _ai_store
from oa.sanity_ai_store import (
    AVAILABILITY_ERRORS,
    INTENT_ACTIVE,
    INTENT_PREFIX,
    JOB_ACTIVE,
    RESUME_PREFIX,
    job_lock,
    parse_timestamp,
    report_header,
    resume_marker,
    unknown_marker,
)
from oa.sanity_ai_submissions import Submissions

STRIPPED_RECOVERY_KEYS = (
    "finished_at", "cancelled_by", "error", "retry_after", "retry_failure",
    "resume_checkpoint", "unknown_reason", "validation_failure",
)


def verify_submission_snapshot(service, job_id: str, snapshot: dict[str, Any]) -> None:
    intent = Submissions(service).load(job_id, required=False)
    if intent:
        check(intent["data"]["snapshot"] == snapshot, "AI_INPUT_STALE")


def evaluate_unknown_availability(
    service,
    event: dict[str, Any],
    user_id: str,
    meta: dict[str, Any],
    verify_ledger_and_budget: Callable[[dict[str, Any]], None],
) -> tuple[bool, str | None]:
    """Shared display-only lightweight preflight for sequential and parallel UNKNOWN jobs."""
    if event["status"] != "UNKNOWN" or "batch" not in event["data"]:
        return False, None
    if event["data"]["command"]["user_id"] != user_id or not meta["can_review"]:
        return False, "FORBIDDEN"
    try:
        current = report_header(service, {**event["data"]["command"], "user_id": user_id})
        check(current["can_review"], "FORBIDDEN")
        check(current["ready"] and current["group_ready"], "AI_INPUT_STALE")
        verify_ledger_and_budget(current)
        verify_submission_snapshot(service, event["job_id"], current["snapshot"])
        service._check_credential_pin(event)
        return True, None
    except ReviewError as error:
        if error.code not in AVAILABILITY_ERRORS:
            raise
        return False, error.code


def begin_job_recovery(
    service,
    user_id: str,
    job_id,
    request_id,
    *,
    unknown: bool = False,
    expected_event: str | None = None,
    expected_step: str | None = None,
) -> tuple[str, str, dict[str, Any], dict[str, Any], bool]:
    """Acquire recovery locks once, load the job once, and check markers & active-job exclusion."""
    job_id = str(uuid_value(job_id, "job_id"))
    request_id = str(uuid_value(request_id, "request_id"))
    service.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(RESUME_PREFIX + request_id),))
    job_lock(service, job_id)
    event = service._load(job_id)
    command = event["data"]["command"]
    check(command["user_id"] == user_id, "FORBIDDEN")
    meta = report_header(service, {**command, "user_id": user_id}, write=True)
    old = unknown_marker(service, request_id) if unknown else resume_marker(service, request_id)
    if old:
        check(
            old["job_id"] == job_id
            and old["actor_id"] == user_id
            and (not unknown or (old["event_sha256"] == expected_event and old["step_sha256"] == expected_step)),
            "REQUEST_CONFLICT",
        )
        return job_id, request_id, event, meta, True
    opposite = resume_marker(service, request_id) if unknown else unknown_marker(service, request_id)
    check(opposite is None, "REQUEST_CONFLICT")
    check(meta["ready"] and meta["can_review"] and meta["group_ready"], "AI_INPUT_STALE")
    target = event["data"]["target"]
    service.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(INTENT_PREFIX + target),))
    latest = service._latest(target=target)
    intent = Submissions(service).latest(target=target)
    check(not latest or latest["job_id"] == job_id or latest["status"] not in JOB_ACTIVE, "AI_JOB_ACTIVE")
    check(not intent or intent["id"] == job_id or intent["status"] not in INTENT_ACTIVE, "AI_JOB_ACTIVE")
    return job_id, request_id, event, meta, False


def validate_common_recovery_fields(
    previous: dict[str, Any],
    status: str,
    data: dict[str, Any],
    *,
    expected_status: str = "RUNNING",
    forbidden_keys: tuple[str, ...] = ("error", "finished_at"),
) -> None:
    check(
        status == expected_status
        and type(data.get("resume_count")) is int
        and data["resume_count"] == previous["data"].get("resume_count", 0) + 1
        and data["resume_count"] <= MAX_RESUMES
        and data.get("resumed_by") == previous["data"]["command"]["user_id"]
        and isinstance(data.get("resume_request_id"), str)
        and str(uuid_value(data["resume_request_id"])) == data["resume_request_id"]
        and data.get("retry_count", 0) == 0
        and all(k not in data for k in forbidden_keys),
        "AI_JOB_CORRUPT",
    )


def append_recovered_job_event(
    service,
    event: dict[str, Any],
    *,
    user_id: str,
    request_id: str,
    batch: bool,
    checkpoint: dict[str, Any] | None,
    marker_key: str,
    marker_payload: dict[str, Any],
) -> dict[str, Any]:
    data = {k: v for k, v in event["data"].items() if k not in STRIPPED_RECOVERY_KEYS}
    data.update(
        resume_count=event["data"].get("resume_count", 0) + 1,
        resume_request_id=request_id,
        resumed_by=user_id,
        retry_count=0,
    )
    now = _ai_store.db_now(service.db)
    if batch:
        duration = parse_timestamp(event["data"]["deadline"]) - parse_timestamp(event["data"]["started_at"])
        check(timedelta(seconds=60) <= duration <= timedelta(seconds=604800), "AI_JOB_CORRUPT")
        data.update(
            resume_checkpoint=checkpoint,
            started_at=now.isoformat(),
            deadline=(now + duration).isoformat(),
        )
        status = "RUNNING"
    else:
        for key in ("call_token", "started_at", "deadline"):
            data.pop(key, None)
        data.update(model_called=False, model_attempted=False)
        status = "PENDING"
    result = service._append(event, status, data, now=now)
    service.ws.put("WS_CONFIG", marker_key, marker_payload)
    return service.public(result)
