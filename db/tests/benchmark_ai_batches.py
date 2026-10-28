"""Synthetic only: full OA worker, mock HTTP, ledger and final source coverage."""
import hashlib
import json
import time
from uuid import uuid4

import httpx

from common.sanity_common import canonical_json
from common.sanity_db import Database
from oa.sanity_access import access_lock
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_jobs import AIJobService,run_once
from oa.sanity_ai_model import OAAdvisor,OASettings
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from tests.test_ai_jobs import SyntheticAIWorkspace


def measure(report):
    calls=0;leaves=0;seen=set();input_hash=hashlib.sha256();maximum_bytes=0
    def respond(request):
        nonlocal calls,leaves,maximum_bytes
        calls+=1;maximum_bytes=max(maximum_bytes,len(request.content))
        body=json.loads(request.content);p=json.loads(body['messages'][1]['content'])
        rows=p['context']['report']['records'];ids=[r['id'] for r in rows]
        if p['batch']['stage']=='LEAF':
            leaves+=1
            for row in rows:
                original={**row,'id':p['batch']['record_aliases'][row['id']]}
                if original['id'] in seen:raise RuntimeError('Repeated original in leaf ownership')
                seen.add(original['id']);input_hash.update(canonical_json(original))
        reply={'groups':[{'id':'synthetic','label':'합성 조건 비교','record_ids':ids}],
               'reason':'전체 합성 기록의 조건을 비교한 시험 제안','reference_ids':[],
               'group_rationales':[{'group_id':'synthetic','reason':'전체 합성 구성원의 조건과 원문은 각 leaf에서 제공됐다.',
                                    'evidence_ids':[ids[0]],'reference_groups':[]}]}
        return httpx.Response(200,json={'model':'synthetic-batch-model','choices':[{'finish_reason':'stop',
                              'message':{'role':'assistant','content':json.dumps(reply,ensure_ascii=False)}}]})
    batch=BatchSettings(enabled=True,context_tokens=260000,input_tokens=128000,output_tokens=32768,
                        output_parameter='max_tokens',max_records=200)
    advisor=OAAdvisor(OASettings(enabled=True,endpoint='https://synthetic.example.test/chat/completions',
        model='synthetic-batch-model',token='synthetic-batch-credential',max_input_bytes=1000000,batch=batch),
        transport=httpx.MockTransport(respond))
    with Database('oa') as db,db.transaction():
        access_lock(db);ws=SyntheticAIWorkspace(db)
        owner=ws.get('WS_BLOCK',report['block_id'])['payload']['owners'][0]
        service=AIJobService(db,ws,advisor=advisor)
        job=service.enqueue(owner,report['block_id'],report['version_id'],report['id'],expected_revision=0,request_id=uuid4())
    started=time.monotonic();steps=0
    while True:
        result=run_once(lambda:Database('oa'),SyntheticAIWorkspace,advisor);steps+=1
        if result is None or result['status'] in ('FAILED','STALE','UNKNOWN','CANCELLED'):
            raise RuntimeError('Synthetic batch failed: '+str(result and result.get('error')))
        if result['status']=='COMPLETED':break
        if steps>batch.max_calls*3:raise RuntimeError('Synthetic worker step bound exceeded')
    with Database('oa') as db:
        event,_=WorkspaceClusteringStore(db,ws=SyntheticAIWorkspace(db)).current(report['id'])
        members=[rid for g in event['groups'] for rid in g['record_ids']]
        if event['confirmed'] or len(members)!=report['record_count'] or set(members)!=seen or len(seen)!=len(members):
            raise RuntimeError('Complete final partition changed')
        rows=db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",('ai2.'+job['id']+'.%',)).fetchone()['n']
        ledger_storage = {r['kind']:{'rows':r['rows'],'payload_bytes':r['payload_bytes']}
            for r in db.execute("SELECT split_part(workspace_key,'.',3) AS kind,count(*) AS rows,"
                "sum(pg_column_size(payload)) AS payload_bytes FROM {oa} "
                "WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s GROUP BY 1",
                ('ai2.'+job['id']+'.%',)).fetchall()}
    return {'synthetic_only':True,'model_calls':calls,'leaf_calls':leaves,'records':len(seen),
            'complete_input_sha256':input_hash.hexdigest(),'final_partition_complete':True,
            'ledger_rows':rows,'ledger_storage_by_kind':ledger_storage,
            'worker_steps':steps,'seconds':round(time.monotonic()-started,3),
            'maximum_request_bytes':maximum_bytes,'batch_settings':batch.public()}
