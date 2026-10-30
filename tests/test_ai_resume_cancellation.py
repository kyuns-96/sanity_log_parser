"""A resumed single call can be cancelled and resumed before dispatch."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_ai_execution import executing_on
from oa.sanity_ai_jobs import _now
from tests.support import PgTestCase


class ResumeCancellationTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w=VwpWorkerTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.c=self.w.c;self.admin,self.schema=self.c.admin,self.c.schema

    def test_503_exhaustion_resume_cancel_and_resume_without_an_extra_call(self):
        job=self.w.request();self.w.prepare()
        self.c.callback=lambda request:httpx.Response(503)
        start=_now()
        for attempt in range(6):
            at=start+timedelta(seconds=200*attempt)
            with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):
                failed=self.c.run_job(now=at)
        self.assertEqual(('FAILED','AI_CALL_FAILED',True),
                         (failed['status'],failed['error'],failed['model_called']))
        self.assertTrue(failed['resumable'])
        self.assertEqual(6,len(self.w.v.wire))
        at=start+timedelta(seconds=1200)
        with patch('oa.sanity_ai_jobs._db_now',return_value=at),self.c.service() as service:
            before=service._load(job['id'])
            first=service.resume('owner',job['id'],uuid4())
            self.assertEqual(('PENDING',False,False),(first['status'],first['model_called'],first['model_attempted']))
            cancelled=service.cancel('owner',job['id'])
            self.assertEqual('CANCELLED',cancelled['status'])
            self.assertTrue(cancelled['resumable'])
            second=service.resume('owner',job['id'],uuid4())
            self.assertEqual(('PENDING',2),(second['status'],second['resume_count']))
            old=service.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                ('ai1.job.'+job['id']+'.'+str(before['sequence']).zfill(4),)).fetchone()['payload']
            self.assertEqual(before,old)
            self.assertEqual(503,old['data']['retry_failure']['http_status'])
            self.assertEqual(6,len(self.w.v.wire))
        self.c.callback=None
        with patch('oa.sanity_ai_jobs._db_now',return_value=at):result=self.w.v.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(7,len(self.w.v.wire))
