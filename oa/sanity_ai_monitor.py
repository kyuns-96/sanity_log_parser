"""Small, immutable progress snapshots. Never load model input on web polling.

Snapshots are display data only. Authorization, dispatch and result acceptance
always use the original job/input ledgers. No log bodies or credentials belong
here. A missing observation cannot make an uncertain call safe to repeat.
"""
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import Database,lock_key,json_value
from common.sanity_ai_execution import worker_site
from common.sanity_vwp_ai import require
from oa.sanity_workspace import WorkspaceStore, record_id

PREFIX, NAMESPACE = "ai.live.", "ai-live-v1"


def record(service, identity, command, target, public, *, phase=None, call_id=None):
    from oa.sanity_ai_jobs import _db_now
    service.db.execute("SELECT pg_advisory_xact_lock(%s)",(lock_key(PREFIX+identity),))
    row=service.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
        "ORDER BY workspace_key DESC LIMIT 1",(PREFIX+identity+".%",)).fetchone()
    previous=validate(row) if row else None
    at = _db_now(service.db)
    value = {"namespace":NAMESPACE, "id":identity, "actor_id":command["user_id"], "target":target,
        "sequence":previous["sequence"]+1 if previous else 1,"previous_sha256":previous["sha256"] if previous else None,
        "command":{k:command[k] for k in ("block_id","version_id","report_id","group_id")},
        "at":at.isoformat(), "job":public}
    if phase:value["phase"] = phase
    if call_id:value["call_id"] = call_id
    value["sha256"] = digest(value)
    require(len(canonical_json(value)) <= 16384,"AI_OUTPUT_LIMIT")
    key = PREFIX + identity + "." + f"{value['sequence']:010d}"
    service.ws.put("WS_CONFIG",key,value)


def capture(service, event, *, phase=None, call_id=None):
    # Rendering a batch reads its last small checkpoint, not completed bodies.
    public = service.public(event)
    record(service,event["job_id"],event["data"]["command"],event["data"]["target"],public,
           phase=phase,call_id=call_id)


def observer(db_factory,workspace_factory,advisor,criteria_provider,event,call_id):
    def observe(phase):
        from oa.sanity_ai_jobs import _transaction
        try:
            with _transaction(db_factory,workspace_factory,advisor,criteria_provider) as service:
                service.db.execute("SELECT pg_advisory_xact_lock(%s)",(lock_key("ai1.job."+event["job_id"]),))
                # Only read this job's latest small display snapshot. Do not
                # acquire review locks while a model connection is open.
                previous = Monitor(service).latest(identity=event["job_id"])
                if (previous and previous["job"]["status"] == "RUNNING"
                        and previous['job'].get('resume_count',0)==event['data'].get('resume_count',0)):
                    record(service,event["job_id"],event["data"]["command"],event["data"]["target"],
                           previous["job"],phase=phase,call_id=call_id)
        except Exception:
            # Observability never changes dispatch certainty or result policy.
            pass
    return observe


def validate(row):
    value=row["payload"]
    require(isinstance(value,dict) and value.get("namespace")==NAMESPACE
        and type(value.get("sequence")) is int and value["sequence"]>=1
        and row["workspace_key"]==PREFIX+value["id"]+"."+f"{value['sequence']:010d}"
        and value.get("sha256")==digest({k:v for k,v in value.items() if k!="sha256"})
        and row["record_id"]==record_id("WS_CONFIG",row["workspace_key"])
        and row["state"]=={} and row["version"]==0 and row["created_at"]==row["updated_at"],"AI_MONITOR_CORRUPT")
    return value


