"""Explicit, idempotent resume of committed safe AI checkpoints."""
from datetime import timedelta
import re

from common.sanity_common import digest
from common.sanity_db import lock_key, uuid_value
from common.sanity_submission import check
from oa.sanity_ai_retry import MAX_RESUMES, resumable
from oa.sanity_workspace import record_id

PREFIX = 'ai.resume.'
NAMESPACE = 'oa-ai-resume-v1'


def validate_resume(service, previous, status, data):
    """Validate the terminal-to-active ledger transition without reading input."""
    if previous['data'].get('batch', {}).get('execution') == 'parallel-v1':
        from oa.sanity_ai_parallel_recovery import validate_resume as parallel_validate
        return parallel_validate(service, previous, status, data)
    batch = 'batch' in previous['data']
    checkpoint = data.get('resume_checkpoint')
    ready = False
    if batch:
        from oa.sanity_ai_batch_jobs import Ledger
        check(isinstance(checkpoint,dict) and set(checkpoint)=={'key','sha256'}
              and isinstance(checkpoint['key'],str) and re.fullmatch(r'step\.\d{8}',checkpoint['key'])
              and isinstance(checkpoint['sha256'],str) and re.fullmatch(r'[a-f0-9]{64}',checkpoint['sha256']),
              'AI_JOB_CORRUPT')
        ledger = Ledger(service,previous)
        step,_ = ledger.get(checkpoint['key'],checkpoint)
        sequence = step.get('sequence')
        check(type(sequence) is int and sequence > 1 and checkpoint['key']==f'step.{sequence:08d}'
              and step.get('resume')=={'request_id':data.get('resume_request_id'),
                                       'actor_id':previous['data']['command']['user_id']},'AI_JOB_CORRUPT')
        before,before_ref = ledger.get(f'step.{sequence-1:08d}')
        check(isinstance(before,dict) and isinstance(before.get('state'),dict)
              and before.get('status')=='READY' and step.get('previous_sha256')==before_ref['sha256']
              and step.get('state')=={**before['state'],'child_retries':0,'retry_after':None},'AI_JOB_CORRUPT')
        ready = step.get('status') == 'READY'
    else:
        check(checkpoint is None,'AI_JOB_CORRUPT')
    check(status == ('RUNNING' if batch else 'PENDING') and resumable(previous,checkpoint_ready=ready)
          and type(data.get('resume_count')) is int
          and data['resume_count']==previous['data'].get('resume_count',0)+1
          and data['resume_count']<=MAX_RESUMES
          and data.get('resumed_by')==previous['data']['command']['user_id']
          and isinstance(data.get('resume_request_id'),str)
          and str(uuid_value(data['resume_request_id']))==data['resume_request_id']
          and data.get('retry_count',0)==0 and 'finished_at' not in data and 'error' not in data,
          'AI_JOB_CORRUPT')


def marker(service, request_id):
    from oa.sanity_ai_jobs import _NULL_COLUMNS
    key=PREFIX+request_id
    row=service.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",(key,)).fetchone()
    if not row:return None
    value=row['payload']
    check(isinstance(value,dict) and set(value)=={'namespace','id','job_id','actor_id','sha256'}
          and value['namespace']==NAMESPACE and value['id']==request_id
          and value['sha256']==digest({k:v for k,v in value.items() if k!='sha256'})
          and row['record_id']==record_id('WS_CONFIG',key) and row['state']=={} and row['version']==0
          and row['status']=='RECORDED' and row['created_at']==row['updated_at']
          and all(row.get(k) is None for k in _NULL_COLUMNS), 'AI_JOB_CORRUPT')
    return value


def resume(service, user_id, job_id, request_id):
    from oa.sanity_ai_jobs import _db_now, _timestamp, ACTIVE
    from oa.sanity_ai_submissions import Submissions, header, job_lock, PREFIX as INTENT_PREFIX, ACTIVE as INTENT_ACTIVE

    job_id,request_id=str(uuid_value(job_id,'job_id')),str(uuid_value(request_id,'request_id'))
    service.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(PREFIX+request_id),))
    job_lock(service,job_id)
    event=service._load(job_id)
    if event['data'].get('batch', {}).get('execution') == 'parallel-v1':
        from oa.sanity_ai_parallel_recovery import resume as parallel_resume
        return parallel_resume(service,user_id,job_id,request_id)
    command=event['data']['command']
    check(command['user_id']==user_id,'FORBIDDEN')
    meta=header(service,{**command,'user_id':user_id},write=True)
    old=marker(service,request_id)
    if old:
        check(old['job_id']==job_id and old['actor_id']==user_id,'REQUEST_CONFLICT')
        return service.public(event)
    from oa.sanity_ai_unknown_recovery import marker as unknown_marker
    check(unknown_marker(service,request_id) is None,'REQUEST_CONFLICT')
    check(meta['ready'] and meta['can_review'] and meta['group_ready'],'AI_INPUT_STALE')
    target=event['data']['target']
    service.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(INTENT_PREFIX+target),))
    latest=service._latest(target=target)
    intent=Submissions(service).latest(target=target)
    check(not latest or latest['job_id']==job_id or latest['status'] not in ACTIVE,'AI_JOB_ACTIVE')
    check(not intent or intent['id']==job_id or intent['status'] not in INTENT_ACTIVE,'AI_JOB_ACTIVE')

    batch='batch' in event['data']
    step=ref=ledger=None
    if batch:
        from oa.sanity_ai_batch_jobs import Ledger
        ledger=Ledger(service,event)
        step,ref=ledger.last()
    check(resumable(event,checkpoint_ready=bool(step and step['status']=='READY')),'AI_RESUME_UNAVAILABLE')
    service._check_credential_pin(event)
    data={k:v for k,v in event['data'].items() if k not in
          ('finished_at','cancelled_by','error','retry_after','retry_failure','resume_checkpoint')}
    data.update(resume_count=event['data'].get('resume_count',0)+1,
                resume_request_id=request_id,resumed_by=user_id,retry_count=0)
    now=_db_now(service.db)
    if batch:
        # Reset this child's per-cycle retries only. Global failed_calls and all
        # completed result references remain bound to the original job.
        _,checkpoint=ledger.step(step,ref,'READY',{**step['state'],'child_retries':0,'retry_after':None},
                                 resume={'request_id':request_id,'actor_id':user_id})
        duration=_timestamp(event['data']['deadline'])-_timestamp(event['data']['started_at'])
        check(timedelta(seconds=60)<=duration<=timedelta(seconds=604800),'AI_JOB_CORRUPT')
        data.update(resume_checkpoint=checkpoint,started_at=now.isoformat(),deadline=(now+duration).isoformat())
        status='RUNNING'
    else:
        for key in ('call_token','started_at','deadline'):
            data.pop(key,None)
        # This new pending cycle has not dispatched a call. Earlier attempts
        # remain recorded in the immutable preceding events.
        data.update(model_called=False,model_attempted=False)
        status='PENDING'
    result=service._append(event,status,data,now=now)
    value={'namespace':NAMESPACE,'id':request_id,'job_id':job_id,'actor_id':user_id}
    value['sha256']=digest(value)
    service.ws.put('WS_CONFIG',PREFIX+request_id,value)
    return service.public(result)
