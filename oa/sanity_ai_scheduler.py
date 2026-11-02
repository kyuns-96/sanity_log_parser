"""FIFO job leases and shared model-call slots, without connections across HTTP."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from common.sanity_db import json_value, lock_key, uuid_value
from common.sanity_submission import check
from oa.sanity_workspace import WorkspaceStore, record_id

CONFIG = 'ai.scheduler.config.'
RUNTIME = 'ai.scheduler.runtime'
NAMESPACE = 'oa-ai-scheduler-v1'
RUNTIME_PAYLOAD = {'namespace':NAMESPACE,'expires_at':'1970-01-01T00:00:00+00:00'}
MAX_SLOTS = 1000000
DEFAULTS = {'max_concurrency':8,'max_jobs':4}
_lease = ContextVar('sanity_ai_scheduler_lease',default=None)


def limits(value, code='AI_SCHEDULER_CONFIG'):
    check(isinstance(value,dict) and set(value)==set(DEFAULTS) and all(
        type(value[k]) is int and 1 <= value[k] <= MAX_SLOTS for k in DEFAULTS),code)
    return value


class Settings:
    def __init__(self,db):self.db=db

    def current(self):
        row=self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
            "ORDER BY workspace_key DESC LIMIT 1",(CONFIG+'%',)).fetchone()
        if row is None:
            return {'revision':0,'limits':dict(DEFAULTS)}
        value=row['payload']
        check(isinstance(value,dict) and type(value.get('revision')) is int
              and 1<=value['revision']<=9999999999
              and row['workspace_key']==CONFIG+f"{value['revision']:010d}"
              and row['record_id']==record_id('WS_CONFIG',row['workspace_key']), 'AI_SCHEDULER_CORRUPT')
        return {'revision':value['revision'],'limits':limits(value.get('limits'),'AI_SCHEDULER_CORRUPT')}

    def read(self,actor):
        from oa.sanity_ai_task_defaults import TaskDefaults
        TaskDefaults._admin(actor,self.db)
        return self.current()

    def save(self,actor,value,expected_revision):
        from oa.sanity_ai_task_defaults import TaskDefaults
        TaskDefaults._admin(actor,self.db)
        value=limits(value,'INVALID_REQUEST')
        check(type(expected_revision) is int and 0<=expected_revision<9999999999,'INVALID_REQUEST')
        self.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(CONFIG),))
        current=self.current();check(current['revision']==expected_revision,'REVISION_CONFLICT')
        body={'revision':expected_revision+1,'actor_id':actor.user_id,'limits':value}
        WorkspaceStore(self.db).put('WS_CONFIG',CONFIG+f"{body['revision']:010d}",body)
        return {'revision':body['revision'],'limits':value}


def _state(db):
    from oa.sanity_ai_jobs import _db_now,_timestamp
    db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(RUNTIME),))
    row=db.execute("SELECT * FROM {oa} WHERE record_type='WS_SESSION' AND workspace_key=%s",(RUNTIME,)).fetchone()
    if row is None:
        # Like the worker heartbeat, this is an explicitly expired service
        # session. Its dotted key cannot equal any login token's SHA-256 key.
        # WS_CONFIG remains immutable; no existing DB constraint is relaxed.
        WorkspaceStore(db).put('WS_SESSION',RUNTIME,RUNTIME_PAYLOAD,state={'jobs':{},'calls':{}})
        row=db.execute("SELECT * FROM {oa} WHERE record_type='WS_SESSION' AND workspace_key=%s",(RUNTIME,)).fetchone()
    check(row['record_id']==record_id('WS_SESSION',RUNTIME) and row['payload']==RUNTIME_PAYLOAD
          and row['status']=='RECORDED' and set(row['state'])=={'jobs','calls'},'AI_SCHEDULER_CORRUPT')
    state=row['state'];at=_db_now(db)
    check(all(isinstance(state[k],dict) and len(state[k])<=MAX_SLOTS for k in state),'AI_SCHEDULER_CORRUPT')
    for identity,value in state['jobs'].items():
        check(str(uuid_value(identity))==identity and set(value)=={'owner','until','submitted_at'}
              and str(uuid_value(value['owner']))==value['owner'],'AI_SCHEDULER_CORRUPT')
        _timestamp(value['submitted_at']);_timestamp(value['until'])
    for token,value in state['calls'].items():
        check(str(uuid_value(token))==token and set(value)=={'job_id','owner','deadline'}
              and str(uuid_value(value['job_id']))==value['job_id']
              and str(uuid_value(value['owner']))==value['owner'],'AI_SCHEDULER_CORRUPT')
        _timestamp(value['deadline'])
    # A crashed process cannot keep a job forever. Dispatch certainty still
    # comes from the authoritative call ledger, never these scheduling leases.
    state['jobs']={k:v for k,v in state['jobs'].items() if _timestamp(v['until'])>at}
    state['calls']={k:v for k,v in state['calls'].items() if _timestamp(v['deadline'])>at}
    return state,at


def _save(db,state):
    db.execute("UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() "
               "WHERE record_type='WS_SESSION' AND workspace_key=%s",(json_value(state),RUNTIME))


@contextmanager
def executing_job(identity,owner):
    token=_lease.set((identity,owner))
    try:yield
    finally:_lease.reset(token)


def allocation(service,identity):
    lease=_lease.get()
    if lease is None:return None
    state,_=_state(service.db)
    current=state['jobs'].get(identity)
    if lease[0]!=identity or current is None or current['owner']!=lease[1]:return 0
    budget=Settings(service.db).current()['limits']['max_concurrency']
    ordered=sorted(state['jobs'],key=lambda key:(state['jobs'][key]['submitted_at'],key))
    share,extra=divmod(budget,len(ordered))
    return share+int(ordered.index(identity)<extra)


def reserve(service,identity,token,deadline):
    lease=_lease.get()
    if lease is None:return True
    width=allocation(service,identity)
    if not width:return False
    state,_=_state(service.db);budget=Settings(service.db).current()['limits']['max_concurrency']
    if len(state['calls'])>=budget or sum(v['job_id']==identity for v in state['calls'].values())>=width:
        return False
    check(token not in state['calls'],'AI_SCHEDULER_CORRUPT')
    state['calls'][token]={'job_id':identity,'owner':lease[1],'deadline':deadline}
    _save(service.db,state)
    return True


def release(service,identity,token):
    if _lease.get() is None:return
    state,_=_state(service.db)
    current=state['calls'].get(token)
    if current is not None:
        check(current['job_id']==identity,'AI_SCHEDULER_CORRUPT')
        del state['calls'][token];_save(service.db,state)


class Scheduler:
    def __init__(self,db_factory,owner,site):
        self.db_factory,self.owner,self.site=db_factory,str(uuid_value(owner)),site

    def claim(self,exclude=()):
        with self.db_factory() as db,db.transaction():
            state,at=_state(db);config=Settings(db).current()['limits']
            if len(state['jobs'])>=min(config['max_jobs'],config['max_concurrency']):
                _save(db,state);return None
            rows=db.execute("WITH jobs AS (SELECT DISTINCT ON (payload->>'job_id') payload FROM {oa} "
                "WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                "AND payload->>'namespace'='oa-ai-v1' ORDER BY payload->>'job_id',workspace_key DESC), "
                "intents AS (SELECT DISTINCT ON (payload->>'id') payload FROM {oa} "
                "WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
                "AND payload->>'namespace'='oa-ai-submission-v1' ORDER BY payload->>'id',workspace_key DESC) "
                "SELECT id,submitted_at FROM (SELECT payload->>'job_id' AS id,payload->'data'->>'created_at' AS submitted_at "
                "FROM jobs WHERE payload->>'status' IN ('PENDING','RUNNING','RESULT_READY') "
                "AND COALESCE(payload->'data'->>'worker_site','oa')=%s UNION ALL "
                "SELECT payload->>'id',payload->'data'->>'created_at' FROM intents i "
                "WHERE %s='vwp' AND payload->>'status' IN ('QUEUED','PREPARING') "
                "AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.payload->>'job_id'=i.payload->>'id')) candidates "
                "WHERE NOT(id=ANY(%s)) ORDER BY submitted_at::timestamptz,id LIMIT 1",
                ('ai1.job.%','ai.intent.%',self.site,self.site,list(set(exclude)|set(state['jobs'])))).fetchall()
            if not rows:_save(db,state);return None
            item=rows[0]
            state['jobs'][item['id']]={'owner':self.owner,'submitted_at':item['submitted_at'],
                                     'until':(at+timedelta(seconds=120)).isoformat()}
            _save(db,state)
            return item['id']

    def renew(self):
        with self.db_factory() as db,db.transaction():
            state,at=_state(db)
            for job in state['jobs'].values():
                if job['owner']==self.owner:job['until']=(at+timedelta(seconds=120)).isoformat()
            _save(db,state)

    def finish(self,identity):
        with self.db_factory() as db,db.transaction():
            state,_=_state(db)
            if state['jobs'].get(identity,{}).get('owner')==self.owner:
                del state['jobs'][identity]
            _save(db,state)


def work_loop(scheduler,process,stop,interval,*,once=False,on_tick=lambda:None,on_result=lambda value:None,
              on_error=lambda code:None):
    """Coordinators run independently; reservations share the DB call budget."""
    from concurrent.futures import ThreadPoolExecutor
    import time
    if once:
        on_tick();identity=scheduler.claim()
        if identity is None:return
        try:on_result(process(identity))
        finally:scheduler.finish(identity)
        return
    futures={};deferred={}
    with ThreadPoolExecutor(max_workers=MAX_SLOTS,thread_name_prefix='ai-job') as executor:
        while not stop.is_set():
            on_tick();at=time.monotonic()
            deferred={key:until for key,until in deferred.items() if until>at}
            for future in list(futures):
                if not future.done():continue
                identity=futures.pop(future)
                try:on_result(future.result())
                except Exception as error:
                    from common.sanity_common import ReviewError
                    on_error(error.code if isinstance(error,ReviewError) else 'AI_JOB_STEP_FAILED')
                finally:scheduler.finish(identity)
                deferred[identity]=time.monotonic()+interval
            admitted=[]
            while not stop.is_set():
                identity=scheduler.claim(set(futures.values())|set(deferred)|set(admitted))
                if identity is None:break
                admitted.append(identity)
            for identity in admitted:futures[executor.submit(process,identity)]=identity
            stop.wait(min(interval,1) if futures else interval)
