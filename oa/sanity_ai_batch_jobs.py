"""Durable child calls for complete OA clustering. No DDL or automatic replay.

Parent jobs retain the existing API/authorization lifecycle. Plans, compressed
child results and small hash-linked call events live in a separate WS_CONFIG
namespace. The parent stores references, never copies every child on progress.
"""
from datetime import timedelta
import base64
import hashlib
import re
import time
from uuid import uuid4

from common.sanity_common import ReviewError, canonical_json, digest, pack_json, unpack_json
from common.sanity_db import json_value
from common.sanity_submission import check
from common.sanity_preserved_ai import available, model_metadata, receipt
from oa.sanity_workspace import record_id
from oa.sanity_ai_batch_model import BatchModel, VERSION
from oa.sanity_ai_retry import MAX_RETRIES,receipt as failure_receipt,valid_receipt,retry_at,resumable

NAMESPACE = 'oa-ai-batch-v1'
MAX_BYTES = 64*1024*1024


def attempts(state):
    values=[state.get('calls'),state.get('failed_calls',0),state.get('unknown_calls',0)]
    check(all(type(value) is int and 0<=value<=100000 for value in values),'AI_BATCH_CORRUPT')
    return sum(values)


class Ledger:
    def __init__(self, service, parent):
        self.service, self.db, self.parent = service, service.db, parent
        self.prefix = 'ai2.'+parent['job_id']+'.'
        self.binding = digest([parent['job_id'],parent['data']['command'],parent['data']['pins']])

    def put(self, key, body):
        packed = pack_json(body,max_bytes=MAX_BYTES)
        packed['payload_gzip'] = base64.b64encode(packed['payload_gzip']).decode('ascii')
        value = {'namespace':NAMESPACE,'version':1,'job_id':self.parent['job_id'],
                 'binding':self.binding,'key':key,'packed':packed}
        value['sha256'] = digest(value)
        self.service.ws.put('WS_CONFIG',self.prefix+key,value)
        return {'key':key,'sha256':value['sha256']}

    def decode(self, row):
        from oa.sanity_ai_jobs import _NULL_COLUMNS
        value = row['payload']
        check(isinstance(value,dict) and set(value)=={'namespace','version','job_id','binding','key','packed','sha256'}
              and value['namespace']==NAMESPACE and type(value['version'])is int and value['version']==1
              and value['job_id']==self.parent['job_id'] and value['binding']==self.binding
              and isinstance(value['key'],str) and row['workspace_key']==self.prefix+value['key']
              and row['record_id']==record_id('WS_CONFIG',row['workspace_key'])
              and row['state']=={} and row['version']==0 and row['status']=='RECORDED'
              and row['updated_at']==row['created_at'] and all(row.get(k)is None for k in _NULL_COLUMNS)
              and value['sha256']==digest({k:v for k,v in value.items() if k!='sha256'}),'AI_BATCH_CORRUPT')
        try:
            body = unpack_json({**value['packed'],'payload_gzip':base64.b64decode(value['packed']['payload_gzip'],validate=True)},max_bytes=MAX_BYTES)
        except (ReviewError,KeyError,TypeError,ValueError):
            raise ReviewError('AI_BATCH_CORRUPT','Invalid immutable batch data') from None
        return body,{'key':value['key'],'sha256':value['sha256']}

    def get(self, key, expected=None):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",(self.prefix+key,)).fetchone()
        check(row is not None,'AI_BATCH_CORRUPT')
        body, ref = self.decode(row)
        check(expected is None or ref==expected,'AI_BATCH_CORRUPT')
        return body,ref

    def rows(self, prefix):
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s ORDER BY workspace_key",
                               (self.prefix+prefix+'%',)).fetchall()
        return [self.decode(r) for r in rows]

    def last(self):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s ORDER BY workspace_key DESC LIMIT 1",
                              (self.prefix+'step.%',)).fetchone()
        return self.decode(row) if row else (None,None)

    def step(self, previous, previous_ref, status, state, **extra):
        index = previous['sequence']+1 if previous else 1
        body = {'sequence':index,'previous_sha256':previous_ref['sha256'] if previous_ref else None,
                'status':status,'state':state,**extra}
        ref=self.put('step.'+str(index).zfill(8),body)
        from oa.sanity_ai_monitor import capture
        capture(self.service,self.parent)
        return body,ref

    def plans(self, round_number, descriptors, input_count):
        # One compressed immutable round retains every descriptor. Separate
        # rows repeat the parent binding, envelope and DB indexes for each leaf.
        # Reservations still hash the exact descriptor and actual HTTP body.
        return self.put(f'round.{round_number:03d}',{'plan_format':2,
            'count':len(descriptors),'input_nodes':input_count,'descriptors':descriptors})

    def round(self, round_number):
        expected = self.parent['data']['batch']['plan'] if round_number == 0 else None
        manifest, _ = self.get(f'round.{round_number:03d}', expected)
        check(isinstance(manifest,dict),'AI_BATCH_CORRUPT')
        if 'plan_format' in manifest:
            check(set(manifest)=={'plan_format','count','input_nodes','descriptors'}
                  and type(manifest['plan_format']) is int and manifest['plan_format']==2,
                  'AI_BATCH_CORRUPT')
            entries=manifest['descriptors']
        else:
            # Checkpoint jobs retain their original rows and receipt hashes.
            check(set(manifest)=={'count','input_nodes','plans'},'AI_BATCH_CORRUPT')
            entries=manifest['plans']
        check(type(manifest['count']) is int and manifest['count']>0
              and type(manifest['input_nodes']) is int and manifest['input_nodes']>0
              and isinstance(entries,list) and len(entries)==manifest['count']
              and all(isinstance(entry,dict) for entry in entries),'AI_BATCH_CORRUPT')
        return manifest

    def descriptor(self, state):
        manifest=self.round(state['round'])
        index=state['index']
        check(type(index)is int and 0<=index<manifest['count'],'AI_BATCH_CORRUPT')
        if 'descriptors' in manifest:
            return manifest['descriptors'][index]
        ref=manifest['plans'][index]
        return self.get(ref['key'],ref)[0]

    def nodes(self, refs):
        results={}
        values=[]
        for ref in refs:
            key=ref['result']['key']
            if key not in results: results[key]=self.get(key,ref['result'])[0]
            rows=results[key]['nodes']; index=ref['index']
            check(type(index)is int and 0<=index<len(rows) and rows[index]['id']==ref['id'],'AI_BATCH_CORRUPT')
            values.append(rows[index])
        return values

    def completed_round(self, round_number):
        nodes,refs=[],[]
        for result,result_ref in self.rows(f'result.{round_number:03d}.'):
            for i,node in enumerate(result['nodes']):
                nodes.append(node);refs.append({'result':result_ref,'index':i,'id':node['id']})
        check(len({n['id'] for n in nodes})==len(nodes),'AI_BATCH_CORRUPT')
        return nodes,refs

    def audit(self):
        previous=None; sequence=0; pending=None
        for step,ref in self.rows('step.'):
            check(step['sequence']==sequence+1 and step['previous_sha256']==previous
                  and step['status'] in ('READY','RESERVED'),'AI_BATCH_CORRUPT')
            previous=ref['sha256'];sequence+=1
            if step['status']=='RESERVED':
                check(pending is None and isinstance(step.get('input_sha256'),str),'AI_BATCH_CORRUPT')
                pending=step
            if step['status']=='READY' and step.get('result'):
                result=self.get(step['result']['key'],step['result'])[0]
                if result['metadata'] is not None:
                    check(pending is not None and result['metadata']['input_sha256']==pending['input_sha256'],'AI_BATCH_CORRUPT')
                    pending=None
                else: check(pending is None,'AI_BATCH_CORRUPT')
            if step['status']=='READY' and 'unsent' in step:
                check(pending is not None and step['unsent']=={'call_token':pending['call_token'],
                      'input_sha256':pending['input_sha256'],'error':'AI_CONNECT_FAILED'},'AI_BATCH_CORRUPT')
                pending=None
            if step['status']=='READY' and 'failure' in step:
                failure=step['failure']
                check(pending is not None and isinstance(failure,dict)
                    and set(failure)=={'call_token','input_sha256','receipt'}
                    and failure['call_token']==pending['call_token'] and failure['input_sha256']==pending['input_sha256']
                    and valid_receipt(failure['receipt']),'AI_BATCH_CORRUPT')
                pending=None
            if step['status']=='READY' and 'unknown_resolution' in step:
                resolution=step['unknown_resolution']
                check(pending is not None and isinstance(resolution,dict)
                    and set(resolution)=={'actor_id','request_id','event_sha256','step_sha256','call_token','input_sha256','acknowledge_duplicate'}
                    and resolution['actor_id']==self.parent['data']['command']['user_id']
                    and isinstance(resolution['request_id'],str) and re.fullmatch(r'[0-9a-f-]{36}',resolution['request_id'])
                    and isinstance(resolution['event_sha256'],str) and re.fullmatch(r'[0-9a-f]{64}',resolution['event_sha256'])
                    and resolution['step_sha256']==step['previous_sha256']
                    and resolution['call_token']==pending['call_token'] and resolution['input_sha256']==pending['input_sha256']
                    and resolution['acknowledge_duplicate'] is True,'AI_BATCH_CORRUPT')
                pending=None
        check(pending is None,'AI_BATCH_CORRUPT')
        return previous


