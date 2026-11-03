"""Global DB slots, FIFO leases, and separate classification/judgment calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event
from types import SimpleNamespace
import unittest
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError
from common.sanity_db import Database
from oa.sanity_ai_scheduler import Settings, Scheduler, executing_job
from tests.support import PgTestCase


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class SchedulerDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.a=Scheduler(lambda:Database('oa'),str(uuid4()),'oa')
        self.b=Scheduler(lambda:Database('oa'),str(uuid4()),'oa')

    def settings(self,calls=2,jobs=2,revision=0):
        with self.c.service() as service:
            def admin(actor):
                if actor.user_id!='owner':raise ReviewError('FORBIDDEN','Synthetic administrator required')
            actor=SimpleNamespace(user_id='owner',auth_method='sso',access=SimpleNamespace(db=service.db,_admin=admin))
            return Settings(service.db).save(actor,{'max_concurrency':calls,'max_jobs':jobs},revision)

    def another_report(self):
        with self.c.service() as service:
            report=service.ws.get('WS_REPORT','report')['payload']
            service.ws.put('WS_REPORT','report2',{**report,'id':'report2'},project='project',block='block')
        self.c.path=('owner','block','version','report2')

    def runtime(self):
        return self.c.admin_query("SELECT state FROM {oa} WHERE workspace_key='ai.scheduler.runtime'").fetchone()[0]


    def test_rolling_jobs_share_slots_and_a_fast_job_finishes_before_a_slow_job(self):
        from threading import Lock
        from oa.sanity_ai_budget import BatchSettings
        from oa.sanity_ai_model import OAAdvisor
        batch=BatchSettings(enabled=True,context_tokens=260000,input_tokens=64000,output_tokens=4096,
                            output_parameter='max_tokens',max_records=1,max_concurrency=8)
        self.c.advisor=OAAdvisor(replace(self.c.advisor.settings,batch=batch),transport=httpx.MockTransport(self.c.respond))
        self.settings();first=self.c.enqueue();self.another_report();second=self.c.enqueue()
        self.assertEqual(first['id'],self.a.claim());self.assertEqual(second['id'],self.b.claim())
        entered=Event();unblock=Event();lock=Lock();counts={'active':0,'peak':0}
        def transport(request):
            body=json.loads(json.loads(request.content)['messages'][1]['content'])
            with lock:
                counts['active']+=1;counts['peak']=max(counts['peak'],counts['active'])
            try:
                if body['context']['report']['id']=='report' and body.get('batch',{}).get('start')==0:
                    entered.set()
                    if not unblock.wait(30):raise AssertionError('Synthetic slow job was not released')
            finally:
                with lock:counts['active']-=1
        self.c.callback=transport
        def process(job,scheduler):
            with executing_job(job['id'],scheduler.owner):
                for _ in range(12):
                    result=self.c.run_job(job_id=job['id'])
                    if result['status'] in ('COMPLETED','FAILED','STALE','UNKNOWN'):return result
                raise AssertionError('Synthetic rolling job did not finish')
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow=pool.submit(process,first,self.a)
            try:
                self.assertTrue(entered.wait(10))
                fast=pool.submit(process,second,self.b)
                result=fast.result(timeout=20)
                self.assertEqual('COMPLETED',result['status'],result)
                self.assertFalse(slow.done());self.assertEqual(2,counts['peak'])
                self.assertEqual(1,len(self.runtime()['calls']))
                self.b.finish(second['id'])
            finally:unblock.set()
            result=slow.result(timeout=20)
            self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual({},self.runtime()['calls'])
