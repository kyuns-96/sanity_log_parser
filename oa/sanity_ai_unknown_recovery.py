"""Explicit user acceptance of duplicate execution for an uncertain batch call."""
from datetime import timedelta
import re

from common.sanity_common import ReviewError, digest
from common.sanity_db import lock_key, uuid_value
from common.sanity_submission import check
from oa.sanity_ai_retry import MAX_RESUMES
from oa.sanity_workspace import record_id

PREFIX = 'ai.unknown.'
NAMESPACE = 'oa-ai-unknown-recovery-v1'

AVAILABILITY_ERRORS = frozenset(('AI_INPUT_STALE','AI_CREDENTIAL_CHANGED','AI_CONFIG_CHANGED','AI_BATCH_LIMIT',
    'AI_VWP_PROFILE_UNAVAILABLE','AI_CONNECTION_UNAVAILABLE','AI_TOKEN_STORE_NOT_CONFIGURED',
    'FORBIDDEN','UNAUTHORIZED','AI_RESUME_UNAVAILABLE'))


def recovery_info(event, step, ref):
    data=event['data']
    if (event['status']!='UNKNOWN' or not data.get('fail_safe') or 'batch' not in data
            or data.get('resume_count',0)>=MAX_RESUMES or not isinstance(step,dict)
            or step.get('status')!='RESERVED' or not isinstance(ref,dict)):
        return None
    state=step.get('state')
    if not isinstance(state,dict):return None
    if not (isinstance(step.get('call_token'),str) and re.fullmatch(r'[0-9a-f-]{36}',step['call_token'])
            and type(step.get('input_bytes')) is int and 0<step['input_bytes']<=67108864
            and all(type(state.get(k)) is int and state[k]>=0 for k in ('round','index','round_total'))
            and state['index']<state['round_total']):return None
    phase=state.get('phase','CLASSIFY' if state['round']==0 else 'MERGE')
    if phase not in ('TEXT','TEXT_MERGE','CLASSIFY','MERGE','REVIEW','REVIEW_MERGE'):return None
    return {'event_sha256':event['sha256'],'step_sha256':ref['sha256'],'call_token':step['call_token'],
            'phase':phase,'round':state['round'],'index':state['index'],'total':state['round_total'],
            'input_bytes':step['input_bytes']}


def unknown_availability(service,event,user_id,meta,job):
    """Display-only lightweight preflight; dispatch still revalidates inputs."""
    if event['data'].get('batch', {}).get('execution') == 'parallel-v1':
        from oa.sanity_ai_parallel_recovery import unknown_availability as parallel_availability
        return parallel_availability(service,event,user_id,meta,job)
    if event['status']!='UNKNOWN' or 'batch' not in event['data']:
        return False,None
    if event['data']['command']['user_id']!=user_id or not meta['can_review']:
        return False,'FORBIDDEN'
    try:
        from oa.sanity_ai_batch_jobs import Ledger,attempts
        from oa.sanity_ai_submissions import Submissions,header
        current=header(service,{**event['data']['command'],'user_id':user_id})
        check(current['can_review'],'FORBIDDEN')
        check(current['ready'] and current['group_ready'],'AI_INPUT_STALE')
        step,ref=Ledger(service,event).last()
        info=recovery_info(event,step,ref)
        check(info is not None and info==job.get('unknown_recovery'),'AI_RESUME_UNAVAILABLE')
        intent=Submissions(service).load(event['job_id'],required=False)
        if intent:check(intent['data']['snapshot']==current['snapshot'],'AI_INPUT_STALE')
        service._check_credential_pin(event)
        advisor=service._job_advisor(event)
        check(advisor.describe()['config_sha256']==event['data']['pins']['config_sha256'],'AI_CONFIG_CHANGED')
        check(attempts(step['state'])+1<advisor.settings.batch.max_calls,'AI_BATCH_LIMIT')
        return True,None
    except ReviewError as error:
        if error.code not in AVAILABILITY_ERRORS:raise
        return False,error.code