def public(service,event):
    if event['data']['batch'].get('execution')=='parallel-v1':
        from oa.sanity_ai_parallel_jobs import public as parallel_public
        return parallel_public(service,event)
    from oa.sanity_ai_jobs import _public
    value=_public(event)
    try: step,step_ref=Ledger(service,event).last()
    except ReviewError:
        if event['status'] in ('FAILED','STALE','UNKNOWN','CANCELLED'):
            value['model_called']=None
            return value
        raise
    state=step['state'] if step else {}
    value['progress']={'mode':'BATCH','phase':state.get('phase', 'CLASSIFY' if state.get('round',0)==0 else 'MERGE'),
        'completed_records':state.get('records',0),'total_records':event['data']['batch'].get('total_records',0),
        'completed_calls':state.get('calls',0),'round':state.get('round',0),
        'waiting':bool(step and (step['status']=='RESERVED' or state.get('retry_after'))),
        'round_completed':state.get('index',0),'round_total':state.get('round_total',0),
        'average_call_seconds':state.get('seconds',0)/state['calls'] if state.get('calls') else None,
        'seconds_per_record':state.get('record_seconds',0)/state['records'] if state.get('records') else None,
        'stage_eta_seconds':max(0,state.get('round_total',0)-state.get('index',0))*state['round_seconds']/state['round_samples']
            if state.get('round_samples',0)>=2 and state.get('round_total') else None,
        'retry_count':state.get('failed_calls',0),'child_retries':state.get('child_retries',0),
        'retry_limit':MAX_RETRIES,'retry_after':state.get('retry_after')}
    value['progress']['unknown_calls']=state.get('unknown_calls',0)
    from oa.sanity_ai_unknown_recovery import recovery_info
    recovery=recovery_info(event,step,step_ref)
    if recovery is not None:value['unknown_recovery']=recovery
    proof=(step.get('failure',{}).get('receipt',{}) if step else {})
    if state.get('retry_after') and valid_receipt(proof):
        value['progress']['retry_error']=proof['error']
        if 'http_status' in proof:value['progress']['retry_http_status']=proof['http_status']
    value['resumable']=resumable(event,checkpoint_ready=bool(step and step['status']=='READY'))
    if state.get('calls',0): value['model_called']=True;value['model_attempted']=True
    elif step and step['status']=='RESERVED':
        # A terminal pre-send failure is known unsent even though its immutable
        # reservation remains. An active/abandoned reservation is still unknown.
        known_unsent=event['status']=='FAILED' and value['model_called'] is False
        if value['model_called'] is not True and not known_unsent:value['model_called']=None
        value['model_attempted']=True
    return value


