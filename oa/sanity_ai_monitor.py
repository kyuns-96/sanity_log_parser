"""Small, immutable progress snapshots. Never load model input on web polling.

Snapshots are display data only. Authorization, dispatch and result acceptance
always use the original job/input ledgers. No log bodies or credentials belong
here. A missing observation cannot make an uncertain call safe to repeat.
"""
from common.sanity_common import ReviewError
from common.sanity_vwp_ai import require
from oa.sanity_ai_live import (
    NAMESPACE, PREFIX, capture, heartbeat, observer, record, validate, worker_health,
)
from oa.sanity_ai_store import (
    deletion_available, deletion_marker, report_header as header,
)
from oa.sanity_ai_unknown_recovery import unknown_availability as check_unknown_availability


class Monitor:
    def __init__(self, service):
        self.service, self.db = service, service.db
        self._unknown_events = {}

    def display_unknown(self, value):
        """Project UNKNOWN snapshots using the authoritative small job ledger.

        This is display-only: no source, cases or model inputs are hydrated and
        no new observation is written. Other snapshot states remain unchanged.
        """
        job = value['job']
        if job.get('status') != 'UNKNOWN':
            return value
        identity = value.get('id', job.get('id'))
        event = self.service._load(identity, required=False)
        if event is None:
            if deletion_marker(self.service, identity) is not None:
                return None
        require(event is not None and event['job_id'] == identity
                and event['data']['command']['user_id'] == value.get('actor_id')
                and event['data']['target'] == value.get('target'), 'AI_MONITOR_CORRUPT')
        command = value.get('command')
        if command is not None:
            require(all(command.get(k) == event['data']['command'].get(k) for k in
                        ('block_id', 'version_id', 'report_id', 'group_id')), 'AI_MONITOR_CORRUPT')
        self._unknown_events[identity] = event
        return {**value, 'job': self.service.public(event)}

    def connection_availability(self, job):
        """Hide resume when its original connection is no longer usable."""
        connection_id = job.get('connection_id')
        if job.get('resumable') is not True or not connection_id:
            return job
        try:
            self.service.connections.get(connection_id)
        except ReviewError as error:
            if error.code != 'AI_CONNECTION_UNAVAILABLE':
                raise
            return {**job, 'resumable': False, 'resume_error': 'AI_CONNECTION_UNAVAILABLE'}
        return job

    def unknown_availability(self, value, user_id, meta):
        job = value['job']
        if job.get('status') != 'UNKNOWN':
            return self.connection_availability(job)
        event = self._unknown_events.get(value.get('id', job.get('id')))
        require(event is not None, 'AI_MONITOR_CORRUPT')
        available, error = check_unknown_availability(self.service, event, user_id, meta, job)
        return {**job, 'can_retry_unknown': available, **({'unknown_recovery_error': error} if error else {})}

    def latest(self, *, identity=None, target=None):
        query = "SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
        params = [PREFIX + (identity + "." if identity else "") + "%"]
        if target:
            query += "AND payload->>'target'=%s "; params.append(target)
        row = self.db.execute(query + "ORDER BY workspace_key DESC LIMIT 1" if identity else
            query + "ORDER BY created_at DESC,workspace_key DESC LIMIT 1", params).fetchone()
        return self.display_unknown(validate(row)) if row else None

    def legacy(self, target):
        """Bounded display projection for jobs written before snapshots existed."""
        row = self.db.execute("SELECT payload->>'job_id' AS id,payload->>'status' AS status,"
            "payload->'data'->'command'->>'user_id' AS actor_id,payload->'data'->>'target' AS target,"
            "jsonb_build_object('model',payload->'data'->'model','created_at',payload->'data'->'created_at',"
            "'finished_at',payload->'data'->'finished_at','error',payload->'data'->'error',"
            "'connection_id',payload->'data'->'command'->'connection_id','connection_label',payload->'data'->'connection_label',"
            "'expected_revision',payload->'data'->'command'->'expected_revision',"
            "'input_case_count',jsonb_array_length(payload->'data'->'pins'->'cases'),"
            "'model_called',payload->'data'->'model_called','model_attempted',payload->'data'->'model_attempted') AS job "
            "FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
            "AND payload->>'namespace'='oa-ai-v1' AND payload->'data'->>'target'=%s "
            "ORDER BY created_at DESC,workspace_key DESC LIMIT 1", ("ai1.job.%", target)).fetchone()
        return self.display_unknown({"id": row['id'], "actor_id": row['actor_id'], "target": row['target'],
            "job": {**row["job"], "id": row["id"], "status": row["status"]}}) if row else None

    def items(self, user_id, *, limit=12):
        require(type(limit) is int and 1 <= limit <= 20, "INVALID_REQUEST")
        rows = self.db.execute("SELECT * FROM (SELECT DISTINCT ON (payload->>'id') * FROM {oa} "
            "WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s AND payload->>'actor_id'=%s "
            "ORDER BY payload->>'id',workspace_key DESC) latest ORDER BY created_at DESC LIMIT %s",
            (PREFIX + "%", user_id, limit)).fetchall()
        items = []
        can_delete_jobs = deletion_available(self.service)
        for row in rows:
            value = self.display_unknown(validate(row))
            if value is None: continue
            try: meta = header(self.service, {**value["command"], "user_id": user_id})
            except ReviewError as error:
                if error.code in ("FORBIDDEN", "UNAUTHORIZED", "NOT_FOUND", "RESOURCE_NOT_FOUND"): continue
                raise
            job = self.unknown_availability(value, user_id, meta)
            items.append({**job, "observed_at": value["at"], "phase": value.get("phase"),
                "report": {**value["command"], **{k: meta[k] for k in ("project_id", "item_id", "family")}},
                "can_cancel": meta["can_review"] and job["status"] in ("PENDING", "RUNNING", "RESULT_READY")
                    and not job.get('progress', {}).get('cancel_requested', False),
                "can_delete": bool(can_delete_jobs and meta["can_review"]),
                **({"delete_error": "AI_JOB_DELETE_NOT_CONFIGURED"} if not can_delete_jobs else {}),
                "can_resume": bool(meta["can_review"] and meta["ready"] and meta["group_ready"] and job.get("resumable"))})
        return {"items": items, "worker": worker_health(self.db), "server_time": self.db.execute(
            "SELECT clock_timestamp() AS now").fetchone()["now"].isoformat()}