def marker(service, request_id):
    from oa.sanity_ai_jobs import _NULL_COLUMNS
    key=PREFIX+request_id
    row=service.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",(key,)).fetchone()
    if not row:return None
    value=row['payload']
    check(isinstance(value,dict) and set(value)=={'namespace','id','job_id','actor_id','event_sha256','step_sha256','sha256'}
          and value['namespace']==NAMESPACE and value['id']==request_id
          and value['sha256']==digest({k:v for k,v in value.items() if k!='sha256'})
          and row['record_id']==record_id('WS_CONFIG',key) and row['state']=={} and row['version']==0
          and row['status']=='RECORDED' and row['created_at']==row['updated_at']
          and all(row.get(k) is None for k in _NULL_COLUMNS),'AI_JOB_CORRUPT')
    return value


def validate_recovery(service, previous, status, data):
    if previous['data'].get('batch', {}).get('execution') == 'parallel-v1':
        from oa.sanity_ai_parallel_recovery import validate_recovery as parallel_validate
        return parallel_validate(service,previous,status,data)
    from oa.sanity_ai_batch_jobs import Ledger, attempts
    check(previous['status']=='UNKNOWN' and status=='RUNNING' and 'batch' in previous['data']
          and previous['data'].get('fail_safe') is True and type(data.get('resume_count')) is int
          and data['resume_count']==previous['data'].get('resume_count',0)+1 and data['resume_count']<=MAX_RESUMES
          and data.get('resumed_by')==previous['data']['command']['user_id']
          and isinstance(data.get('resume_request_id'),str)
          and str(uuid_value(data['resume_request_id']))==data['resume_request_id']
          and data.get('retry_count',0)==0 and all(k not in data for k in ('error','finished_at','retry_after','retry_failure','unknown_reason')),
          'AI_JOB_CORRUPT')
    checkpoint=data.get('resume_checkpoint')
    check(isinstance(checkpoint,dict) and set(checkpoint)=={'key','sha256'}
          and isinstance(checkpoint['key'],str) and re.fullmatch(r'step\.\d{8}',checkpoint['key'])
          and isinstance(checkpoint['sha256'],str) and re.fullmatch(r'[a-f0-9]{64}',checkpoint['sha256']),'AI_JOB_CORRUPT')
    ledger=Ledger(service,previous);after,_=ledger.get(checkpoint['key'],checkpoint)
    sequence=after.get('sequence')
    check(type(sequence) is int and sequence>1 and checkpoint['key']==f'step.{sequence:08d}','AI_JOB_CORRUPT')
    before,ref=ledger.get(f'step.{sequence-1:08d}')
    check(recovery_info(previous,before,ref) is not None,'AI_JOB_CORRUPT')
    check(isinstance(before,dict) and before.get('status')=='RESERVED' and isinstance(before.get('state'),dict)
          and after.get('status')=='READY' and after.get('previous_sha256')==ref['sha256']
          and after.get('state')=={**before['state'],'unknown_calls':before['state'].get('unknown_calls',0)+1,
                                  'child_retries':0,'retry_after':None}
          and after.get('unknown_resolution')=={'actor_id':data['resumed_by'],'request_id':data['resume_request_id'],
              'event_sha256':previous['sha256'],'step_sha256':ref['sha256'],'call_token':before.get('call_token'),
              'input_sha256':before.get('input_sha256'),'acknowledge_duplicate':True},'AI_JOB_CORRUPT')
    attempts(before['state'])


