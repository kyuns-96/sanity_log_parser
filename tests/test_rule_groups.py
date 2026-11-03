"""Rule codes partition every sanity before AI or human semantic grouping."""
from dataclasses import replace
import unittest
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError
from tests.support import PgTestCase


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class RuleGroupDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        with self.c.service() as service:
            report=service.ws.get('WS_REPORT','report')['payload']
            report={**report,'id':'multi-rule','records':[
                {**row,'fields':{**row['fields'],'code':'R'+str(i+1)}} for i,row in enumerate(report['records'])]}
            service.ws.put('WS_REPORT','multi-rule',report,project='project',block='block')
        self.c.path=('owner','block','version','multi-rule')

    def test_manual_merge_move_and_new_group_reject_mixed_codes_and_rename_keeps_prefix(self):
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        groups=[{'id':'g1','label':'첫 조건','record_ids':['a']},{'id':'g2','label':'둘째 조건','record_ids':['b']}]
        with self.c.service() as service:
            store=WorkspaceClusteringStore(service.db,ws=service.ws)
            result=store.change(*self.c.path,action='PROPOSE',expected_revision=0,request_id=str(uuid4()),
                prepared={'groups':groups,'reason':'다른 규칙을 분리했습니다.','reference_ids':[]})
        self.assertEqual(['R1 · 첫 조건','R2 · 둘째 조건'],[g['label'] for g in result['groups']])
        operations=[{'kind':'MERGE','group_ids':['g1','g2'],'destination_id':'g1'},
                    {'kind':'MOVE','record_ids':['a'],'destination_id':'g2'},
                    {'kind':'MOVE','group_ids':['g1','g2'],'new_group_id':'new','label':'새 그룹'}]
        for operation in operations:
            with self.subTest(operation=operation),self.assertRaises(ReviewError) as error:
                with self.c.service() as service:
                    WorkspaceClusteringStore(service.db,ws=service.ws).change(*self.c.path,
                        action='EDIT',expected_revision=1,request_id=str(uuid4()),reason='합성 변경',operation=operation)
            self.assertEqual('RULE_CODE_CONFLICT',error.exception.code)
        with self.c.service() as service:
            event=WorkspaceClusteringStore(service.db,ws=service.ws).change(*self.c.path,
                action='EDIT',expected_revision=1,request_id=str(uuid4()),reason='이름 설명 변경',
                operation={'kind':'RENAME','destination_id':'g1','label':'새 설명'})
            self.assertEqual('R1 · 새 설명',event['groups'][0]['label'])

    def test_rolling_worker_keeps_all_codes_separate_through_publication(self):
        from oa.sanity_ai_budget import BatchSettings
        batch=BatchSettings(enabled=True,context_tokens=260000,input_tokens=64000,
            output_parameter='max_tokens',max_records=20,max_concurrency=2)
        self.c.advisor=type(self.c.advisor)(replace(self.c.advisor.settings,batch=batch),
            transport=httpx.MockTransport(self.c.respond))
        job=self.c.enqueue()
        for _ in range(16):
            result=self.c.run_job(job_id=job['id'])
            if result['status'] in ('COMPLETED','FAILED','STALE','UNKNOWN'):break
        self.assertEqual('COMPLETED',result['status'])
        self.assertEqual(2,result['current_group_count'])
        for packet in self.c.requests:
            self.assertEqual(1,len(set(packet['context']['report']['rule_codes'].values())))
        with self.c.service() as service:
            from oa.sanity_workspace_clustering import WorkspaceClusteringStore
            event,_=WorkspaceClusteringStore(service.db,ws=service.ws).current('multi-rule')
            self.assertEqual([['a'],['b']],[g['record_ids'] for g in event['groups']])
            self.assertTrue(all(g['label'].startswith(code+' · ') for code,g in zip(('R1','R2'),event['groups'])))
