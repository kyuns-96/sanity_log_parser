"""Synthetic complete-input batching, durable calls and atomic publication."""
from dataclasses import replace
import unittest

import httpx

from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_batch_jobs import Ledger
from oa.sanity_ai_model import OAAdvisor
from tests.support import PgTestCase


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class BatchDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.c.advisor=OAAdvisor(replace(self.c.advisor.settings,batch=BatchSettings(
            enabled=True,context_tokens=260000,output_parameter='max_tokens',max_records=1)),
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