class Monitor:
    def __init__(self,service):
        self.service,self.db=service,service.db
        self._unknown_events={}

    def display_unknown(self,value):
        """Project UNKNOWN snapshots using the authoritative small job ledger.

        This is display-only: no source, cases or model inputs are hydrated and
        no new observation is written. Other snapshot states remain unchanged.
        """
        job=value['job']
        if job.get('status')!='UNKNOWN':
            return value
        identity=value.get('id',job.get('id'))
        event=self.service._load(identity,required=False)
        require(event is not None and event['job_id']==identity
                and event['data']['command']['user_id']==value.get('actor_id')
                and event['data']['target']==value.get('target'),'AI_MONITOR_CORRUPT')
        command=value.get('command')
        if command is not None:
            require(all(command.get(k)==event['data']['command'].get(k) for k in
                        ('block_id','version_id','report_id','group_id')),'AI_MONITOR_CORRUPT')
        self._unknown_events[identity]=event
        return {**value,'job':self.service.public(event)}

    def unknown_availability(self,value,user_id,meta):
        job=value['job']
        if job.get('status')!='UNKNOWN':return job
        event=self._unknown_events.get(value.get('id',job.get('id')))
        require(event is not None,'AI_MONITOR_CORRUPT')
        from oa.sanity_ai_unknown_recovery import unknown_availability
        available,error=unknown_availability(self.service,event,user_id,meta,job)
        return {**job,'can_retry_unknown':available,**({'unknown_recovery_error':error} if error else {})}

    def latest(self, *, identity=None,target=None):
        query="SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
        params=[PREFIX+(identity+"." if identity else "")+"%"]
        if target:
            query+="AND payload->>'target'=%s ";params.append(target)
        row=self.db.execute(query+"ORDER BY workspace_key DESC LIMIT 1" if identity else
            query+"ORDER BY created_at DESC,workspace_key DESC LIMIT 1",params).fetchone()
        return self.display_unknown(validate(row)) if row else None

    def legacy(self,target):
        """Bounded display projection for jobs written before snapshots existed."""
        row=self.db.execute("SELECT payload->>'job_id' AS id,payload->>'status' AS status,"
            "payload->'data'->'command'->>'user_id' AS actor_id,payload->'data'->>'target' AS target,"
            "jsonb_build_object('model',payload->'data'->'model','created_at',payload->'data'->'created_at',"
            "'finished_at',payload->'data'->'finished_at','error',payload->'data'->'error',"
            "'connection_id',payload->'data'->'command'->'connection_id','connection_label',payload->'data'->'connection_label',"
            "'expected_revision',payload->'data'->'command'->'expected_revision',"
            "'input_case_count',jsonb_array_length(payload->'data'->'pins'->'cases'),"
            "'model_called',payload->'data'->'model_called','model_attempted',payload->'data'->'model_attempted') AS job "
            "FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
            "AND payload->>'namespace'='oa-ai-v1' AND payload->'data'->>'target'=%s "
            "ORDER BY created_at DESC,workspace_key DESC LIMIT 1",("ai1.job.%",target)).fetchone()
        return self.display_unknown({"id":row['id'],"actor_id":row['actor_id'],"target":row['target'],
            "job":{**row["job"],"id":row["id"],"status":row["status"]}}) if row else None

    def items(self,user_id,*,limit=12):
        from oa.sanity_ai_submissions import header
        require(type(limit) is int and 1<=limit<=20,"INVALID_REQUEST")
        rows=self.db.execute("SELECT * FROM (SELECT DISTINCT ON (payload->>'id') * FROM {oa} "
            "WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s AND payload->>'actor_id'=%s "
            "ORDER BY payload->>'id',workspace_key DESC) latest ORDER BY created_at DESC LIMIT %s",
            (PREFIX+"%",user_id,limit)).fetchall()
        items=[]
        for row in rows:
            value=self.display_unknown(validate(row))
            try:meta=header(self.service,{**value["command"],"user_id":user_id})
            except ReviewError as error:
                if error.code in ("FORBIDDEN","UNAUTHORIZED","NOT_FOUND","RESOURCE_NOT_FOUND"):continue
                raise
            job=self.unknown_availability(value,user_id,meta)
            items.append({**job,"observed_at":value["at"],"phase":value.get("phase"),
                "report":{**value["command"],**{k:meta[k] for k in ("project_id","item_id","family")}},
                "can_cancel":meta["can_review"] and job["status"] in ("PENDING","RUNNING","RESULT_READY")
                    and not job.get('progress',{}).get('cancel_requested',False),
                "can_resume":bool(meta["can_review"] and meta["ready"] and meta["group_ready"] and job.get("resumable"))})
        return {"items":items,"worker":worker_health(self.db),"server_time":self.db.execute(
            "SELECT clock_timestamp() AS now").fetchone()["now"].isoformat()}


def heartbeat(db_factory=None):
    """One mutable service-session marker per site; no DELETE/DDL privilege.

    Its dotted key cannot be a login token hash. The payload is expired even
    if it were ever offered to the legacy session reader. No user session or
    production authentication state is created here.
    """
    from oa.sanity_ai_jobs import _db_now
    factory=db_factory or (lambda:Database("oa"))
    with factory() as db,db.transaction():
        at=_db_now(db);site=worker_site()
        key="ai.worker."+site
        db.execute("SELECT pg_advisory_xact_lock(%s)",(lock_key(key),))
        payload={"namespace":"ai-worker-v1","site":site,"expires_at":"1970-01-01T00:00:00+00:00"}
        row=db.execute("SELECT * FROM {oa} WHERE record_type='WS_SESSION' AND workspace_key=%s",(key,)).fetchone()
        if row:
            require(row["record_id"]==record_id("WS_SESSION",key) and row["payload"]==payload,"AI_MONITOR_CORRUPT")
            db.execute("UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() "
                       "WHERE record_type='WS_SESSION' AND workspace_key=%s",(json_value({"last_seen":at.isoformat()}),key))
        else:WorkspaceStore(db).put("WS_SESSION",key,payload,state={"last_seen":at.isoformat()})


def worker_health(db):
    row=db.execute("SELECT record_id,payload,state,clock_timestamp()-updated_at AS age FROM {oa} "
        "WHERE record_type='WS_SESSION' AND workspace_key='ai.worker.vwp'").fetchone()
    if row:require(row["record_id"]==record_id("WS_SESSION","ai.worker.vwp") and
        row["payload"]=={"namespace":"ai-worker-v1","site":"vwp","expires_at":"1970-01-01T00:00:00+00:00"},"AI_MONITOR_CORRUPT")
    return {"site":"vwp","online":bool(row and 0<=row["age"].total_seconds()<=90),
            "last_seen":row["state"]["last_seen"] if row else None}
