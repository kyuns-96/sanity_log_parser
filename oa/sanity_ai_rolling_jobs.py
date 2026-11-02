"""Refill a durable, bounded window after each completed HTTP call."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from copy import copy
from datetime import timedelta
import hashlib
from uuid import uuid4

from common.sanity_common import ReviewError, digest
from oa.sanity_ai_batch_jobs import _finish_round, attempts
from oa.sanity_ai_parallel_jobs import STALE, _execute, _expire, _prepare, _save, _settle
from oa.sanity_ai_parallel_ledger import ParallelLedger


def _advance(db_factory, workspace_factory, advisor, criteria_provider, job_id,
             owned, model=None, revision=None, *, now=None):
    from oa.sanity_ai_jobs import _transaction, _db_now, _timestamp, _error_code
    dispatch = []
    with _transaction(db_factory,workspace_factory,advisor,criteria_provider) as service:
        service._lock(job_id)
        event = service._load(job_id)
        if event['status']=='RESULT_READY':
            return service.public(service._apply(event)),dispatch,model,revision
        if event['status'] not in ('PENDING','RUNNING'):
            return service.public(event),dispatch,model,revision
        try:
            if event['status']=='RUNNING':
                ledger = ParallelLedger(service,event)
                event = _expire(service,event,ledger,now or _db_now(service.db))
                if event['status']!='RUNNING':
                    return service.public(event),dispatch,model,revision
                state = ledger.last()[0]['state']
                # Another coordinator may own the remaining reservations. Never
                # refill its window or take a connection across network I/O.
                if any(slot['status']=='RESERVED' and slot['call_token'] not in owned for slot in state['wave']):
                    return service.public(event),dispatch,model,revision
                if state.get('cancel_requested') or state.get('dispatch_error') or any(
                        slot['status'] in ('UNKNOWN','FAILED') for slot in state['wave']):
                    return service.public(_settle(service,event,ledger)),dispatch,model,revision
            if model is None:
                event,ledger,model,revision = _prepare(service,event,now)
            else:
                # Keep the prepared model, but revalidate the complete input,
                # authorization, configuration and personal credential pins.
                service._inputs(event)
                ledger = ParallelLedger(service,event)
            step,ref = ledger.last()
            state = step['state']
            current = now or _db_now(service.db)
            if _timestamp(event['data']['deadline']) <= current:
                raise ReviewError('AI_BATCH_DEADLINE','Overall batch deadline exceeded')
            done = sum(slot['status']=='DONE' for slot in state['wave'])
            if done:
                state = {**state,'index':state['index']+done,
                         'wave':[slot for slot in state['wave'] if slot['status']!='DONE']}
                step,ref = ledger.step(step,ref,'READY',state)
            manifest = ledger.round(state['round'])
            if state['index']==manifest['count']:
                event = _finish_round(service,event,ledger,model,step,ref)
                return service.public(event),dispatch,model,revision
            # Backoff pauses new requests for this job as well as retries. In
            # particular, a 429 must not be bypassed with fresh descriptors.
            if any(slot['status']=='RETRY' and slot.get('retry_after') and
                   _timestamp(slot['retry_after']) > current for slot in state['wave']):
                return service.public(event),dispatch,model,revision
            with service.db.transaction():
                width = event['data']['batch']['max_concurrency']
                while True:
                    old = next((slot for slot in state['wave'] if slot['status']=='RETRY'),None)
                    if old is None:
                        if len(state['wave']) >= width or state['next_index'] >= manifest['count']:
                            break
                    reserved = sum(slot['status']=='RESERVED' for slot in state['wave'])
                    if attempts(state)+reserved >= model.settings.max_calls:
                        state = {**state,'dispatch_error':'AI_BATCH_LIMIT'}
                        break
                    if old is None:
                        old = {'index':state['next_index'],'status':'RETRY','attempt':0,'retries':0}
                        state = {**state,'next_index':state['next_index']+1,'wave':[*state['wave'],old]}
                    descriptor = ledger.descriptor({**state,'index':old['index']})
                    nodes = ledger.nodes(descriptor['node_refs']) if 'node_refs' in descriptor else None
                    if nodes is not None and len(nodes)==1:
                        result = ledger.put(f"result.{state['round']:03d}.{old['index']:06d}",{'nodes':nodes,'metadata':None})
                        new = {**old,'status':'DONE','result':result}
                        state = {**state,'nodes':state['nodes']+1}
                    else:
                        child = copy(model)
                        child.advisor = copy(model.advisor)
                        child.advisor.observer = None
                        prepared = child.prepare(descriptor,nodes)
                        raw = prepared[1][0]
                        token = str(uuid4())
                        current = now or _db_now(service.db)
                        if _timestamp(event['data']['deadline']) <= current:
                            raise ReviewError('AI_BATCH_DEADLINE','Overall batch deadline exceeded')
                        timing = state.get('wall_timing')
                        if (not isinstance(timing,dict) or timing.get('round')!=state['round'] or
                                timing.get('resume_count')!=event['data'].get('resume_count',0)):
                            state = {**state,'wall_timing':{'round':state['round'],
                                'resume_count':event['data'].get('resume_count',0),
                                'started_at':current.isoformat(),'measured_at':None,
                                'base_samples':state['round_samples'],'base_records':state['records'],
                                'base_units':state['index']+sum(slot['status']=='DONE' for slot in state['wave']),
                                'base_seconds':state['round_seconds']}}
                        work = descriptor['stop']-descriptor['start'] if descriptor['kind'] in ('LEAF','REVIEW_LEAF') else 1
                        call = {'kind':'RESERVATION','round':state['round'],'index':old['index'],
                            'attempt':old['attempt']+1,'call_token':token,'descriptor_sha256':digest(descriptor),
                            'input_sha256':hashlib.sha256(raw).hexdigest(),'input_bytes':len(raw),
                            'prompt_version':prepared[0][2],'reserved_at':current.isoformat(),'work':work,
                            'deadline':(current+timedelta(seconds=child.advisor.settings.timeout+60)).isoformat()}
                        reservation = ledger.put(f"call.{state['round']:03d}.{old['index']:06d}.{call['attempt']:03d}",call)
                        clean = {k:v for k,v in old.items() if k not in
                                 ('receipt','error','result','model_called','unknown_reason','validation_failure')}
                        new = {**clean,**{k:call[k] for k in ('attempt','call_token','input_sha256','input_bytes',
                            'prompt_version','deadline','reserved_at','work')},
                            'status':'RESERVED','reservation':reservation,'retry_after':None}
                        child.advisor = service._bind_advisor(child.advisor,event,token)
                        dispatch.append((copy_context(),child,descriptor,nodes,prepared,new,revision))
                    state = {**state,'wave':[new if slot['index']==old['index'] else slot for slot in state['wave']]}
                ledger.step(step,ref,'READY',state)
            return service.public(_settle(service,event,ledger)),dispatch,model,revision
        except ReviewError as error:
            # A failed preparation rolls back every reservation prepared in its
            # savepoint. Drain existing calls before publishing the failure.
            dispatch = []
            if event['status']=='RUNNING' and error.code!='AI_BATCH_CORRUPT':
                ledger = ParallelLedger(service,event)
                step,ref = ledger.last()
                ledger.step(step,ref,'READY',{**step['state'],'dispatch_error':_error_code(error)})
                event = _settle(service,event,ledger)
            else:
                event = service._failed(event,error,status='STALE' if error.code in STALE else 'FAILED')
            return service.public(event),dispatch,model,revision


def run_step(db_factory,workspace_factory,advisor,criteria_provider,job_id,*,now=None):
    args = (db_factory,workspace_factory,advisor,criteria_provider,job_id)
    result,dispatch,model,revision = _advance(*args,set(),now=now)
    if not dispatch:
        return result
    with ThreadPoolExecutor(max_workers=result['progress']['concurrency'],thread_name_prefix='ai-batch') as executor:
        futures = {}
        while True:
            for ctx,child,descriptor,nodes,prepared,slot,credential_revision in dispatch:
                future = executor.submit(ctx.run,_execute,child,descriptor,nodes,prepared)
                futures[future] = (slot,descriptor,credential_revision)
            if not futures:
                return result
            completed,_ = wait(futures,return_when=FIRST_COMPLETED)
            # Settle all outcomes already available before deciding to refill.
            while completed:
                for future in completed:
                    slot,descriptor,credential_revision = futures.pop(future)
                    value,error,seconds = future.result()
                    result = _save(*args,slot,descriptor,credential_revision,value,error,seconds,model.settings.max_calls)
                completed = {future for future in futures if future.done()}
            owned = {slot['call_token'] for slot,_,_ in futures.values()}
            result,dispatch,model,revision = _advance(*args,owned,model,revision,now=now)
