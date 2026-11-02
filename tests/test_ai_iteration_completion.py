"""Iteration caps publish the last complete partition, without claiming convergence."""
from dataclasses import replace
import json
import unittest
from uuid import uuid4

import httpx

from common.sanity_db import json_value
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_model import OAAdvisor
from oa.sanity_workspace import record_id
from tests.support import PgTestCase


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class IterationCompletionTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)

    def test_serial_and_rolling_caps_keep_exact_groups_and_publish_once(self):
        for width in (1,2):
            with self.subTest(width=width):
                if width==2:
                    with self.c.service() as service:
                        source=service.ws.get('WS_REPORT','report')['payload']
                        value={**source,'id':'rolling-report'}
                        service.ws.put('WS_REPORT',value['id'],value,project='project',block='block')
                    self.c.path=('owner','block','version','rolling-report')
                batch=BatchSettings(enabled=True,context_tokens=260000,input_tokens=64000,output_tokens=4096,
                    output_parameter='max_tokens',max_records=1,merge_groups=2,max_rounds=1,max_concurrency=width)
                self.c.advisor=OAAdvisor(replace(self.c.advisor.settings,batch=batch),transport=httpx.MockTransport(self.c.respond))
                def keep_separate(request):
                    packet=json.loads(json.loads(request.content)['messages'][1]['content'])
                    if packet.get('batch',{}).get('stage')!='MERGE':return None
                    rows=packet['context']['report']['records']
                    groups=[{'id':'separate-'+str(i),'label':'독립 조건 '+str(i),'record_ids':[row['id']]} for i,row in enumerate(rows)]
                    advice={'groups':groups,'reason':'서로 다른 조건을 유지합니다.','reference_ids':[],
                        'group_rationales':[{'group_id':g['id'],'reason':'이번 입력의 독립 조건입니다.',
                            'evidence_ids':g['record_ids'],'reference_groups':[]} for g in groups]}
                    return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop',
                        'message':{'role':'assistant','content':json.dumps(advice,ensure_ascii=False)}}]})
                self.c.callback=keep_separate
                job=self.c.enqueue()
                result=None
                for _ in range(12):
                    result=self.c.run_job(job_id=job['id'])
                    if result['status'] in ('COMPLETED','FAILED','STALE','UNKNOWN'):break
                self.assertEqual(('COMPLETED','MAX_ITERATIONS',1,2),
                    (result['status'],result.get('completion_reason'),result['merge_iteration'],result['current_group_count']))
                with self.c.service() as service:
                    from oa.sanity_workspace_clustering import WorkspaceClusteringStore
                    event,revision=WorkspaceClusteringStore(service.db,ws=service.ws).current(self.c.path[-1])
                    self.assertEqual(1,revision)
                    self.assertEqual([['a'],['b']],sorted(g['record_ids'] for g in event['groups']))
                    self.assertFalse(event['confirmed'])
                    self.assertEqual('MAX_ITERATIONS',event['model_metadata']['completion_reason'])
                count=len(self.c.requests)
                self.assertEqual('COMPLETED',self.c.run_job(job_id=job['id'])['status'])
                self.assertEqual(count,len(self.c.requests))
