"""Durable child calls for complete OA clustering. No DDL or automatic replay.

Parent jobs retain the existing API/authorization lifecycle. Plans, compressed
child results and small hash-linked call events live in a separate WS_CONFIG
namespace. The parent stores references, never copies every child on progress.
"""
from datetime import timedelta
import base64
import hashlib
from uuid import uuid4

from common.sanity_common import ReviewError, canonical_json, digest, pack_json, unpack_json
from common.sanity_db import json_value
from common.sanity_submission import check
from common.sanity_preserved_ai import available, model_metadata, receipt
from oa.sanity_workspace import record_id
from oa.sanity_ai_batch_model import BatchModel, VERSION

NAMESPACE = 'oa-ai-batch-v1'
MAX_BYTES = 64*1024*1024


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
        return body,self.put('step.'+str(index).zfill(8),body)

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
        check(pending is None,'AI_BATCH_CORRUPT')
        return previous


def public(service,event):
    from oa.sanity_ai_jobs import _public
    value=_public(event)
    try: step,_=Ledger(service,event).last()
    except ReviewError:
        if event['status'] in ('FAILED','STALE','UNKNOWN','CANCELLED'):
            value['model_called']=None
            return value
        raise
    state=step['state'] if step else {}
    value['progress']={'mode':'BATCH','phase':('CLASSIFY' if state.get('round',0)==0 else 'MERGE'),
        'completed_records':state.get('records',0),'total_records':event['data']['batch'].get('total_records',0),
        'completed_calls':state.get('calls',0),'round':state.get('round',0),
        'waiting':bool(step and step['status']=='RESERVED')}
    if state.get('calls',0): value['model_called']=True;value['model_attempted']=True
    elif step and step['status']=='RESERVED':
        if value['model_called'] is not True:value['model_called']=None
        value['model_attempted']=True
    return value


def _prepare(service,event,advisor,now):
    """Inside a short authorization transaction. Never performs HTTP."""
    from oa.sanity_ai_jobs import _db_now
    _,context,cases,_=service._inputs(event)
    revision=None
    if service._personal_tokens():
        token,revision=service._user_token(event['data']['command']['user_id'])
        advisor=advisor.with_user_token(token)
    model=BatchModel(advisor,context,cases,event['data']['pins']['identity_fields'])
    ledger=Ledger(service,event)
    if event['status']=='PENDING':
        ranges=model.leaf_plan()  # All leaves preflight, including an oversized last record.
        plan=ledger.plans(0,[{'kind':'LEAF',**r} for r in ranges],len(context['report']['records']))
        started=now or _db_now(service.db)
        batch={**event['data']['batch'],'total_records':len(model.records),'leaf_count':len(ranges),
               'plan':plan,'credential_revision':revision}
        event=service._append(event,'RUNNING',{**event['data'],'batch':batch,'call_token':str(uuid4()),
            'model_attempted':False,'model_called':False,'started_at':started.isoformat(),
            'deadline':(started+timedelta(seconds=model.settings.deadline_seconds)).isoformat()},now=started)
        ledger=Ledger(service,event)
        ledger.step(None,None,'READY',{'round':0,'index':0,'records':0,'calls':0,'nodes':0,'stable':0,'actual_model':None})
    return event,ledger,model,revision


def _finish_round(service,event,ledger,model,step,ref):
    state=step['state']; round_number=state['round']
    nodes,node_refs=ledger.completed_round(round_number)
    manifest=ledger.round(round_number)
    check(nodes and len(nodes)==state['nodes'],'AI_BATCH_CORRUPT')
    stable=state['stable']+1 if round_number>0 and len(nodes)==manifest['input_nodes'] else 0
    if len(nodes)==1 or stable>=2:
        check(state['records']==len(model.records),'INVALID_PARTITION')
        advice,citations=model.advice(nodes)  # Existing full-original partition and rationales.
        chain=ledger.audit()
        metadata={**model.advisor.describe(),'model':state['actual_model'],'requested_model':model.advisor.settings.model,
            'prompt_version':VERSION,'model_called':True,'input_sha256':digest([event['data']['batch']['plan'],chain]),
            'input_hash_kind':'BATCH_MANIFEST','output_sha256':digest(advice),'batch_receipt':{
                'version':1,'root':chain,'calls':state['calls'],'records':state['records'],
                'plan':event['data']['batch']['plan']}}
        usage={}
        for result,_ in ledger.rows('result.'):
            for key,value in (result['metadata'] or {}).get('usage',{}).items():usage[key]=usage.get(key,0)+value
        metadata['usage']=usage
        metadata=model_metadata(metadata,citations if available(model.context) else None,advice,model.context)
        if event['data']['batch']['credential_revision'] is not None:
            metadata['credential_revision']=event['data']['batch']['credential_revision']
        receipt(metadata,advice,model.context)
        # Recover by immutable node references, not a second full ID assignment
        # snapshot; apply_ai owns the sole published clustering event.
        final=ledger.put('final',{'nodes':node_refs,'metadata':metadata})
        event=service._append(event,'RESULT_READY',{**event['data'],'result_ref':final,'model_called':True,'model_attempted':True})
        return event
    check(round_number<model.settings.max_rounds,'AI_BATCH_LIMIT','Reduction has not stabilized within its call budget')
    groups=model.merge_plan(nodes,round_number+1)
    by_id={r['id']:r for r in node_refs}
    descriptors=[{'kind':'MERGE','nodes':g,'node_refs':[by_id[k] for k in g]} for g in groups]
    ledger.plans(round_number+1,descriptors,len(nodes))
    ledger.step(step,ref,'READY',{**state,'round':round_number+1,'index':0,'nodes':0,'stable':stable})
    return event


