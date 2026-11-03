"""Synthetic complete-input batching, durable calls and atomic publication."""
from dataclasses import replace
import json
import unittest

import httpx

from common.sanity_common import digest
from common.sanity_integration_examples import clustering_example
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_batch_model import BatchModel
from oa.sanity_ai_batch_jobs import Ledger
from oa.sanity_ai_model import OAAdvisor, OASettings
from tests.support import PgTestCase


def batch_settings(**values):
    return BatchSettings(enabled=True,context_tokens=260000,output_parameter='max_tokens',**values)


def response(packet):
    rows=packet['context']['report']['records']
    from common.sanity_rule_groups import rule_code
    by_code={}
    for row in rows:by_code.setdefault(rule_code(row,packet['context']['report']),[]).append(row['id'])
    groups=[{'id':'g' if len(by_code)==1 else 'g'+str(i),'label':'합성 분류','record_ids':ids}
            for i,ids in enumerate(by_code.values())]
    return {'groups':groups, 'reason':'합성 전체 분류', 'reference_ids':[],
            'group_rationales':[{'group_id':g['id'],'reason':'같은 규칙과 조건이며 반례가 확인되지 않았습니다.',
                                 'evidence_ids':[g['record_ids'][0]],'reference_groups':[]} for g in groups]}


class BatchPureTests(unittest.TestCase):


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


    def test_cancel_during_child_call_cannot_revive_parent(self):
        job=self.c.enqueue()
        def cancel(request):
            with self.c.service() as service:service.cancel('owner',job['id'])
        self.c.callback=cancel
        self.assertEqual('CANCELLED',self.c.run_job()['status']);self.assertIsNone(self.c.run_job())


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