def _model(advisor,event,context,cases,criteria):
    if event['data']['batch']['version']==2:
        from oa.sanity_ai_evidence_batch import EvidenceBatchModel
        return EvidenceBatchModel(advisor,context,cases,event['data']['pins']['identity_fields'],criteria)
    return BatchModel(advisor,context,cases,event['data']['pins']['identity_fields'])


def _record_plan(model):
    kind='REVIEW_LEAF' if getattr(model,'review',False) else 'LEAF'
    return [{'kind':kind,**r} for r in model.leaf_plan()]


def _bind_preserved(model,ledger,state):
    if state.get('preserved_ref'):
        model.bind_preserved(ledger.nodes([state['preserved_ref']])[0])


def _prepare(service,event,advisor,now):
    """Inside a short authorization transaction. Never performs HTTP."""
    from oa.sanity_ai_jobs import _db_now
    _,context,cases,criteria=service._inputs(event)
    advisor=service._job_advisor(event)
    revision=None
    if service._personal_tokens(event):
        token,revision=service._user_token(event['data']['command']['user_id'],event['data']['command'].get('connection_id'))
        advisor=advisor.with_user_token(token)
    model=_model(advisor,event,context,cases,criteria)
    ledger=Ledger(service,event)
    if event['status']=='PENDING':
        # Catch a fundamentally oversized record/criterion before paying for
        # text analysis. The final record plan also includes its actual summary.
        if getattr(model,'segments',[]): model.leaf_plan()
        text_plan=model.text_plan() if hasattr(model,'text_plan') else []
        # Every text fragment is preflighted before the first HTTP call. Record
        # leaves are preflighted together after the actual text analysis fits.
        ranges=text_plan or _record_plan(model)
        plan=ledger.plans(0,ranges,len(text_plan) or len(model.records))
        started=now or _db_now(service.db)
        batch={**event['data']['batch'],'total_records':len(model.records),'leaf_count':len(ranges),
               'plan':plan,'credential_revision':revision}
        event=service._append(event,'RUNNING',{**event['data'],'batch':batch,'call_token':str(uuid4()),
            'model_attempted':False,'model_called':False,'started_at':started.isoformat(),
            'deadline':(started+timedelta(seconds=model.settings.deadline_seconds)).isoformat()},now=started)
        ledger=Ledger(service,event)
        state={'round':0,'index':0,'records':0,'calls':0,'nodes':0,'stable':0,'actual_model':None,
               'round_total':len(ranges),'round_seconds':0,'round_samples':0,'seconds':0,'record_seconds':0}
        if event['data']['batch']['version']==2:
            state.update(phase='TEXT' if text_plan else 'REVIEW' if model.review else 'CLASSIFY',stage_round=0)
        ledger.step(None,None,'READY',state)
    _bind_preserved(model,ledger,ledger.last()[0]['state'])
    return event,ledger,model,revision