def load_result(service,event,context,cases):
    ledger=Ledger(service,event)
    ref=event['data']['result_ref']
    result=ledger.get(ref['key'],ref)[0]
    check(set(result)=={'nodes','metadata'} and result['metadata'].get('input_hash_kind')=='BATCH_MANIFEST'
          and result['metadata']['batch_receipt']['root']==ledger.audit()
          and result['metadata']['input_sha256']==digest([event['data']['batch']['plan'],ledger.last()[1]['sha256']]),'AI_BATCH_CORRUPT')
    model=BatchModel(service.advisor,context,cases,event['data']['pins']['identity_fields'])
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
            event,ledger,model,revision=_prepare(service,event,advisor,now)
            step,ref=ledger.last();state=step['state'];current=now or _db_now(service.db)
            if step['status']=='RESERVED':
                if _timestamp(step['deadline'])<=current or _timestamp(event['data']['deadline'])<=current:
                    return public(service,service._failed(event,ReviewError('AI_OUTCOME_UNKNOWN','Child call completion is unknown'),status='UNKNOWN',called=None))
                return public(service,event)
            if _timestamp(event['data']['deadline'])<=current:
                return public(service,service._failed(event,ReviewError('AI_BATCH_DEADLINE','Overall batch deadline exceeded'),status='FAILED'))
            manifest=ledger.round(state['round'])
            if state['index']==manifest['count']:
                return public(service,_finish_round(service,event,ledger,model,step,ref))
            check(state['index']<manifest['count'],'AI_BATCH_CORRUPT')
            descriptor=ledger.descriptor(state)
            nodes=ledger.nodes(descriptor['node_refs']) if descriptor['kind']=='MERGE' else None
            if nodes is not None and len(nodes)==1:
                result=ledger.put(f"result.{state['round']:03d}.{state['index']:06d}",{'nodes':nodes,'metadata':None})
                ledger.step(step,ref,'READY',{**state,'index':state['index']+1,'nodes':state['nodes']+1},result=result)
                return public(service,event)
            check(state['calls']<model.settings.max_calls,'AI_BATCH_LIMIT')
            prepared_call=model.prepare(descriptor,nodes)
            request_bytes=prepared_call[1][0]
            reserved,reserved_ref=ledger.step(step,ref,'RESERVED',state,descriptor_sha256=digest(descriptor),
                input_sha256=hashlib.sha256(request_bytes).hexdigest(),input_bytes=len(request_bytes),
                prompt_version=prepared_call[0][2],
                call_token=str(uuid4()),deadline=(current+timedelta(seconds=advisor.settings.timeout+60)).isoformat())
            dispatch=(event,reserved,reserved_ref,model,descriptor,nodes,revision,prepared_call)
        except ReviewError as error:
            stale=error.code in ('AI_INPUT_STALE','AI_CASES_STALE','REVISION_CONFLICT','AI_CONFIG_CHANGED','AI_CREDENTIAL_CHANGED','UNAUTHORIZED','FORBIDDEN')
            return public(service,service._failed(event,error,status='STALE' if stale else 'FAILED'))
    # No database connection or authorization lock survives model dispatch.
    event,reserved,reserved_ref,model,descriptor,nodes,revision,prepared_call=dispatch
    try:
        result=model.execute(descriptor,nodes=nodes,prepared_call=prepared_call); failure=None
    except Exception as error:
        result=None;failure=error
    with _transaction(db_factory,workspace_factory,advisor,criteria_provider) as service:
        service._lock(job_id);current=service._load(job_id);ledger=Ledger(service,current)
        if current['status']!='RUNNING': return public(service,current)
        step,ref=ledger.last()
        check(ref==reserved_ref and step['status']=='RESERVED','AI_BATCH_CORRUPT')
        if _timestamp(step['deadline'])<=_db_now(service.db):
            return public(service,service._failed(current,ReviewError('AI_OUTCOME_UNKNOWN','Child deadline exceeded'),status='UNKNOWN',called=True))
        if failure is not None:
            unknown=not isinstance(failure,ReviewError) or _error_code(failure)=='AI_OUTCOME_UNKNOWN'
            return public(service,service._failed(current,failure,status='UNKNOWN' if unknown else 'FAILED',called=getattr(failure,'model_called',None)))
        try:
            service._inputs(current)
            if service._personal_tokens():
                check(service._user_token(current['data']['command']['user_id'])[1]==revision,'AI_CREDENTIAL_CHANGED')
            state=step['state'];actual=result['metadata']['model']
            check(result['metadata']['input_sha256']==step['input_sha256'],'AI_BATCH_CORRUPT')
            check(state['actual_model'] is None or state['actual_model']==actual,'AI_MODEL_CHANGED')
            result_ref=ledger.put(f"result.{state['round']:03d}.{state['index']:06d}",result)
            records=state['records']+(descriptor['stop']-descriptor['start'] if descriptor['kind']=='LEAF' else 0)
            ledger.step(step,ref,'READY',{**state,'index':state['index']+1,'calls':state['calls']+1,
                        'records':records,'nodes':state['nodes']+len(result['nodes']),'actual_model':actual},result=result_ref)
            return public(service,current)
        except ReviewError as error:
            return public(service,service._failed(current,error,status='STALE',called=True))