def retry_unknown(service,user_id,job_id,request_id,expected_event_sha256,expected_step_sha256,acknowledge_duplicate):
    from oa.sanity_ai_jobs import _db_now,_timestamp,ACTIVE
    from oa.sanity_ai_batch_jobs import Ledger, attempts
    from oa.sanity_ai_resume import PREFIX as RESUME_PREFIX, marker as resume_marker
    from oa.sanity_ai_submissions import Submissions,header,job_lock,PREFIX as INTENT_PREFIX,ACTIVE as INTENT_ACTIVE

    check(acknowledge_duplicate is True and all(isinstance(v,str) and re.fullmatch(r'[0-9a-f]{64}',v)
          for v in (expected_event_sha256,expected_step_sha256)),'INVALID_REQUEST')
    job_id,request_id=str(uuid_value(job_id,'job_id')),str(uuid_value(request_id,'request_id'))
    service.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(RESUME_PREFIX+request_id),))
    job_lock(service,job_id)
    event=service._load(job_id);command=event['data']['command']
    if event['data'].get('batch', {}).get('execution') == 'parallel-v1':
        from oa.sanity_ai_parallel_recovery import retry_unknown as parallel_retry
        return parallel_retry(service,user_id,job_id,request_id,expected_event_sha256,expected_step_sha256,acknowledge_duplicate)
    check(command['user_id']==user_id,'FORBIDDEN')
    meta=header(service,{**command,'user_id':user_id},write=True)
    old=marker(service,request_id)
    if old:
        check(old['job_id']==job_id and old['actor_id']==user_id and old['event_sha256']==expected_event_sha256
              and old['step_sha256']==expected_step_sha256,'REQUEST_CONFLICT')
        return service.public(event)
    check(resume_marker(service,request_id) is None,'REQUEST_CONFLICT')
    check(meta['ready'] and meta['can_review'] and meta['group_ready'],'AI_INPUT_STALE')
    target=event['data']['target']
    service.db.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key(INTENT_PREFIX+target),))
    latest=service._latest(target=target);intent=Submissions(service).latest(target=target)
    check(not latest or latest['job_id']==job_id or latest['status'] not in ACTIVE,'AI_JOB_ACTIVE')
    check(not intent or intent['id']==job_id or intent['status'] not in INTENT_ACTIVE,'AI_JOB_ACTIVE')
    check(event['status']=='UNKNOWN' and 'batch' in event['data'],'AI_RESUME_UNAVAILABLE')
    ledger=Ledger(service,event);step,ref=ledger.last()
    info=recovery_info(event,step,ref)
    check(info is not None,'AI_RESUME_UNAVAILABLE')
    check(event['sha256']==expected_event_sha256 and ref['sha256']==expected_step_sha256,'REVISION_CONFLICT')
    original_intent=Submissions(service).load(job_id,required=False)
    if original_intent:check(original_intent['data']['snapshot']==meta['snapshot'],'AI_INPUT_STALE')
    service._check_credential_pin(event)
    advisor=service._job_advisor(event)
    check(advisor.describe()['config_sha256']==event['data']['pins']['config_sha256'],'AI_CONFIG_CHANGED')
    check(attempts(step['state'])+1<advisor.settings.batch.max_calls,'AI_BATCH_LIMIT')
    state={**step['state'],'unknown_calls':step['state'].get('unknown_calls',0)+1,'child_retries':0,'retry_after':None}
    resolution={'actor_id':user_id,'request_id':request_id,'event_sha256':event['sha256'],
        'step_sha256':ref['sha256'],'call_token':step['call_token'],'input_sha256':step['input_sha256'],
        'acknowledge_duplicate':True}
    _,checkpoint=ledger.step(step,ref,'READY',state,unknown_resolution=resolution)
    data={k:v for k,v in event['data'].items() if k not in
          ('finished_at','cancelled_by','error','retry_after','retry_failure','resume_checkpoint','unknown_reason')}
    data.update(resume_count=event['data'].get('resume_count',0)+1,resume_request_id=request_id,
                resumed_by=user_id,retry_count=0,resume_checkpoint=checkpoint)
    now=_db_now(service.db)
    duration=_timestamp(event['data']['deadline'])-_timestamp(event['data']['started_at'])
    check(timedelta(seconds=60)<=duration<=timedelta(seconds=604800),'AI_JOB_CORRUPT')
    data.update(started_at=now.isoformat(),deadline=(now+duration).isoformat())
    result=service._append(event,'RUNNING',data,now=now)
    value={'namespace':NAMESPACE,'id':request_id,'job_id':job_id,'actor_id':user_id,
           'event_sha256':expected_event_sha256,'step_sha256':expected_step_sha256}
    value['sha256']=digest(value)
    service.ws.put('WS_CONFIG',PREFIX+request_id,value)
    return service.public(result)