def _text_round(ledger,model,step,ref,nodes,node_refs):
    state=step['state'];number=state['round']+1
    if len(nodes)==1:
        model.bind_preserved(nodes[0])
        descriptors=_record_plan(model)
        ledger.plans(number,descriptors,len(model.records))
        ledger.step(step,ref,'READY',{**state,'round':number,'index':0,'nodes':0,'stage_round':0,
            'round_total':len(descriptors),'round_seconds':0,'round_samples':0,
            'phase':'REVIEW' if model.review else 'CLASSIFY','preserved_ref':node_refs[0]})
    else:
        check(state['stage_round']<model.settings.max_rounds,'AI_BATCH_LIMIT')
        groups=model.bounded_groups(nodes,text=True);by_id={r['id']:r for r in node_refs}
        descriptors=[{'kind':'TEXT_MERGE','nodes':g,'node_refs':[by_id[k] for k in g]} for g in groups]
        ledger.plans(number,descriptors,len(nodes))
        ledger.step(step,ref,'READY',{**state,'round':number,'index':0,'nodes':0,
                                    'round_total':len(descriptors),'round_seconds':0,'round_samples':0,
                                    'stage_round':state['stage_round']+1,'phase':'TEXT_MERGE'})


def _finish_round(service,event,ledger,model,step,ref):
    state=step['state']; round_number=state['round']
    nodes,node_refs=ledger.completed_round(round_number)
    manifest=ledger.round(round_number)
    check(nodes and len(nodes)==state['nodes'],'AI_BATCH_CORRUPT')
    if state.get('phase') in ('TEXT','TEXT_MERGE'):
        _text_round(ledger,model,step,ref,nodes,node_refs)
        return event
    stage_round=state.get('stage_round',round_number)
    stable=state['stable']+1 if stage_round>0 and len(nodes)==manifest['input_nodes'] else 0
    review=getattr(model,'review',False)
    if len(nodes)==1 or (not review and stable>=2):
        check(state['records']==len(model.records),'INVALID_PARTITION')
        advice,citations=model.advice(nodes)  # Existing full-original partition and rationales.
        chain=ledger.audit()
        metadata={**model.advisor.describe(),'model':state['actual_model'],'requested_model':model.advisor.settings.model,
            'prompt_version':getattr(model,'version',VERSION),'model_called':True,'input_sha256':digest([event['data']['batch']['plan'],chain]),
            'input_hash_kind':'BATCH_MANIFEST','output_sha256':digest(advice),'batch_receipt':{
                'version':1,'root':chain,'calls':state['calls'],'records':state['records'],
                'plan':event['data']['batch']['plan']}}
        from common.sanity_ai_projection import policy_for, source_policy_pin
        if policy_for(model.context):
            metadata['prompt_version'] += '.fields-v1'
            metadata.update(source_policy_pin(model.context))
        usage={};routing={'primary_calls':0,'fallback_calls':0,'connect_failures':0}
        for result,_ in ledger.rows('result.'):
            for key,value in (result['metadata'] or {}).get('usage',{}).items():usage[key]=usage.get(key,0)+value
            route=(result['metadata'] or {}).get('routing')
            if route:
                routing[route['endpoint']+'_calls']+=1
                routing['connect_failures']+=len(route['attempted_endpoints'])-1
        metadata['usage']=usage
        if model.advisor.settings.fallback_endpoint:metadata['routing_summary']=routing
        metadata=model_metadata(metadata,citations if available(model.context) else None,advice,model.context)
        if event['data']['batch']['credential_revision'] is not None:
            metadata['credential_revision']=event['data']['batch']['credential_revision']
        receipt(metadata,advice,model.context)
        # Recover by immutable node references, not a second full ID assignment
        # snapshot; apply_ai owns the sole published clustering event.
        final=ledger.put('final',{'nodes':node_refs,'metadata':metadata})
        event=service._append(event,'RESULT_READY',{**event['data'],'result_ref':final,'model_called':True,'model_attempted':True})
        return event
    check(stage_round<model.settings.max_rounds,'AI_BATCH_LIMIT','Reduction has not stabilized within its call budget')
    groups=model.merge_plan(nodes,stage_round+1)
    by_id={r['id']:r for r in node_refs}
    descriptors=[{'kind':'REVIEW_MERGE' if review else 'MERGE','nodes':g,'node_refs':[by_id[k] for k in g]} for g in groups]
    ledger.plans(round_number+1,descriptors,len(nodes))
    extra={'stage_round':stage_round+1,'phase':'REVIEW_MERGE' if review else 'MERGE'} if 'phase' in state else {}
    ledger.step(step,ref,'READY',{**state,'round':round_number+1,'index':0,'nodes':0,'stable':stable,
                                'round_total':len(descriptors),'round_seconds':0,'round_samples':0,**extra})
    return event


