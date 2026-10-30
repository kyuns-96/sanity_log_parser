"""Bounded parallel waves. Only HTTP runs in copied thread contexts.

The coordinator commits each response immediately on a fresh connection. A
wave never starts its successor until every reservation has a known outcome.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import copy
from datetime import timedelta
import hashlib
import time
from uuid import uuid4

from common.sanity_common import ReviewError, digest
from common.sanity_submission import check
from oa.sanity_ai_batch_jobs import _model, _record_plan, _bind_preserved, _finish_round, attempts
from oa.sanity_ai_parallel_ledger import ParallelLedger
from oa.sanity_ai_retry import MAX_RETRIES, receipt as failure_receipt, retry_at, validation_failure

STALE = frozenset(('AI_INPUT_STALE','AI_CASES_STALE','AI_CRITERIA_STALE','REVISION_CONFLICT',
                   'AI_CONFIG_CHANGED','AI_CONNECTION_UNAVAILABLE','AI_CREDENTIAL_CHANGED','UNAUTHORIZED','FORBIDDEN'))


def _wall_metrics(event, state, wave):
    """Use completed wall-clock samples, never summed overlapping call times."""
    from oa.sanity_ai_jobs import _timestamp
    rate = None
    record_sample = state.get('wall_record_sample')
    if isinstance(record_sample,dict):
        seconds, records = record_sample.get('seconds'), record_sample.get('records')
        if (type(seconds) in (int,float) and 0 < seconds <= 604800
                and type(records) is int and 0 < records <= 1000000):
            rate=seconds/records
    timing=state.get('wall_timing')
    if not isinstance(timing,dict) or timing.get('round') != state.get('round') \
            or timing.get('resume_count') != event['data'].get('resume_count',0):
        return rate,None,None
    if (type(timing.get('round')) is not int or type(timing.get('resume_count')) is not int
            or any(type(timing.get(key)) is not int or not 0 <= timing[key] <= 1000000
                   for key in ('base_samples','base_records','base_units'))):
        return rate,None,None
    try:
        started=_timestamp(timing.get('started_at')); measured=_timestamp(timing.get('measured_at'))
        elapsed=(measured-started).total_seconds()
        samples=state.get('round_samples',0)-timing['base_samples']
        records=state.get('records',0)-timing['base_records']
        completed=state.get('index',0)+sum(slot.get('status')=='DONE' for slot in wave)
        units=completed-timing['base_units']
        total=state['round_total']
        total_records=event['data']['batch']['total_records']
        if (not 0 < elapsed <= 604800 or type(samples) is not int or samples < 2
                or type(records) is not int or records < 0 or type(units) is not int or units <= 0
                or type(total) is not int or not 0 <= completed <= total
                or type(total_records) is not int or not 0 <= state['records'] <= total_records):
            return rate,None,None
    except (ReviewError,KeyError,TypeError,ValueError,OverflowError):
        return rate,None,None
    measured_at=timing['measured_at']
    if (event['status']!='RUNNING' or state.get('cancel_requested')
            or any(slot.get('status') in ('FAILED','UNKNOWN') or
                   slot.get('status')=='RETRY' and slot.get('retry_after') for slot in wave)):
        return rate,None,measured_at
    if completed >= total:
        return rate,None,measured_at
    if state.get('phase') in ('CLASSIFY','REVIEW'):
        remaining=total_records-state['records']
        eta=elapsed/records*remaining if records > 0 and remaining > 0 else None
    else:
        eta=elapsed/units*(total-completed)
    return rate,eta,measured_at


def public(service, event):
    from oa.sanity_ai_jobs import _public
    from oa.sanity_ai_parallel_recovery import recovery_info, can_resume
    value = _public(event)
    step, _ = ParallelLedger(service,event).last()
    state = step['state'] if step else {}
    wave = state.get('wave', [])
    active = [slot for slot in wave if slot['status'] == 'RESERVED']
    retry = [slot for slot in wave if slot['status'] == 'RETRY' and slot.get('retry_after')]
    wall_rate,wall_eta,wall_measured_at=_wall_metrics(event,state,wave)
    value['progress'] = {'mode':'BATCH','execution':'parallel-v1',
        'concurrency':event['data']['batch']['max_concurrency'],'inflight_calls':len(active),
        'active_descriptors':[{'index':s['index'],'phase':state.get('phase','CLASSIFY')} for s in active],
        'phase':state.get('phase','CLASSIFY'),'round':state.get('round',0),
        'completed_records':state.get('records',0),'total_records':event['data']['batch'].get('total_records',0),
        'completed_calls':state.get('calls',0),'round_completed':state.get('index',0)+sum(s['status']=='DONE' for s in wave),
        'round_total':state.get('round_total',0),'waiting':bool(active or retry),
        'average_call_seconds':state.get('seconds',0)/state['calls'] if state.get('calls') else None,
        'seconds_per_record':None,'stage_eta_seconds':None,
        'wall_seconds_per_record':wall_rate,'wall_stage_eta_seconds':wall_eta,
        'wall_measured_at':wall_measured_at,
        'retry_count':state.get('failed_calls',0),'unknown_calls':state.get('unknown_calls',0),
        'child_retries':max((slot.get('retries',0) for slot in wave),default=0),
        'retry_limit':MAX_RETRIES,'retry_after':min((slot['retry_after'] for slot in retry),default=None),
        'cancel_requested':state.get('cancel_requested',False)}
    value['resumable'] = bool(step and can_resume(event,state))
    failed_diagnostics=sorted((slot for slot in wave if slot['status'] in ('RETRY','FAILED') and slot.get('error')=='INVALID_RATIONALE'
                               and slot.get('validation_failure') is not None),key=lambda slot:slot['index'])
    if failed_diagnostics and (event['status']=='RUNNING' or
            event['status']=='FAILED' and event['data'].get('error')=='INVALID_RATIONALE'):
        value['validation_failure']=failed_diagnostics[0]['validation_failure']
    info = recovery_info(service,event) if step else None
    if info is not None:
        value['unknown_recovery'],value['unknown_recoveries'] = info
    if active or state.get('unknown_calls'):
        value['model_called'] = None
        value['model_attempted'] = True
    if state.get('calls'):
        value['model_called'] = True; value['model_attempted'] = True
    return value


def _prepare(service, event, now):
    from oa.sanity_ai_jobs import _db_now
    _,context,cases,criteria = service._inputs(event)
    advisor = service._job_advisor(event)
    revision = None
    if service._personal_tokens(event):
        token,revision = service._user_token(event['data']['command']['user_id'],event['data']['command'].get('connection_id'))
        advisor = advisor.with_user_token(token)
    model = _model(advisor,event,context,cases,criteria)
    ledger = ParallelLedger(service,event)
    if event['status'] == 'PENDING':
        if getattr(model,'segments',[]): model.leaf_plan()
        text = model.text_plan() if hasattr(model,'text_plan') else []
        descriptors = text or _record_plan(model)
        plan = ledger.plans(0,descriptors,len(text) or len(model.records))
        at = now or _db_now(service.db)
        batch = {**event['data']['batch'],'plan':plan,'total_records':len(model.records),
                 'leaf_count':len(descriptors),'credential_revision':revision}
        event = service._append(event,'RUNNING',{**event['data'],'batch':batch,'call_token':str(uuid4()),
            'model_called':False,'model_attempted':False,'started_at':at.isoformat(),
            'deadline':(at+timedelta(seconds=model.settings.deadline_seconds)).isoformat()},now=at)
        ledger = ParallelLedger(service,event)
        state = {'round':0,'index':0,'next_index':0,'wave':[],'records':0,'calls':0,'nodes':0,
            'stable':0,'actual_model':None,'round_total':len(descriptors),
            'round_seconds':0,'round_samples':0,'seconds':0,'record_seconds':0,
            'phase':'TEXT' if text else 'REVIEW' if getattr(model,'review',False) else 'CLASSIFY'}
        if batch['version'] == 2: state['stage_round'] = 0
        ledger.step(None,None,'READY',state)
    _bind_preserved(model,ledger,ledger.last()[0]['state'])
    return event,ledger,model,revision


def _outcome(ledger, slot, status, **extra):
    return ledger.put(slot['reservation']['key']+'.outcome',{'kind':'OUTCOME',
        'reservation':slot['reservation'],'call_token':slot['call_token'],'status':status,**extra})


def _settle(service, event, ledger):
    """Keep the parent active until its entire wave has drained."""
    state = ledger.last()[0]['state']; wave = state.get('wave',[])
    if any(slot['status']=='RESERVED' for slot in wave): return event
    unknown = [slot for slot in wave if slot['status']=='UNKNOWN']
    if unknown:
        error = ReviewError('AI_OUTCOME_UNKNOWN','Parallel call completion is unknown')
        error.unknown_reason = unknown[0].get('unknown_reason','TRANSPORT_ERROR')
        return service._failed(event,error,status='UNKNOWN',called=None)
    if state.get('cancel_requested'):
        return service._append(event,'CANCELLED',{**event['data'],'cancelled_by':state['cancelled_by']})
    failed = next((slot for slot in wave if slot['status']=='FAILED'),None)
    if failed:
        if failed.get('receipt'):
            event = {**event,'data':{**event['data'],'retry_failure':failed['receipt']}}
        code = failed.get('error','AI_INTERNAL_ERROR')
        error=ReviewError(code,'Parallel child did not complete')
        if failed.get('validation_failure') is not None:error.validation_failure=failed['validation_failure']
        return service._failed(event,error,
                               status='STALE' if code in STALE else 'FAILED',called=failed.get('model_called'))
    return event


def cancel(service, event, user_id):
    if 'plan' not in event['data']['batch'] or event['status']=='RESULT_READY':
        return service.public(service._append(event,'CANCELLED',{**event['data'],'cancelled_by':user_id}))
    ledger = ParallelLedger(service,event); step,ref = ledger.last()
    if not step['state'].get('cancel_requested'):
        ledger.step(step,ref,'READY',{**step['state'],'cancel_requested':True,'cancelled_by':user_id})
    return service.public(_settle(service,event,ledger))


def _expire(service, event, ledger, current):
    from oa.sanity_ai_jobs import _timestamp
    step,ref = ledger.last(); state = step['state']; wave = []
    expired = 0
    for slot in state.get('wave',[]):
        if slot['status']=='RESERVED' and (_timestamp(slot['deadline'])<=current
                or _timestamp(event['data']['deadline'])<=current):
            _outcome(ledger,slot,'UNKNOWN',error='AI_OUTCOME_UNKNOWN')
            slot = {**slot,'status':'UNKNOWN','unknown_reason':'RESERVED_EXPIRED'}
            expired += 1
        wave.append(slot)
    if expired:
        ledger.step(step,ref,'READY',{**state,'wave':wave,'unknown_calls':state.get('unknown_calls',0)+expired})
    return _settle(service,event,ledger)


def _execute(model, descriptor, nodes, prepared):
    start = time.monotonic()
    try: return model.execute(descriptor,nodes=nodes,prepared_call=prepared),None,time.monotonic()-start
    except Exception as error: return None,error,time.monotonic()-start


def _save(db_factory,workspace_factory,advisor,criteria_provider,job_id,slot,descriptor,revision,result,error,seconds,max_calls):
    from oa.sanity_ai_jobs import _transaction,_db_now,_timestamp,_error_code
    with _transaction(db_factory,workspace_factory,advisor,criteria_provider) as service:
        service._lock(job_id); event = service._load(job_id)
        if event['status']!='RUNNING': return service.public(event)
        ledger = ParallelLedger(service,event); step,ref = ledger.last(); state = step['state']
        current = next((s for s in state['wave'] if s['index']==slot['index']),None)
        if not current or current['status']!='RESERVED' or current.get('call_token')!=slot['call_token']:
            return service.public(event)
        if _timestamp(slot['deadline']) <= _db_now(service.db):
            error = ReviewError('AI_OUTCOME_UNKNOWN','Parallel response deadline exceeded')
            error.unknown_reason = 'RESPONSE_DEADLINE'
        if error is None:
            try:
                service._inputs(event)
                if service._personal_tokens(event):
                    check(service._user_token(event['data']['command']['user_id'],event['data']['command'].get('connection_id'))[1]==revision,'AI_CREDENTIAL_CHANGED')
                actual = result['metadata']['model']
                check(result['metadata']['input_sha256']==slot['input_sha256'],'AI_BATCH_CORRUPT')
                check(state['actual_model'] is None or state['actual_model']==actual,'AI_MODEL_CHANGED')
                result_ref = ledger.put(f"result.{state['round']:03d}.{slot['index']:06d}",result)
                _outcome(ledger,slot,'SUCCESS',result=result_ref)
                slot = {**slot,'status':'DONE','result':result_ref}
                records = descriptor['stop']-descriptor['start'] if descriptor['kind'] in ('LEAF','REVIEW_LEAF') else 0
                state = {**state,'calls':state['calls']+1,'records':state['records']+records,
                    'nodes':state['nodes']+len(result['nodes']),'actual_model':actual,
                    'seconds':state['seconds']+seconds,'round_seconds':state['round_seconds']+seconds,
                    'round_samples':state['round_samples']+1,'record_seconds':state['record_seconds']+(seconds if records else 0)}
                timing=state.get('wall_timing')
                if (isinstance(timing,dict) and timing.get('round')==state['round'] and
                        type(timing.get('round')) is int and type(timing.get('resume_count')) is int and
                        timing.get('resume_count')==event['data'].get('resume_count',0) and
                        all(type(timing.get(key)) is int and 0 <= timing[key] <= 1000000
                            for key in ('base_samples','base_records','base_units'))):
                    try:
                        measured=_db_now(service.db)
                        elapsed=(measured-_timestamp(timing['started_at'])).total_seconds()
                        sample_records=state['records']-timing['base_records']
                        samples=state['round_samples']-timing['base_samples']
                        if 0 < elapsed <= 604800:
                            state={**state,'wall_timing':{**timing,'measured_at':measured.isoformat()}}
                            if records and samples>=2 and sample_records>0:
                                state['wall_record_sample']={'seconds':elapsed,'records':sample_records}
                    except (ReviewError,KeyError,TypeError,ValueError,OverflowError):
                        pass  # Invalid or old optional timing never changes the model result.
            except ReviewError as failure: error = failure
        if error is not None:
            proof = failure_receipt(error) if event['data'].get('fail_safe') else None
            code = _error_code(error)
            if proof is not None:
                diagnostic=validation_failure(error)
                _outcome(ledger,slot,'FAILURE',receipt=proof,error=code,
                         **({'validation_failure':diagnostic} if diagnostic is not None else {}))
                retry = slot.get('retries',0)<MAX_RETRIES and attempts(state)+1<max_calls
                number = slot.get('retries',0)+(1 if retry else 0)
                slot = {**slot,'status':'RETRY' if retry else 'FAILED','receipt':proof,
                    'error':code,'model_called':proof['model_called'],'retries':number,
                    'retry_after':retry_at(_db_now(service.db),number,error) if retry else None}
                if diagnostic is not None:slot['validation_failure']=diagnostic
                else:slot.pop('validation_failure',None)
                state = {**state,'failed_calls':state.get('failed_calls',0)+1}
                if not retry and attempts(state)>=max_calls: slot['error']='AI_BATCH_LIMIT'
            elif not isinstance(error,ReviewError) or code=='AI_OUTCOME_UNKNOWN':
                _outcome(ledger,slot,'UNKNOWN',error=code)
                slot = {**slot,'status':'UNKNOWN','unknown_reason':getattr(error,'unknown_reason','TRANSPORT_ERROR')}
                state = {**state,'unknown_calls':state.get('unknown_calls',0)+1}
            else:
                _outcome(ledger,slot,'REJECTED',error=code)
                slot = {**slot,'status':'FAILED','error':code,'model_called':getattr(error,'model_called',True)}
        state = {**state,'wave':[slot if s['index']==slot['index'] else s for s in state['wave']]}
        ledger.step(step,ref,'READY',state)
        return service.public(_settle(service,event,ledger))


def run_step(db_factory,workspace_factory,advisor,criteria_provider,job_id,*,now=None):
    from oa.sanity_ai_jobs import _transaction,_db_now,_timestamp
    dispatch = []
    with _transaction(db_factory,workspace_factory,advisor,criteria_provider) as service:
        service._lock(job_id); event = service._load(job_id)
        if event['status']=='RESULT_READY': return service.public(service._apply(event))
        if event['status'] not in ('PENDING','RUNNING'): return service.public(event)
        try:
            if event['status']=='RUNNING':
                ledger = ParallelLedger(service,event)
                event = _expire(service,event,ledger,now or _db_now(service.db))
                if event['status']!='RUNNING': return service.public(event)
                if any(s['status']=='RESERVED' for s in ledger.last()[0]['state'].get('wave',[])):
                    return service.public(event)
            event,ledger,model,revision = _prepare(service,event,now)
            step,ref = ledger.last(); state = step['state']; current = now or _db_now(service.db)
            if _timestamp(event['data']['deadline'])<=current:
                return service.public(service._failed(event,ReviewError('AI_BATCH_DEADLINE','Overall batch deadline exceeded'),status='FAILED'))
            wave = state.get('wave',[])
            if wave and all(s['status']=='DONE' for s in wave):
                step,ref = ledger.step(step,ref,'READY',{**state,'index':state['next_index'],'wave':[]})
                state = step['state']; wave = []
            manifest = ledger.round(state['round'])
            if state['index']==manifest['count']:
                return service.public(_finish_round(service,event,ledger,model,step,ref))
            if not wave:
                remaining = model.settings.max_calls-attempts(state)
                check(remaining>0,'AI_BATCH_LIMIT')
                stop = min(manifest['count'],state['index']+event['data']['batch']['max_concurrency'],state['index']+remaining)
                wave = [{'index':i,'status':'RETRY','attempt':0,'retries':0} for i in range(state['index'],stop)]
                state = {**state,'wave':wave,'next_index':stop}
            with service.db.transaction():
                for old in wave:
                    if old['status']!='RETRY' or old.get('retry_after') and _timestamp(old['retry_after'])>current: continue
                    if attempts(state)+sum(s['status']=='RESERVED' for s in state['wave'])>=model.settings.max_calls: break
                    descriptor = ledger.descriptor({**state,'index':old['index']})
                    nodes = ledger.nodes(descriptor['node_refs']) if 'node_refs' in descriptor else None
                    if nodes is not None and len(nodes)==1:
                        result_ref = ledger.put(f"result.{state['round']:03d}.{old['index']:06d}",{'nodes':nodes,'metadata':None})
                        new = {**old,'status':'DONE','result':result_ref}
                        state = {**state,'nodes':state['nodes']+1}
                    else:
                        child = copy(model); child.advisor = copy(model.advisor)
                        # Per-call progress comes from reservation and durable outcome;
                        # the HTTP thread never opens an observation DB connection.
                        child.advisor.observer = None
                        prepared = child.prepare(descriptor,nodes)
                        raw = prepared[1][0]; token = str(uuid4()); attempt = old['attempt']+1
                        timing=state.get('wall_timing')
                        if (not isinstance(timing,dict) or timing.get('round')!=state['round'] or
                                timing.get('resume_count')!=event['data'].get('resume_count',0)):
                            state={**state,'wall_timing':{'round':state['round'],
                                'resume_count':event['data'].get('resume_count',0),'started_at':_db_now(service.db).isoformat(),
                                'measured_at':None,'base_samples':state['round_samples'],
                                'base_records':state['records'],
                                'base_units':state['index']+sum(s['status']=='DONE' for s in state['wave'])}}
                        call = {'kind':'RESERVATION','round':state['round'],'index':old['index'],'attempt':attempt,
                            'call_token':token,'descriptor_sha256':digest(descriptor),'input_sha256':hashlib.sha256(raw).hexdigest(),
                            'input_bytes':len(raw),'prompt_version':prepared[0][2],
                            'deadline':(current+timedelta(seconds=child.advisor.settings.timeout+60)).isoformat()}
                        reservation = ledger.put(f"call.{state['round']:03d}.{old['index']:06d}.{attempt:03d}",call)
                        clean = {k:v for k,v in old.items() if k not in ('receipt','error','result','model_called','unknown_reason','validation_failure')}
                        new = {**clean,**{k:call[k] for k in ('attempt','call_token','input_sha256','input_bytes','prompt_version','deadline')},
                               'status':'RESERVED','reservation':reservation,'retry_after':None}
                        child.advisor = service._bind_advisor(child.advisor,event,token)
                        dispatch.append((copy_context(),child,descriptor,nodes,prepared,new,revision))
                    state = {**state,'wave':[new if s['index']==old['index'] else s for s in state['wave']]}
                ledger.step(step,ref,'READY',state)
            if not dispatch:
                if any(s['status']=='RETRY' for s in state['wave']) and attempts(state)>=model.settings.max_calls:
                    return service.public(service._failed(event,ReviewError('AI_BATCH_LIMIT','Call attempt budget exhausted'),status='FAILED'))
                return service.public(event)
        except ReviewError as error:
            return service.public(service._failed(event,error,status='STALE' if error.code in STALE else 'FAILED'))
    # A single coordinator owns this wave. All reservations committed first.
    with ThreadPoolExecutor(max_workers=len(dispatch),thread_name_prefix='ai-batch') as executor:
        futures = {executor.submit(ctx.run,_execute,child,descriptor,nodes,prepared):(slot,descriptor,revision)
                   for ctx,child,descriptor,nodes,prepared,slot,revision in dispatch}
        result = None
        for future in as_completed(futures):
            slot,descriptor,revision = futures[future]
            value,error,seconds = future.result()
            result = _save(db_factory,workspace_factory,advisor,criteria_provider,job_id,slot,descriptor,revision,value,error,seconds,model.settings.max_calls)
        return result
