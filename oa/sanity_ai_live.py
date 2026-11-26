"""Immutable live progress snapshots and worker heartbeat storage."""
from __future__ import annotations
from oa import sanity_ai_store as _ai_store

from common.sanity_ai_execution import worker_site
from common.sanity_common import canonical_json
from common.sanity_db import Database, json_value, lock_key
from common.sanity_vwp_ai import require
from oa.sanity_ai_store import JOB_PREFIX, deletion_marker
from oa.sanity_workspace_core import WorkspaceCoreStore, record_id
from oa.sanity_ws_config import verify_ws_config_row

PREFIX, NAMESPACE = "ai.live.", "ai-live-v1"


def validate(row):
    value = row.get("payload")
    require(
        isinstance(value, dict) and value.get("namespace") == NAMESPACE
        and isinstance(value.get("id"), str)
        and type(value.get("sequence")) is int and value["sequence"] >= 1,
        "AI_MONITOR_CORRUPT",
    )
    return verify_ws_config_row(
        row,
        PREFIX + value["id"] + "." + f"{value['sequence']:010d}",
        error_code="AI_MONITOR_CORRUPT",
    )


def record(service, identity, command, target, public, *, phase=None, call_id=None):
    # Observers can already hold the job lock while HTTP is open. Taking the
    # global review lock here would invert delete's review -> job lock order.
    service.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(JOB_PREFIX + identity),))
    if deletion_marker(service, identity) is not None:
        return
    service.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(PREFIX + identity),))
    row = service.db.execute(
        "SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
        "ORDER BY workspace_key DESC LIMIT 1",
        (PREFIX + identity + ".%",),
    ).fetchone()
    previous = validate(row) if row else None
    at = _ai_store.db_now(service.db)
    if len(canonical_json(public)) > 12000:
        # Large configured windows must not turn observability into a failed
        # model result. UNKNOWN detail is reconstructed from its durable ledger.
        public = {k: v for k, v in public.items() if k not in ("unknown_recoveries",)}
        progress = public.get("progress")
        if progress and "active_descriptors" in progress:
            public = {
                **public,
                "progress": {
                    **progress,
                    "active_descriptors": progress["active_descriptors"][:32],
                    "active_descriptors_truncated": len(progress["active_descriptors"]) > 32,
                },
            }
    value = {
        "namespace": NAMESPACE, "id": identity, "actor_id": command["user_id"], "target": target,
        "sequence": previous["sequence"] + 1 if previous else 1,
        "command": {k: command[k] for k in ("block_id", "version_id", "report_id", "group_id")},
        "at": at.isoformat(), "job": public,
    }
    if phase:
        value["phase"] = phase
    if call_id:
        value["call_id"] = call_id
    require(len(canonical_json(value)) <= 16384, "AI_OUTPUT_LIMIT")
    key = PREFIX + identity + "." + f"{value['sequence']:010d}"
    service.ws.put("WS_CONFIG", key, value)


def capture(service, event, *, phase=None, call_id=None):
    # Rendering a batch reads its last small checkpoint, not completed bodies.
    public = service.public(event)
    record(service, event["job_id"], event["data"]["command"], event["data"]["target"], public,
           phase=phase, call_id=call_id)


def observer(open_service, event, call_id):
    def observe(phase):
        try:
            with open_service() as service:
                service.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(JOB_PREFIX + event["job_id"]),))
                # Only read this job's latest small display snapshot. Do not
                # acquire review locks while a model connection is open.
                row = service.db.execute(
                    "SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                    "ORDER BY workspace_key DESC LIMIT 1",
                    (PREFIX + event["job_id"] + ".%",),
                ).fetchone()
                previous = validate(row) if row else None
                if (previous and previous["job"]["status"] == "RUNNING"
                        and previous["job"].get("resume_count", 0) == event["data"].get("resume_count", 0)):
                    record(service, event["job_id"], event["data"]["command"], event["data"]["target"],
                           previous["job"], phase=phase, call_id=call_id)
        except Exception:
            # Observability never changes dispatch certainty or result policy.
            pass
    return observe


def heartbeat(db_factory=None):
    """One mutable service-session marker per site; no DELETE/DDL privilege.

    Its dotted key cannot be a login token hash. The payload is expired even
    if it were ever offered to the legacy session reader. No user session or
    production authentication state is created here.
    """
    factory = db_factory or (lambda: Database("oa"))
    with factory() as db, db.transaction():
        at = _ai_store.db_now(db)
        site = worker_site()
        key = "ai.worker." + site
        db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(key),))
        payload = {"namespace": "ai-worker-v1", "site": site, "expires_at": "1970-01-01T00:00:00+00:00"}
        row = db.execute("SELECT * FROM {oa} WHERE record_type='WS_SESSION' AND workspace_key=%s", (key,)).fetchone()
        if row:
            require(row["record_id"] == record_id("WS_SESSION", key) and row["payload"] == payload, "AI_MONITOR_CORRUPT")
            db.execute(
                "UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() "
                "WHERE record_type='WS_SESSION' AND workspace_key=%s",
                (json_value({"last_seen": at.isoformat()}), key),
            )
        else:
            WorkspaceCoreStore(db).put("WS_SESSION", key, payload, state={"last_seen": at.isoformat()})


def worker_health(db):
    row = db.execute(
        "SELECT record_id,payload,state,clock_timestamp()-updated_at AS age FROM {oa} "
        "WHERE record_type='WS_SESSION' AND workspace_key='ai.worker.vwp'"
    ).fetchone()
    if row:
        require(
            row["record_id"] == record_id("WS_SESSION", "ai.worker.vwp")
            and row["payload"] == {"namespace": "ai-worker-v1", "site": "vwp", "expires_at": "1970-01-01T00:00:00+00:00"},
            "AI_MONITOR_CORRUPT",
        )
    return {
        "site": "vwp",
        "online": bool(row and 0 <= row["age"].total_seconds() <= 90),
        "last_seen": row["state"]["last_seen"] if row else None,
    }
