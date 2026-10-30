"""Synthetic complete-input batching, durable calls and atomic publication."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from common.sanity_common import ReviewError, digest, canonical_json
from common.sanity_integration_examples import clustering_example
from oa.sanity_ai_budget import BatchSettings, TokenBudget
from oa.sanity_ai_batch_model import BatchModel
from oa.sanity_ai_batch_jobs import Ledger
from oa.sanity_ai_jobs import AIJobService, _now
from oa.sanity_ai_model import OAAdvisor, OASettings
from tests.support import PgTestCase


def batch_settings(**values):
    return BatchSettings(enabled=True,context_tokens=260000,output_parameter='max_tokens',**values)


def response(packet):
    rows=packet['context']['report']['records']
    ids=[r['id'] for r in rows]
    return {'groups':[{'id':'g','label':'합성 분류','record_ids':ids}], 'reason':'합성 전체 분류', 'reference_ids':[],
            'group_rationales':[{'group_id':'g','reason':'같은 조건이며 반례가 확인되지 않았습니다.',
                                 'evidence_ids':[ids[0]],'reference_groups':[]}]}


class BatchPureTests(unittest.TestCase):
    def test_serialized_budget_keeps_exact_utf8_and_tokenizer_inputs(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        budget=TokenBudget(batch_settings(input_tokens=64000))
        packet={'raw':'합성 😀\n"', 'fields':{'zero':0,'false':False,'missing':None}}
        prompt='전체 입력을 검사';raw=canonical_json(packet)
        expected=len(prompt.encode())+len(raw)
        self.assertEqual(expected,budget.measure(packet,prompt))
        self.assertEqual(expected,budget.measure_serialized(raw,prompt))
        self.assertEqual(expected,budget.check_serialized(raw,prompt))
        tokenizer=Mock();tokenizer.encode.side_effect=lambda text,**kwargs:SimpleNamespace(ids=list(text))
        budget.tokenizer=tokenizer
        self.assertEqual(len(prompt)+len(raw.decode()),budget.measure_serialized(raw,prompt))
        self.assertEqual([(prompt,), (raw.decode(),)], [call.args for call in tokenizer.encode.call_args_list])
        self.assertTrue(all(call.kwargs=={'add_special_tokens':True} for call in tokenizer.encode.call_args_list))

    def test_full_chunk_planning_checks_every_chunk_once_and_keeps_exact_hashes(self):
        model=self.model(max_records=7,input_tokens=64000,output_tokens=40000)
        template=model.records[0]
        model.records=[{**template,'id':f'planning-{i}'} for i in range(30)]
        with patch.object(model,'fits',wraps=model.fits) as fits:
            plan=model.leaf_plan()
        self.assertEqual([(0,7),(7,14),(14,21),(21,28),(28,30)],[(p['start'],p['stop']) for p in plan])
        self.assertEqual(len(plan),fits.call_count)
        for part in plan:
            self.assertEqual(digest(model.records[part['start']:part['stop']]),part['records_sha256'])
        self.assertEqual([],self.requests)

    def test_fast_plan_matches_original_binary_search_for_full_and_size_limited_chunks(self):
        for budget in (64000,10000):
            with self.subTest(input_tokens=budget):
                model=self.model(max_records=7,input_tokens=budget,output_tokens=40000)
                template=model.records[0]
                model.records=[{**template,'id':f'planning-{i}','raw':'합성 경계 '+('x'*(100+500*(i%3)))} for i in range(30)]
                expected=[];start=0;reference_checks=0
                while start<len(model.records):
                    lo,hi,count=1,min(7,len(model.records)-start),0
                    while lo<=hi:
                        mid=(lo+hi)//2
                        reference_checks+=1
                        if model.fits(model.leaf(start,start+mid)):count=mid;lo=mid+1
                        else:hi=mid-1
                    self.assertGreater(count,0)
                    expected.append({'start':start,'stop':start+count,'records_sha256':digest(model.records[start:start+count])})
                    start+=count
                with patch.object(model,'fits',wraps=model.fits) as fits:
                    actual=model.leaf_plan()
                self.assertEqual(expected,actual)
                self.assertLessEqual(fits.call_count,reference_checks+1)
                if budget==10000:
                    self.assertTrue(any(part['stop']-part['start']<7 for part in actual))
                self.assertEqual([],self.requests)
        # A custom tokenizer need not count longer prefixes monotonically.
        # Preserve the original search even when the largest prefix would fit.
        model=self.model(max_records=3)
        template=model.records[0]
        model.records=[{**template,'id':f'nonmonotonic-{i}'} for i in range(3)]
        model.budget.tokenizer=object()
        with patch.object(model,'fits',side_effect=lambda prepared:len(prepared[0]['context']['report']['records'])!=2):
            plan=model.leaf_plan()
        self.assertEqual([(0,1),(1,2),(2,3)],[(p['start'],p['stop']) for p in plan])

    def model(self, **options):
        context=clustering_example()['context']
        self.requests=[]
        def respond(request):
            wire=json.loads(request.content); packet=json.loads(wire['messages'][1]['content'])
            self.requests.append((wire,packet))
            return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop','message':{
                'role':'assistant','content':json.dumps(response(packet),ensure_ascii=False)}}]})
        advisor=OAAdvisor(OASettings(enabled=True,endpoint='https://model.example.test/v1/chat/completions',
            token='synthetic-credential',model='synthetic-model',batch=batch_settings(**options)),transport=httpx.MockTransport(respond))
        return BatchModel(advisor,context,[],['object','check_point','mode'])

    def test_all_originals_are_seen_and_global_partition_is_preserved(self):
        model=self.model(max_records=1)
        plan=model.leaf_plan();nodes=[]
        for desc in plan: nodes.extend(model.execute({'kind':'LEAF',**desc})['nodes'])
        self.assertEqual(len(model.records),len(self.requests))
        original=[]
        for wire,packet in self.requests:
            self.assertEqual(4096,wire['max_tokens'])
            original.extend(packet['batch']['record_aliases'].values())
            start=packet['batch']['start']
            for offset,row in enumerate(packet['context']['report']['records']):
                restored={**row,'id':packet['batch']['record_aliases'][row['id']]}
                self.assertEqual(model.records[start+offset],restored)
        self.assertEqual([r['id'] for r in model.records],original)
        reduced=model.execute({'kind':'MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)['nodes']
        advice,_=model.advice(reduced)
        self.assertEqual(set(original),set(advice['groups'][0]['record_ids']))

    def test_token_and_gateway_byte_limits_preflight_every_record(self):
        model=self.model(max_records=1)
        model.records[-1]['raw']='가'*100000
        with self.assertRaises(ReviewError) as caught:model.leaf_plan()
        self.assertEqual('AI_INPUT_LIMIT',caught.exception.code);self.assertEqual([],self.requests)
        model=self.model()
        model.advisor.settings=replace(model.advisor.settings,max_input_bytes=100)
        with self.assertRaises(ReviewError):model.leaf_plan()

    def test_duplicate_missing_and_foreign_final_memberships_rejected(self):
        model=self.model()
        nodes=model.execute({'kind':'LEAF',**model.leaf_plan()[0]})['nodes']
        for members in ([0,0],[],[999999]):
            with self.subTest(members=members),self.assertRaises(ReviewError):
                model.advice([{**nodes[0],'members':members}])

    def test_explicit_settings_and_model_output_parameter_compatibility(self):
        for changes in ({'context_tokens':1},{'output_parameter':'invented'},{'max_records':0},{'enabled':'true'}):
            values={**batch_settings().public(),**changes}
            with self.subTest(changes=changes),self.assertRaises(ReviewError):BatchSettings(**values)
        with self.assertRaises(ReviewError):OASettings(api='responses',batch=batch_settings())
        budget=TokenBudget(batch_settings())
        self.assertEqual(len('한글'.encode()),budget.tokens('한글'))

    def test_offline_tokenizer_hash_and_disabled_truncation(self):
        from tokenizers import Tokenizer,models,pre_tokenizers
        with tempfile.TemporaryDirectory() as folder:
            tokenizer=Tokenizer(models.WordLevel({'[UNK]':0,'hello':1},unk_token='[UNK]'))
            tokenizer.pre_tokenizer=pre_tokenizers.Whitespace();tokenizer.enable_truncation(1)
            p=Path(folder)/'tokenizer.json';tokenizer.save(str(p));sha=hashlib.sha256(p.read_bytes()).hexdigest()
            settings=batch_settings(tokenizer_mode='tokenizer_json',tokenizer_file=str(p),tokenizer_sha256=sha)
            self.assertEqual(3,TokenBudget(settings).tokens('hello hello hello'))
            p.write_text('{}')
            with self.assertRaises(ReviewError):TokenBudget(settings)

    def test_merge_uses_model_partition_even_when_all_labels_are_identical(self):
        model=self.model(max_records=1,input_tokens=64000)
        template=model.records[0]
        model.records=[{**template,'id':f'log-{i}','fields':{**template['fields'],'mode':'A' if i%2 else 'B'}} for i in range(4)]
        model.context['report'].update(records=model.records,record_count=4)
        def respond(request):
            p=json.loads(json.loads(request.content)['messages'][1]['content']);groups={}
            for row in p['context']['report']['records']:
                mode=row['fields'].get('mode') or row['fields']['conditions'][0]
                groups.setdefault(mode,[]).append(row['id'])
            advice={'groups':[{'id':key,'label':'같은 코드','record_ids':ids} for key,ids in groups.items()],
                'reason':'조건별로 분리','reference_ids':[], 'group_rationales':[{'group_id':key,'reason':'조건 '+key,
                    'evidence_ids':[ids[0]],'reference_groups':[]} for key,ids in groups.items()]}
            return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(advice)}}]})
        model.advisor.transport=httpx.MockTransport(respond)
        nodes=[]
        for desc in model.leaf_plan():nodes.extend(model.execute({'kind':'LEAF',**desc})['nodes'])
        nodes=model.execute({'kind':'MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)['nodes']
        advice,_=model.advice(nodes)
        self.assertEqual({frozenset(('log-0','log-2')),frozenset(('log-1','log-3'))},
                         {frozenset(g['record_ids']) for g in advice['groups']})

    def test_complete_preserved_text_and_separate_citations_survive_leaf_and_merge(self):
        from tests.test_preserved_text import PreservedTextTests
        c=PreservedTextTests();c.setUp();self.addCleanup(c.doCleanups)
        file,_,segments=c.extracted();model=self.model(max_records=1,input_tokens=64000)
        model.context['report']['preserved_sources']=[file]
        model.context['preserved_evidence']=[{'source_id':file['id'],'segments':segments}]
        def respond(request):
            p=json.loads(json.loads(request.content)['messages'][1]['content'])
            self.assertEqual(model.context['preserved_evidence'],p['context']['preserved_evidence'])
            reply=response(p)
            reply['preserved_citations']=[{'target_id':'g','evidence_ids':[segments[0]['id']],'reason':'본문의 현재 조건을 비교'}]
            return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(reply)}}]})
        model.advisor.transport=httpx.MockTransport(respond);nodes=[]
        for desc in model.leaf_plan():nodes.extend(model.execute({'kind':'LEAF',**desc})['nodes'])
        nodes=model.execute({'kind':'MERGE','nodes':[n['id'] for n in nodes]},nodes=nodes)['nodes']
        advice,citations=model.advice(nodes)
        self.assertEqual([segments[0]['id']],citations[0]['evidence_ids'])
        self.assertEqual(advice['groups'][0]['id'],citations[0]['target_id'])

    def test_many_distinct_groups_remain_separate_across_bounded_merge_rounds(self):
        model=self.model(max_records=8,input_tokens=64000)
        template=model.records[0]
        model.records=[{**template,'id':f'log-{i}','raw':f'Synthetic distinct condition {i}'} for i in range(137)]
        model.context['report'].update(records=model.records,record_count=len(model.records))
        model.context['source_sha256']=digest(model.context['report'])
        merge_sizes=[]
        def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][1]['content'])
            ids=[r['id'] for r in packet['context']['report']['records']]
            if packet['batch']['stage']=='MERGE':merge_sizes.append(len(ids))
            reply={'groups':[{'id':key,'label':'Same code, distinct condition','record_ids':[key]} for key in ids],
                'reason':'Each condition requires separate review','reference_ids':[],
                'group_rationales':[{'group_id':key,'reason':'Keep this independently reviewed condition separate',
                    'evidence_ids':[key],'reference_groups':[]} for key in ids]}
            return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop',
                'message':{'role':'assistant','content':json.dumps(reply)}}]})
        model.advisor.transport=httpx.MockTransport(respond)
        nodes=[]
        for desc in model.leaf_plan():nodes.extend(model.execute({'kind':'LEAF',**desc})['nodes'])
        for round_number in (1,2):
            lookup={n['id']:n for n in nodes};following=[]
            for group in model.merge_plan(nodes,round_number):
                selected=[lookup[key] for key in group]
                following.extend(selected if len(group)==1 else model.execute(
                    {'kind':'MERGE','nodes':group},nodes=selected)['nodes'])
            nodes=following
        advice,_=model.advice(nodes)
        self.assertEqual({(r['id'],) for r in model.records},
                         {tuple(g['record_ids']) for g in advice['groups']})
        self.assertGreater(len(merge_sizes),2)
        self.assertLessEqual(max(merge_sizes),model.settings.merge_groups)


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class BatchDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.c.advisor=OAAdvisor(replace(self.c.advisor.settings,batch=batch_settings(max_records=1)),
                                transport=httpx.MockTransport(self.c.respond))

    def finish(self):
        for _ in range(30):
            result=self.c.run_job()
            if result is None or result['status'] in ('FAILED','STALE','UNKNOWN','COMPLETED','CANCELLED'):return result
        self.fail('Batch job did not terminate')

    def test_full_job_publishes_once_with_child_input_hashes(self):
        job=self.c.enqueue();first=self.c.run_job()
        self.assertEqual('RUNNING',first['status']);self.assertEqual(1,first['progress']['completed_records'])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])
        result=self.finish();self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(3,len(self.c.requests))
        with self.c.service() as service:
            event=service._load(job['id']);ledger=Ledger(service,event)
            self.assertIn('result_ref',event['data']);self.assertNotIn('result',event['data'])
            reservations=[s for s,_ in ledger.rows('step.') if s['status']=='RESERVED']
            self.assertEqual(3,len(reservations));self.assertTrue(all(len(s['input_sha256'])==64 for s in reservations))
        self.assertIsNone(self.c.run_job())

    def test_round_keeps_every_descriptor_without_separate_plan_rows(self):
        job=self.c.enqueue();self.c.run_job()
        with self.c.service() as service:
            ledger=Ledger(service,service._load(job['id']))
            manifest=ledger.round(0)
            self.assertEqual(2,manifest['plan_format'])
            self.assertEqual(2,manifest['count'])
            self.assertEqual([(0,1),(1,2)],[(d['start'],d['stop']) for d in manifest['descriptors']])
            self.assertEqual([],ledger.rows('plan.'))
            self.assertEqual(manifest['descriptors'][1],ledger.descriptor({'round':0,'index':1}))
        self.assertEqual('COMPLETED',self.finish()['status'])
        self.assertEqual(3,len(self.c.requests))

    def test_checkpoint_row_plans_resume_without_repeating_completed_children(self):
        def old_plans(ledger,round_number,descriptors,input_count):
            refs=[ledger.put(f'plan.{round_number:03d}.{i:06d}',d) for i,d in enumerate(descriptors)]
            return ledger.put(f'round.{round_number:03d}',
                              {'count':len(refs),'input_nodes':input_count,'plans':refs})
        job=self.c.enqueue()
        with patch.object(Ledger,'plans',old_plans):self.c.run_job()
        self.assertEqual('COMPLETED',self.finish()['status'])
        self.assertEqual(3,len(self.c.requests))
        with self.c.service() as service:
            ledger=Ledger(service,service._load(job['id']))
            self.assertIn('plans',ledger.round(0))
            self.assertEqual(2,ledger.round(1)['plan_format'])
            self.assertIsNotNone(ledger.audit())

    def test_rehashed_leaf_plan_cannot_replace_the_parent_pinned_plan(self):
        import base64
        from common.sanity_common import pack_json
        from common.sanity_db import json_value
        job=self.c.enqueue();self.c.run_job()
        with self.c.service() as service:
            ledger=Ledger(service,service._load(job['id']))
            manifest=ledger.round(0)
        manifest['descriptors'][1]['start']=0
        key='ai2.'+job['id']+'.round.000'
        payload=self.c.admin_query("SELECT payload FROM {oa} WHERE workspace_key=%s",(key,)).fetchone()[0]
        packed=pack_json(manifest)
        packed['payload_gzip']=base64.b64encode(packed['payload_gzip']).decode('ascii')
        payload['packed']=packed
        payload['sha256']=digest({k:v for k,v in payload.items() if k!='sha256'})
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE workspace_key=%s",(json_value(payload),key))
        result=self.c.run_job()
        self.assertEqual(('FAILED','AI_BATCH_CORRUPT'),(result['status'],result['error']))
        self.assertEqual(1,len(self.c.requests));self.assertIsNone(self.c.run_job())
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_second_child_failure_does_not_publish_partial_results_or_retry(self):
        self.c.enqueue();self.c.run_job()
        self.c.callback=lambda request:httpx.Response(500,json={})
        result=self.c.run_job();self.assertEqual('FAILED',result['status'])
        self.assertIsNone(self.c.run_job());self.assertEqual(2,len(self.c.requests))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_child_timeout_is_unknown_without_replay(self):
        self.c.enqueue()
        def fail(request):raise httpx.ReadTimeout('synthetic',request=request)
        self.c.callback=fail
        self.assertEqual('UNKNOWN',self.c.run_job()['status']);self.assertIsNone(self.c.run_job())
        self.assertEqual(1,len(self.c.requests))

    def test_completed_child_survives_worker_restart_and_duplicate_workers(self):
        self.c.enqueue()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:self.c.run_job(),range(2)))
        result=self.finish();self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(3,len(self.c.requests))

    def test_cancel_during_child_call_cannot_revive_parent(self):
        job=self.c.enqueue()
        def cancel(request):
            with self.c.service() as service:service.cancel('owner',job['id'])
        self.c.callback=cancel
        self.assertEqual('CANCELLED',self.c.run_job()['status']);self.assertIsNone(self.c.run_job())

    def test_human_revision_change_between_children_is_stale(self):
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        from uuid import uuid4
        self.c.enqueue();self.c.run_job()
        with self.c.service() as service:
            WorkspaceClusteringStore(service.db,ws=service.ws).change(*self.c.path,action='PROPOSE',expected_revision=0,request_id=uuid4())
        self.assertEqual('STALE',self.c.run_job()['status']);self.assertEqual(1,len(self.c.requests))

    def test_batch_prompt_version_change_stops_between_children(self):
        self.c.enqueue();self.c.run_job()
        with patch('oa.sanity_ai_model.BATCH_PROMPT_VERSION','synthetic-new-prompt'):
            result=self.c.run_job()
        self.assertEqual(('STALE','AI_CONFIG_CHANGED'),(result['status'],result['error']))
        self.assertEqual(1,len(self.c.requests))

    def test_final_ready_recovers_without_any_repeated_call(self):
        self.c.enqueue()
        for _ in range(20):
            result=self.c.run_job()
            if result['status']=='RESULT_READY':break
        self.assertEqual('RESULT_READY',result['status'],result)
        with patch.object(AIJobService,'_apply',side_effect=RuntimeError('synthetic publication interruption')):
            with self.assertRaises(RuntimeError):self.c.run_job()
        self.assertEqual('COMPLETED',self.c.run_job()['status']);self.assertEqual(3,len(self.c.requests))

    def test_abandoned_child_reservation_is_unknown_without_dispatch(self):
        self.c.enqueue()
        with patch.object(BatchModel,'execute',side_effect=SystemExit('synthetic process death')):
            with self.assertRaises(SystemExit):self.c.run_job()
        result=self.c.run_job(now=_now()+timedelta(seconds=300))
        self.assertEqual('UNKNOWN',result['status']);self.assertEqual([],self.c.requests)
        self.assertIsNone(self.c.run_job())

    def test_revoked_account_between_children_stops_before_the_next_http_call(self):
        from common.sanity_db import json_value
        self.c.enqueue();self.c.run_job()
        self.c.admin_query("UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() WHERE record_type='WS_USER' AND workspace_key='owner'",
                           (json_value({'active':False}),))
        self.assertEqual('STALE',self.c.run_job()['status']);self.assertEqual(1,len(self.c.requests))

    def test_personal_credential_change_between_children_stops_without_fallback(self):
        from cryptography.fernet import Fernet
        from oa.sanity_ai_tokens import UserTokenStore
        settings={'SANITY_OA_AI_TOKEN_SOURCE':'user','SANITY_OA_AI_TOKEN_STORE_SECRET':Fernet.generate_key().decode()}
        with patch.dict('os.environ',settings):
            self.c.advisor=OAAdvisor(replace(self.c.advisor.settings,token_source='user',token=''),transport=httpx.MockTransport(self.c.respond))
            with self.c.service() as service:UserTokenStore(service.db).save('owner','synthetic-personal-first',0)
            self.c.enqueue();self.c.run_job()
            with self.c.service() as service:UserTokenStore(service.db).save('owner','synthetic-personal-second',1)
            self.assertEqual('STALE',self.c.run_job()['status']);self.assertEqual(1,len(self.c.requests))

    def test_corrupt_saved_final_is_failed_without_publication_or_model_replay(self):
        from common.sanity_db import json_value
        job=self.c.enqueue()
        for _ in range(20):
            result=self.c.run_job()
            if result['status']=='RESULT_READY':break
        key='ai2.'+job['id']+'.final'
        row=self.c.admin_query("SELECT payload FROM {oa} WHERE workspace_key=%s",(key,)).fetchone()[0]
        row['sha256']='0'*64
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE workspace_key=%s",(json_value(row),key))
        self.assertEqual('FAILED',self.c.run_job()['status']);self.assertEqual(3,len(self.c.requests))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_corrupt_child_progress_can_fail_closed_and_be_cancelled(self):
        from common.sanity_db import json_value
        job=self.c.enqueue();self.c.run_job()
        key='ai2.'+job['id']+'.step.00000003'
        row=self.c.admin_query("SELECT payload FROM {oa} WHERE workspace_key=%s",(key,)).fetchone()[0]
        row['sha256']='0'*64
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE workspace_key=%s",(json_value(row),key))
        result=self.c.run_job()
        self.assertEqual(('FAILED','AI_BATCH_CORRUPT'),(result['status'],result['error']))
        self.assertIsNone(self.c.run_job());self.assertEqual(1,len(self.c.requests))


class BatchLargeTests(unittest.TestCase):
    def test_one_hundred_thousand_records_reach_http_and_exact_final_partition(self):
        fixture=BatchPureTests();model=fixture.model(max_records=200,input_tokens=64000,output_tokens=40000)
        template=model.records[0]
        records=[{**template,'id':f'synthetic-log-{i:06d}','raw':f'Synthetic complete evidence {i} 끝',
                  'fields':{**template['fields'],'object':f'synthetic/{i}'}} for i in range(100000)]
        model.context['report'].update(records=records,record_count=len(records))
        model.context['source_sha256']=digest(model.context['report']);model.records=records
        before=digest(records);nodes=[]
        plan=model.leaf_plan()
        for entry in plan:nodes.extend(model.execute({'kind':'LEAF',**entry})['nodes'])
        leaf_packets=[p for _,p in fixture.requests if p['batch']['stage']=='LEAF']
        seen=[v for p in leaf_packets for v in p['batch']['record_aliases'].values()]
        self.assertEqual(len(records),len(seen));self.assertEqual({r['id'] for r in records},set(seen))
        while len(nodes)>1:
            lookup={n['id']:n for n in nodes};next_nodes=[]
            for group in model.merge_plan(nodes):
                selected=[lookup[k] for k in group]
                next_nodes.extend(selected if len(group)==1 else model.execute({'kind':'MERGE','nodes':group},nodes=selected)['nodes'])
            nodes=next_nodes
        advice,_=model.advice(nodes)
        self.assertEqual(100000,len(advice['groups'][0]['record_ids']));self.assertEqual(before,digest(records))
        self.assertGreater(len(plan),1)