def load_result(service,event,context,cases,criteria=None):
    if event['data']['batch'].get('execution')=='parallel-v1':
        from oa.sanity_ai_parallel_ledger import ParallelLedger
        ledger=ParallelLedger(service,event)
    else:
        ledger=Ledger(service,event)
    ref=event['data']['result_ref']
    result=ledger.get(ref['key'],ref)[0]
    check(set(result)=={'nodes','metadata'} and result['metadata'].get('input_hash_kind')=='BATCH_MANIFEST'
          and result['metadata']['batch_receipt']['root']==ledger.audit()
          and result['metadata']['input_sha256']==digest([event['data']['batch']['plan'],ledger.last()[1]['sha256']]),'AI_BATCH_CORRUPT')
    model=_model(service._job_advisor(event),event,context,cases,criteria or [])
    _bind_preserved(model,ledger,ledger.last()[0]['state'])
    advice,citations=model.advice(ledger.nodes(result['nodes']))
    check(digest(advice)==result['metadata']['output_sha256'],'AI_BATCH_CORRUPT')
    if available(context):check(citations==result['metadata']['preserved_citations'],'AI_BATCH_CORRUPT')
    return {'advice':advice,'metadata':result['metadata']}


def run_step(db_factory,workspace_factory,advisor,criteria_provider,job_id,*,now=None):
    from oa.sanity_ai_jobs import _transaction,_db_now,_timestamp,_error_code
    dispatch=None
    with _transaction(db_factory,workspace_factory,advisor,criteria_provider) as service:
        service._lock(job_id);event=service._load(job_id)
        if event['status']=='RESULT_READY': return public(service,service._apply(event))
        if event['status'] not in ('PENDING','RUNNING'): return public(service,event)
        try:
            # A persisted reservation may already have reached the model.
            # Input or credential changes cannot prove that it did not happen.
            if event['status']=='RUNNING':
                reserved,_=Ledger(service,event).last()
                if reserved and reserved['status']=='RESERVED':
                    current=now or _db_now(service.db)
                    if _timestamp(reserved['deadline'])<=current or _timestamp(event['data']['deadline'])<=current:
                        error=ReviewError('AI_OUTCOME_UNKNOWN','Child call completion is unknown');error.unknown_reason='RESERVED_EXPIRED'
                        return public(service,service._failed(event,error,status='UNKNOWN',called=None))
                    return public(service,event)
            event,ledger,model,revision=_prepare(service,event,advisor,now)
            step,ref=ledger.last();state=step['state'];current=now or _db_now(service.db)
            if step['status']=='RESERVED':
                if _timestamp(step['deadline'])<=current or _timestamp(event['data']['deadline'])<=current:
                    return public(service,service._failed(event,ReviewError('AI_OUTCOME_UNKNOWN','Child call completion is unknown'),status='UNKNOWN',called=None))
                return public(service,event)
            if _timestamp(event['data']['deadline'])<=current:
                return public(service,service._failed(event,ReviewError('AI_BATCH_DEADLINE','Overall batch deadline exceeded'),status='FAILED'))
            if state.get('retry_after') and _timestamp(state['retry_after'])>current:return public(service,event)
            manifest=ledger.round(state['round'])
            if state['index']==manifest['count']:
                return public(service,_finish_round(service,event,ledger,model,step,ref))
            check(state['index']<manifest['count'],'AI_BATCH_CORRUPT')
            descriptor=ledger.descriptor(state)
            nodes=ledger.nodes(descriptor['node_refs']) if 'node_refs' in descriptor else None
            if nodes is not None and len(nodes)==1:
                result=ledger.put(f"result.{state['round']:03d}.{state['index']:06d}",{'nodes':nodes,'metadata':None})
                ledger.step(step,ref,'READY',{**state,'index':state['index']+1,'nodes':state['nodes']+1},result=result)
                return public(service,event)
            check(attempts(state)<model.settings.max_calls,'AI_BATCH_LIMIT')
            prepared_call=model.prepare(descriptor,nodes)
            request_bytes=prepared_call[1][0]
            state={**state,'retry_after':None}
            reserved,reserved_ref=ledger.step(step,ref,'RESERVED',state,descriptor_sha256=digest(descriptor),
                input_sha256=hashlib.sha256(request_bytes).hexdigest(),input_bytes=len(request_bytes),
                prompt_version=prepared_call[0][2],
                call_token=str(uuid4()),deadline=(current+timedelta(seconds=model.advisor.settings.timeout+60)).isoformat())
            model.advisor=service._bind_advisor(model.advisor,event,reserved['call_token'])
            from oa.sanity_ai_monitor import observer
            model.advisor=model.advisor.with_observer(observer(db_factory,workspace_factory,advisor,criteria_provider,event,reserved['call_token']))
            dispatch=(event,reserved,reserved_ref,model,descriptor,nodes,revision,prepared_call)
        except ReviewError as error:
            stale=error.code in ('AI_INPUT_STALE','AI_CASES_STALE','AI_CRITERIA_STALE','REVISION_CONFLICT','AI_CONFIG_CHANGED','AI_CONNECTION_UNAVAILABLE','AI_CREDENTIAL_CHANGED','UNAUTHORIZED','FORBIDDEN')
            return public(service,service._failed(event,error,status='STALE' if stale else 'FAILED'))
    # No database connection or authorization lock survives model dispatch.
    event,reserved,reserved_ref,model,descriptor,nodes,revision,prepared_call=dispatch
    call_started=time.monotonic()
    try:
        result=model.execute(descriptor,nodes=nodes,prepared_call=prepared_call); failure=None
    except Exception as error:
        result=None;failure=error
    seconds=time.monotonic()-call_started
    with _transaction(db_factory,workspace_factory,advisor,criteria_provider) as service:
        service._lock(job_id);current=service._load(job_id);ledger=Ledger(service,current)
        if current['status']!='RUNNING': return public(service,current)
        if current['sequence']!=event['sequence']:return public(service,current)
        step,ref=ledger.last()
        check(ref==reserved_ref and step['status']=='RESERVED','AI_BATCH_CORRUPT')
        if _timestamp(step['deadline'])<=_db_now(service.db):
            error=ReviewError('AI_OUTCOME_UNKNOWN','Child deadline exceeded');error.unknown_reason='RESPONSE_DEADLINE'
            return public(service,service._failed(current,error,status='UNKNOWN',called=True))
        if failure is not None:
            state=step['state']
            proof=failure_receipt(failure) if current['data'].get('fail_safe') else None
            if proof is not None:
                failed=state.get('failed_calls',0)+1
                can_retry=state.get('child_retries',0)<MAX_RETRIES and attempts(state)+1<model.settings.max_calls
                retries=state.get('child_retries',0)+(1 if can_retry else 0)
                ledger.step(step,ref,'READY',{**state,'child_retries':retries,'failed_calls':failed,
                    'retry_after':retry_at(_db_now(service.db),retries,failure) if can_retry else None},
                    failure={'call_token':step['call_token'],'input_sha256':step['input_sha256'],'receipt':proof})
                if can_retry:return public(service,current)
                current={**current,'data':{**current['data'],'retry_failure':proof}}
                if attempts(state)+1>=model.settings.max_calls:
                    return public(service,service._failed(current,ReviewError('AI_BATCH_LIMIT','Call attempt budget exhausted'),
                                  status='FAILED',called=proof['model_called']))
            unknown=not isinstance(failure,ReviewError) or _error_code(failure)=='AI_OUTCOME_UNKNOWN'
            stale=_error_code(failure) in ('AI_CONFIG_CHANGED','AI_CONNECTION_UNAVAILABLE','AI_CREDENTIAL_CHANGED','FORBIDDEN','UNAUTHORIZED')
            return public(service,service._failed(current,failure,status='UNKNOWN' if unknown else 'STALE' if stale else 'FAILED',called=getattr(failure,'model_called',None)))
        try:
            service._inputs(current)
            if service._personal_tokens(current):
                check(service._user_token(current['data']['command']['user_id'],current['data']['command'].get('connection_id'))[1]==revision,'AI_CREDENTIAL_CHANGED')
            state=step['state'];actual=result['metadata']['model']
            check(result['metadata']['input_sha256']==step['input_sha256'],'AI_BATCH_CORRUPT')
            check(state['actual_model'] is None or state['actual_model']==actual,'AI_MODEL_CHANGED')
            result_ref=ledger.put(f"result.{state['round']:03d}.{state['index']:06d}",result)
            records=state['records']+(descriptor['stop']-descriptor['start'] if descriptor['kind'] in ('LEAF','REVIEW_LEAF') else 0)
            ledger.step(step,ref,'READY',{**state,'index':state['index']+1,'calls':state['calls']+1,
                        'records':records,'nodes':state['nodes']+len(result['nodes']),'actual_model':actual,
                        'seconds':state.get('seconds',0)+seconds,'round_seconds':state.get('round_seconds',0)+seconds,
                        'round_samples':state.get('round_samples',0)+1,
                        'record_seconds':state.get('record_seconds',0)+(seconds if records>state['records'] else 0),
                        'child_retries':0,'retry_after':None},result=result_ref)
            return public(service,current)
        except ReviewError as error:
            return public(service,service._failed(current,error,status='STALE',called=True))
