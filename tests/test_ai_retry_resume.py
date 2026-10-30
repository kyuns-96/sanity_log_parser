"""Synthetic tests for explicit resume, saved checkpoints and authorization."""
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError
from common.sanity_db import json_value
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_jobs import _now
from tests.support import PgTestCase


class ResumeApiTests(unittest.TestCase):
    def test_resume_uses_authenticated_actor_and_mutation_guards(self):
        from tests.test_ai_workbench_api import ProductionWorkbenchApiTests, HEADERS, TARGET
        fixture=ProductionWorkbenchApiTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        job_id,request_id=uuid4(),uuid4()
        path=f'/api/workbench/ai/jobs/{job_id}/resume'
        body={'request_id':str(request_id)}
        fixture.service.resume.return_value={'id':str(job_id),'status':'RUNNING'}
        self.assertEqual(403,fixture.client.post(path,json=body).status_code)
        self.assertEqual(400,fixture.client.post(path,headers=HEADERS,json={**body,'user_id':'other'}).status_code)
        fixture.service.resume.assert_not_called()
        response=fixture.client.post(path,headers=HEADERS,json=body)
        self.assertEqual(202,response.status_code)
        fixture.service.resume.assert_called_once_with(TARGET[0],str(job_id),request_id)


class ResumeCheckpointTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w=VwpWorkerTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.c=self.w.c;self.admin,self.schema=self.c.admin,self.c.schema

    def setup_batch(self):
        from tests.test_vwp_inference import configuration
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
            overhead_tokens=1024,max_records=1,output_parameter='max_tokens')
        self.w.connection=self.w.v.create(configuration(batch=settings.public()))
        job=self.w.request();self.w.prepare()
        result=self.w.prepare()
        self.assertEqual(1,result['progress']['completed_calls'])
        return job

    def exhaust(self):
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ConnectError('synthetic',request=request))
        start=_now()
        for attempt in range(6):
            at=start+timedelta(seconds=200*attempt)
            with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):result=self.c.run_job(now=at)
        self.assertEqual('FAILED',result['status'],result)
        return result,start+timedelta(seconds=1200)

    def test_exhausted_batch_resumes_saved_child_without_repeating_completed_work(self):
        job=self.setup_batch()
        failed,at=self.exhaust()
        self.assertTrue(failed['resumable'])
        self.assertEqual(1,failed['progress']['completed_calls'])
        self.assertEqual(7,len(self.w.v.wire))
        request_id=uuid4()
        with patch('oa.sanity_ai_jobs._db_now',return_value=at), \
             patch('oa.sanity_ai_jobs.AIJobService._context',side_effect=AssertionError('web hydrated source')):
            with self.c.service() as service:
                first=service.resume('owner',job['id'],request_id)
                duplicate=service.resume('owner',job['id'],request_id)
                self.assertEqual(first,duplicate)
                self.assertEqual(('RUNNING',1,1),(first['status'],first['resume_count'],first['progress']['completed_calls']))
        self.c.callback=None
        with patch('oa.sanity_ai_jobs._db_now',return_value=at):result=self.w.v.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(9,len(self.w.v.wire))  # one completed leaf, six failures, one leaf, one merge
        self.assertNotEqual(self.w.v.wire[0].content,self.w.v.wire[-2].content)
        with self.c.service() as service:
            replay=service.resume('owner',job['id'],request_id)
            self.assertEqual('COMPLETED',replay['status'])

    def test_cancelled_between_batches_can_resume_but_other_actor_is_rejected(self):
        job=self.setup_batch()
        with self.c.service() as service:
            cancelled=service.cancel('owner',job['id'])
            self.assertTrue(cancelled['resumable'])
            with self.assertRaises(ReviewError) as error:service.resume('other',job['id'],uuid4())
            self.assertEqual('FORBIDDEN',error.exception.code)
            resumed=service.resume('owner',job['id'],uuid4())
            self.assertEqual(('RUNNING',1),(resumed['status'],resumed['progress']['completed_calls']))
        result=self.w.v.finish()
        self.assertEqual('COMPLETED',result['status'])
        self.assertEqual(3,len(self.w.v.wire))

    def test_unknown_outcome_never_resumes_and_source_change_blocks_resumed_call(self):
        job=self.w.request();self.w.prepare()
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ReadTimeout('synthetic',request=request))
        result=self.w.v.finish();self.assertEqual('UNKNOWN',result['status'])
        with self.c.service() as service:
            with self.assertRaises(ReviewError) as error:service.resume('owner',job['id'],uuid4())
            self.assertEqual('AI_RESUME_UNAVAILABLE',error.exception.code)
        self.assertEqual(1,len(self.w.v.wire))

        job=self.w.request();self.w.prepare();failed,at=self.exhaust()
        self.assertTrue(failed['resumable'])
        with patch('oa.sanity_ai_jobs._db_now',return_value=at),self.c.service() as service:
            service.resume('owner',job['id'],uuid4())
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{rule}}',%s) WHERE record_type='WS_REPORT'",(json_value('synthetic changed'),))
        count=len(self.w.v.wire);self.c.callback=None
        with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):result=self.c.run_job(now=at)
        self.assertEqual('STALE',result['status'])
        self.assertEqual(count,len(self.w.v.wire))

    def test_a_batch_in_retry_backoff_does_not_block_another_job(self):
        from tests.test_vwp_inference import configuration
        from oa.sanity_workspace import WorkspaceStore
        job=self.setup_batch()
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ConnectError('synthetic',request=request))
        at=_now()+timedelta(seconds=1000)
        with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):
            waiting=self.c.run_job(now=at)
        self.assertEqual('RUNNING',waiting['status'])
        self.assertIsNotNone(waiting['progress']['retry_after'])
        report=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key=%s",('report',)).fetchone()[0]
        report={**report,'id':'report-2'}
        with self.c.service() as service:
            WorkspaceStore(service.db).put('WS_REPORT','report-2',report,project='project',block='block')
        connection=self.w.v.create(configuration())
        with self.c.service() as service:
            second=service.request('owner','block','version','report-2',expected_revision=0,request_id=uuid4(),connection_id=connection)
        self.c.callback=None
        with executing_on('vwp'):
            prepared=self.c.run_job();result=self.c.run_job()
        self.assertEqual(second['id'],prepared['id'])
        self.assertEqual((second['id'],'COMPLETED'),(result['id'],result['status']))
        self.assertEqual(3,len(self.w.v.wire))  # one completed leaf, one refused call, one other job
        with self.c.service() as service:
            self.assertEqual('RUNNING',service.status('owner',job['id'])['status'])

    def test_vwp_http_retry_after_waits_and_succeeds_on_sixth_attempt(self):
        job=self.w.request();self.w.prepare()
        def respond(request):
            if len(self.w.v.wire)<=5:
                return httpx.Response(503,headers={'Retry-After':'20'})
            return None  # The synthetic server produces a valid response.
        self.c.callback=respond
        at=_now()
        for attempt,delay in enumerate((20,20,20,40,60),start=1):
            with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):
                result=self.c.run_job(now=at)
            self.assertEqual(('PENDING',attempt), (result['status'],result['retry_count']))
            self.assertEqual(at+timedelta(seconds=delay),datetime.fromisoformat(result['retry_after']))
            self.assertEqual(attempt,len(self.w.v.wire))
            before=at+timedelta(seconds=delay-1)
            with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=before):
                self.assertIsNone(self.c.run_job(now=before))
            self.assertEqual(attempt,len(self.w.v.wire))
            at+=timedelta(seconds=delay)
        with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):
            result=self.c.run_job(now=at)
            self.assertIsNone(self.c.run_job(now=at))
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(6,len(self.w.v.wire))
        with self.c.service() as service:
            self.assertEqual('COMPLETED',service.status('owner',job['id'])['status'])

    def test_batch_call_budget_stops_early_and_cannot_be_resumed(self):
        from tests.test_vwp_inference import configuration
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
            overhead_tokens=1024,max_records=1,output_parameter='max_tokens',max_calls=2)
        self.w.connection=self.w.v.create(configuration(batch=settings.public()))
        job=self.w.request();self.w.prepare()
        first=self.w.prepare()
        self.assertEqual(1,first['progress']['completed_calls'])
        self.c.callback=lambda request:httpx.Response(503,headers={'Retry-After':'20'})
        failed=self.w.prepare()
        self.assertEqual(('FAILED','AI_BATCH_LIMIT',False),
                         (failed['status'],failed['error'],failed['resumable']))
        self.assertEqual((1,1),(failed['progress']['completed_calls'],failed['progress']['retry_count']))
        self.assertEqual(2,len(self.w.v.wire))
        with self.c.service() as service:
            with self.assertRaises(ReviewError) as error:service.resume('owner',job['id'],uuid4())
            self.assertEqual('AI_RESUME_UNAVAILABLE',error.exception.code)
        self.assertIsNone(self.w.prepare())
        self.assertEqual(2,len(self.w.v.wire))
