"""Job-bound widths above eight remain valid for display and safe recovery."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event, Lock
import json
import unittest
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError, digest
from common.sanity_ai_execution import executing_on
from common.sanity_db import Database, json_value
from oa.sanity_ai_parallel_recovery import _info, _reset, can_resume
from tests.support import PgTestCase


class ParallelWidthTests(unittest.TestCase):
    def event(self, status='RUNNING', width=32):
        return {'status':status,'sha256':'a'*64,
                'data':{'batch':{'max_concurrency':width},'fail_safe':True}}

    def test_running_status_accepts_nine_sixteen_and_thirty_two_reservations(self):
        for count in (9,16,32):
            state={'wave':[{'index':i,'status':'RESERVED'} for i in range(count)]}
            with self.subTest(count=count):self.assertFalse(can_resume(self.event(),state))

    def test_job_limit_and_invalid_or_duplicate_slots_still_fail_closed(self):
        valid={'wave':[{'index':i,'status':'RESERVED'} for i in range(32)]}
        for state,width in ((valid,31),(valid,True),(valid,1000001),
                            ({'wave':[valid['wave'][0],valid['wave'][0]]},32),
                            ({'wave':[{'index':True,'status':'RESERVED'}]},32),
                            ({'wave':[{'index':0,'status':'invalid'}]},32)):
            with self.subTest(state=state,width=width),self.assertRaises(ReviewError) as caught:
                can_resume(self.event(width=width),state)
            self.assertEqual('AI_BATCH_CORRUPT',caught.exception.code)

    def test_unknown_recovery_contains_the_complete_wide_reservation_set(self):
        event=self.event('UNKNOWN');state={'round':0,'round_total':32,'phase':'CLASSIFY','wave':[]}
        for i in range(32):
            state['wave'].append({'status':'UNKNOWN','index':i,'call_token':str(uuid4()),
                'input_bytes':100,'input_sha256':digest(['input',i]),
                'reservation':{'key':f'call.{i}','sha256':digest(['reservation',i])}})
        primary,details=_info(event,{'status':'READY','state':state},{'sha256':'b'*64})
        self.assertEqual(list(range(32)),[row['index'] for row in details])
        self.assertEqual('b'*64,primary['step_sha256'])
        self.assertTrue(all(row['event_sha256']==event['sha256'] for row in details))

    def test_smaller_live_allocation_does_not_invalidate_saved_wide_calls(self):
        state={'allocated_concurrency':2,'wave':[{'index':i,'status':'DONE' if i==0 else 'UNKNOWN',
               'attempt':1,'retries':2,'retry_after':None} for i in range(32)]}
        restored=_reset(self.event('UNKNOWN'),state,{'UNKNOWN'})
        self.assertEqual(state['wave'][0],restored['wave'][0])
        self.assertTrue(all(slot['status']=='RETRY' and slot['retries']==0 for slot in restored['wave'][1:]))
        self.assertTrue(can_resume(self.event('CANCELLED'),restored))
        self.assertEqual('UNKNOWN',state['wave'][1]['status'])


@unittest.skipIf(getattr(PgTestCase,'__unittest_skip__',False),'Disposable PostgreSQL fixture required')
class ParallelWidthDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_scheduler import SchedulerDatabaseTests
        from oa.sanity_ai_budget import BatchSettings
        from oa.sanity_ai_model import OAAdvisor
        self.f=SchedulerDatabaseTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c=self.f.c;self.f.settings(calls=32,jobs=16)
        from oa.sanity_ai_scheduler import Scheduler
        self.f.a=Scheduler(lambda:Database('oa'),self.f.a.owner,'vwp')
        batch=BatchSettings(enabled=True,context_tokens=260000,input_tokens=64000,output_tokens=4096,
                            max_records=1,output_parameter='max_tokens',max_concurrency=2)
        self.c.advisor=OAAdvisor(replace(self.c.advisor.settings,batch=batch),transport=httpx.MockTransport(self.c.respond))
        report=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='report'").fetchone()[0]
        original=report['records'][0]
        report['records']=[{**original,'id':f'log-{i}',
                           'fields':{**original['fields'],'object':f'top/synthetic-{i}'}} for i in range(32)]
        report['record_count']=32
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key='report'",
                           (json_value(report),))

    def enqueue(self):
        # Match central-worker preparation, which explicitly enables fail-safe
        # recovery; legacy OA enqueue intentionally does not enable it.
        with executing_on('vwp'):return self.c.enqueue(_worker_site='vwp')

    def process(self,job):
        from oa.sanity_ai_scheduler import executing_job
        with executing_on('vwp'),executing_job(job['id'],self.f.a.owner):return self.c.run_job(job_id=job['id'])

    def finish(self,job):
        for _ in range(20):
            result=self.process(job)
            if result['status'] in ('COMPLETED','FAILED','STALE','UNKNOWN'):return result
        self.fail('Synthetic wide job did not finish')

    def test_global_32_jobs_16_displays_live_calls_and_drains_after_budget_reduction(self):
        job=self.enqueue();self.assertEqual(job['id'],self.f.a.claim())
        ready=Event();release=Event();lock=Lock();entered=0
        def respond(request):
            nonlocal entered
            packet=json.loads(json.loads(request.content)['messages'][1]['content'])
            if packet['batch']['stage']=='LEAF':
                with lock:
                    entered+=1
                    if entered==32:ready.set()
                if not release.wait(30):raise AssertionError('Synthetic wide calls were not released')
        self.c.callback=respond
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending=executor.submit(self.process,job)
            try:
                self.assertTrue(ready.wait(20))
                with self.c.service() as service:current=service.status('owner',job['id'])
                self.assertEqual(('RUNNING',32,32),(current['status'],current['progress']['inflight_calls'],
                                                  current['progress']['concurrency']))
                self.assertEqual(32,len(self.f.runtime()['calls']))
                self.f.settings(calls=4,jobs=16,revision=1)
                with self.c.service() as service:self.assertEqual('RUNNING',service.status('owner',job['id'])['status'])
            finally:release.set()
            result=pending.result(timeout=30)
        self.assertNotIn(result['status'],('FAILED','STALE','UNKNOWN'))
        self.c.callback=None;result=self.finish(job)
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual({},self.f.runtime()['calls'])
        self.f.a.finish(job['id'])

    def test_sixteen_unknown_calls_need_acknowledgment_and_preserve_successful_siblings(self):
        job=self.enqueue();self.assertEqual(job['id'],self.f.a.claim());entered=Barrier(32)
        def respond(request):
            packet=json.loads(json.loads(request.content)['messages'][1]['content'])
            if packet['batch']['stage']=='LEAF':
                entered.wait(timeout=20)
                if packet['batch']['start']<16:raise httpx.ReadTimeout('Synthetic uncertain response',request=request)
        self.c.callback=respond;unknown=self.process(job)
        self.assertEqual(('UNKNOWN',16),(unknown['status'],unknown['progress']['completed_records']))
        self.assertIn('unknown_recoveries',unknown)
        self.assertEqual(16,len(unknown['unknown_recoveries']))
        self.assertFalse(unknown['resumable'])
        ref=unknown['unknown_recovery']
        with self.assertRaises(ReviewError) as caught:
            with self.c.service() as service:
                service.retry_unknown('owner',job['id'],uuid4(),ref['event_sha256'],ref['step_sha256'],False)
        self.assertEqual('INVALID_REQUEST',caught.exception.code)
        with self.c.service() as service:
            restored=service.retry_unknown('owner',job['id'],uuid4(),ref['event_sha256'],ref['step_sha256'],True)
        self.assertEqual(('RUNNING',16),(restored['status'],restored['progress']['completed_records']))
        self.c.callback=None;result=self.finish(job)
        self.assertEqual('COMPLETED',result['status'],result)
        leaves=Counter(packet['batch']['start'] for packet in self.c.requests if packet.get('batch',{}).get('stage')=='LEAF')
        self.assertEqual(Counter({i:2 if i<16 else 1 for i in range(32)}),leaves)
        self.assertEqual({},self.f.runtime()['calls'])
        self.f.a.finish(job['id'])
