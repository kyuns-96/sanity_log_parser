"""Synthetic rolling dispatch, bounded durable windows and concurrency-aware ETA."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime, timezone
from threading import Event, Lock
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError

from oa.sanity_ai_parallel_jobs import _wall_metrics
from tests.support import PgTestCase


class RollingMetricsTests(unittest.TestCase):
    def sample(self, *, width=8, total=10, next_index=8):
        event = {'status':'RUNNING','data':{'batch':{'execution':'parallel-v2',
                 'max_concurrency':width,'total_records':total}}}
        state = {'round':0,'phase':'CLASSIFY','round_total':total,'index':2,
                 'next_index':next_index,'round_samples':2,'records':2,'round_seconds':20,
                 'wall_record_sample':{'seconds':10,'records':2},
                 'wall_timing':{'round':0,'resume_count':0,'started_at':'2026-11-02T00:00:00+00:00',
                     'measured_at':'2026-11-02T00:00:10+00:00','base_samples':0,
                     'base_records':0,'base_units':0,'base_seconds':0}}
        wave = [{'status':'RESERVED','work':1,'reserved_at':'2026-11-02T00:00:00+00:00'}
                for _ in range(next_index-2)]
        return event,state,wave

    def test_eta_accounts_for_parallel_capacity_and_already_elapsed_calls(self):
        event,state,wave = self.sample()
        rate,eta,at = _wall_metrics(event,state,wave)
        self.assertEqual(5,rate)  # Observed wall throughput is still wall time.
        self.assertAlmostEqual(10,eta)
        self.assertEqual(state['wall_timing']['measured_at'],at)
        # A fresh observation never moves the measured completion timestamp.
        self.assertEqual((rate,eta,at),_wall_metrics(event,state,wave))

    def test_tail_uses_remaining_active_call_time_without_dividing_it_by_width(self):
        event,state,wave = self.sample(width=8,total=4,next_index=4)
        wave[1]['reserved_at']='2026-11-02T00:00:05+00:00'
        self.assertEqual(5,_wall_metrics(event,state,wave)[1])
        wave[1]['reserved_at']='2026-11-02T00:00:00+00:00'
        self.assertEqual(0,_wall_metrics(event,state,wave)[1])
        wave[1]['reserved_at']='2026-11-02T00:00:11+00:00'
        self.assertEqual(11,_wall_metrics(event,state,wave)[1])

    def test_stage_and_resume_samples_exclude_previous_call_durations(self):
        event,state,wave = self.sample(width=2,total=6,next_index=4)
        state.update(round_seconds=120,round_samples=12,records=4,index=4)
        state['wall_timing'].update(base_seconds=100,base_samples=10,base_records=2)
        # Two queued calls run together. Older 100 seconds must not inflate ETA.
        self.assertAlmostEqual(10,_wall_metrics(event,state,[])[1])
        for phase in ('TEXT','TEXT_MERGE','MERGE','REVIEW_MERGE'):
            with self.subTest(phase=phase):
                state['phase']=phase
                self.assertAlmostEqual(10,_wall_metrics(event,state,[])[1])

    def test_retry_cancel_failure_and_invalid_measurements_hide_eta(self):
        event,state,wave = self.sample()
        for slot in ({'status':'RETRY','retry_after':'2026-11-02T00:01:00+00:00'},
                     {'status':'UNKNOWN'},{'status':'FAILED'}):
            with self.subTest(slot=slot):
                self.assertIsNone(_wall_metrics(event,state,[*wave,slot])[1])
        for changes in ({'cancel_requested':True},{'dispatch_error':'AI_INPUT_STALE'},
                        {'round_samples':1},{'round_seconds':float('nan')},
                        {'wall_timing':{**state['wall_timing'],'base_seconds':True}}):
            with self.subTest(changes=changes):
                self.assertIsNone(_wall_metrics(event,{**state,**changes},wave)[1])
        self.assertIsNone(_wall_metrics(event,state,[{**wave[0],'reserved_at':'invalid'}])[1])


class RollingJobsTests(PgTestCase):
    def setUp(self):
        from tests.test_ai_parallel_batches import ParallelVwpJobsTests
        self.f = ParallelVwpJobsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def create(self, **kwargs):
        return self.f.create_job(execution='parallel-v2',**kwargs)

    def wait_status(self, job, predicate):
        until = time.monotonic()+10
        while time.monotonic() < until:
            result = self.f.status(job)
            if predicate(result):
                return result
            Event().wait(.02)
        self.fail('Synthetic rolling checkpoint did not become ready')

    def test_eight_slots_refill_while_the_first_seven_are_blocked_and_merge_waits(self):
        f=self.f;job=self.create(width=8,records=10)
        entered=[Event() for _ in range(10)];release=[Event() for _ in range(10)]
        lock=Lock();calls=[];active=0;peak=0
        def block(request):
            nonlocal active,peak
            packet=f.packet(request);stage=packet['batch']['stage'];index=packet['batch'].get('start')
            with lock:
                calls.append((stage,index));active+=1;peak=max(peak,active)
            try:
                if stage=='LEAF':
                    entered[index].set()
                    if not release[index].wait(30):raise AssertionError('synthetic call was not released')
            finally:
                with lock:active-=1
        f.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(f.run_wave)
            try:
                for signal in entered[:8]:self.assertTrue(signal.wait(10))
                release[7].set();self.assertTrue(entered[8].wait(10))
                release[8].set();self.assertTrue(entered[9].wait(10))
                self.assertIsNotNone(f.status(job)['progress']['wall_stage_eta_seconds'])
                release[9].set();partial=f.wait_progress(job,3)
                self.assertEqual('parallel-v2',partial['progress']['execution'])
                self.assertEqual(3,partial['progress']['round_completed'])
                self.assertFalse(future.done())
                with lock:self.assertFalse(any(stage=='MERGE' for stage,_ in calls))
                with f.c.service() as service:
                    from oa.sanity_ai_parallel_ledger import ParallelLedger
                    ledger=ParallelLedger(service,service._load(job['id']))
                    self.assertLessEqual(len(ledger.last()[0]['state']['wave']),8)
                    self.assertIsNotNone(ledger.audit(allow_unresolved=True))
                self.assertEqual(0,f.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])
            finally:
                for signal in release:signal.set()
            future.result(timeout=15)
        f.c.callback=None;result=f.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(8,peak)
        self.assertEqual(Counter({index:1 for index in range(10)}),Counter(index for stage,index in calls if stage=='LEAF'))

    def test_cancel_after_refill_stops_new_dispatch_and_resume_keeps_successes(self):
        f=self.f;job=self.create(records=5)
        entered=[Event() for _ in range(5)];release=[Event() for _ in range(5)]
        def block(request):
            packet=f.packet(request)
            if packet['batch']['stage']=='LEAF':
                index=packet['batch']['start'];entered[index].set()
                if not release[index].wait(30):raise AssertionError('synthetic call was not released')
        f.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(f.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                release[1].set();self.assertTrue(entered[2].wait(10))
                with f.c.service() as service:cancel=service.cancel('owner',job['id'])
                self.assertTrue(cancel['progress']['cancel_requested'])
            finally:
                for signal in release:signal.set()
            cancelled=future.result(timeout=15)
        self.assertEqual(('CANCELLED',3),(cancelled['status'],cancelled['progress']['completed_records']))
        self.assertFalse(entered[3].is_set());self.assertFalse(entered[4].is_set())
        with f.c.service() as service:service.resume('owner',job['id'],uuid4())
        f.c.callback=None;result=f.finish();self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(Counter({i:1 for i in range(5)}),Counter(p['batch']['start'] for p in f.c.requests if p['batch']['stage']=='LEAF'))

    def test_unknown_after_refill_drains_and_recovers_only_the_uncertain_child(self):
        f=self.f;job=self.create(records=5);blocked=Event();release=Event();unknown=Event()
        def respond(request):
            packet=f.packet(request)
            if packet['batch']['stage']!='LEAF':return
            index=packet['batch']['start']
            if index==0:
                blocked.set()
                if not release.wait(30):raise AssertionError('synthetic call was not released')
            if index==2:
                unknown.set();raise httpx.ReadTimeout('synthetic timeout',request=request)
        f.c.callback=respond
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(f.run_wave)
            try:
                self.assertTrue(blocked.wait(10));self.assertTrue(unknown.wait(10))
                partial=self.wait_status(job,lambda j:j['progress']['unknown_calls']==1)
                self.assertEqual('RUNNING',partial['status']);self.assertNotIn('unknown_recovery',partial)
                self.assertEqual(3,len(f.v.wire))
            finally:release.set()
            result=future.result(timeout=15)
        self.assertEqual('UNKNOWN',result['status']);self.assertEqual([2],[r['index'] for r in result['unknown_recoveries']])
        info=result['unknown_recovery']
        with f.c.service() as service:
            service.retry_unknown('owner',job['id'],uuid4(),info['event_sha256'],info['step_sha256'],True)
        f.c.callback=None;result=f.finish();self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(Counter({0:1,1:1,2:2,3:1,4:1}),Counter(p['batch']['start'] for p in f.c.requests if p['batch']['stage']=='LEAF'))

    def test_429_after_refill_pauses_fresh_descriptors_until_backoff_expires(self):
        f=self.f;job=self.create(records=5);blocked=Event();release=Event();busy=Event()
        def respond(request):
            packet=f.packet(request)
            if packet['batch']['stage']!='LEAF':return
            index=packet['batch']['start']
            if index==0:
                blocked.set()
                if not release.wait(30):raise AssertionError('synthetic call was not released')
            if index==2:
                busy.set();return httpx.Response(429,headers={'Retry-After':'20'})
        f.c.callback=respond
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(f.run_wave)
            try:
                self.assertTrue(blocked.wait(10));self.assertTrue(busy.wait(10))
                partial=self.wait_status(job,lambda j:bool(j['progress']['retry_after']))
                self.assertIsNone(partial['progress']['wall_stage_eta_seconds'])
                self.assertEqual(3,len(f.v.wire))
            finally:release.set()
            waiting=future.result(timeout=15)
        self.assertEqual('RUNNING',waiting['status']);self.assertEqual(3,len(f.v.wire))
        self.assertIsNone(f.run_wave());self.assertEqual(3,len(f.v.wire))
        f.c.callback=None;at=datetime.now(timezone.utc)+timedelta(seconds=400)
        with patch('oa.sanity_ai_jobs._db_now',return_value=at):
            result=f.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(Counter({0:1,1:1,2:2,3:1,4:1}),Counter(p['batch']['start'] for p in f.c.requests if p['batch']['stage']=='LEAF'))

    def test_another_worker_cannot_duplicate_or_overfill_the_rolling_window(self):
        f=self.f;job=self.create(records=5);entered=[Event() for _ in range(5)];release=Event()
        def block(request):
            packet=f.packet(request)
            if packet['batch']['stage']=='LEAF':
                index=packet['batch']['start'];entered[index].set()
                if index!=1 and not release.wait(30):raise AssertionError('synthetic call was not released')
        f.c.callback=block
        with ThreadPoolExecutor(max_workers=2) as executor:
            first=executor.submit(f.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[2].wait(10))
                second=executor.submit(f.run_wave);second.result(timeout=10)
                self.assertEqual(3,len(f.v.wire));self.assertFalse(entered[3].is_set())
                self.assertEqual(2,f.status(job)['progress']['inflight_calls'])
            finally:release.set()
            first.result(timeout=15)
        f.c.callback=None;self.assertEqual('COMPLETED',f.finish()['status'])
        self.assertEqual(Counter({i:1 for i in range(5)}),Counter(p['batch']['start'] for p in f.c.requests if p['batch']['stage']=='LEAF'))

    def test_budget_includes_reserved_and_completed_calls_during_refill(self):
        f=self.f;self.create(records=3,max_calls=3)
        first=f.run_wave()
        self.assertEqual(3,first['progress']['completed_records'])
        result=f.finish()
        self.assertEqual(('FAILED','AI_BATCH_LIMIT'),(result['status'],result['error']))
        self.assertEqual(3,len(f.v.wire));self.assertEqual(3,result['progress']['completed_records'])
        self.assertEqual(0,f.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_restart_after_refills_preserves_successes_and_rejects_a_late_response(self):
        f=self.f;job=self.create(records=4);blocked=Event();release=Event()
        def block(request):
            packet=f.packet(request)
            if packet['batch']['stage']=='LEAF' and packet['batch']['start']==0:
                blocked.set()
                if not release.wait(30):raise AssertionError('synthetic call was not released')
        f.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            old=executor.submit(f.run_wave)
            try:
                self.assertTrue(blocked.wait(10));f.wait_progress(job,3)
                at=datetime.now(timezone.utc)+timedelta(seconds=600)
                with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):
                    unknown=f.c.run_job(now=at)
                    self.assertEqual('UNKNOWN',unknown['status'])
                    self.assertEqual(3,unknown['progress']['completed_records']);self.assertEqual(4,len(f.v.wire))
                    info=unknown['unknown_recovery']
                    self.assertEqual([0],[item['index'] for item in unknown['unknown_recoveries']])
                    with f.c.service() as service:
                        service.retry_unknown('owner',job['id'],uuid4(),info['event_sha256'],info['step_sha256'],True)
                    f.c.callback=None;recovered=f.c.run_job(now=at)
                    self.assertEqual(4,recovered['progress']['completed_records'])
                    release.set();old.result(timeout=15)
                    result=f.finish()
            finally:release.set()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(Counter({0:2,1:1,2:1,3:1}),Counter(p['batch']['start'] for p in f.c.requests if p['batch']['stage']=='LEAF'))

    def test_credential_change_after_refill_stops_further_requests_and_publication(self):
        f=self.f;job=self.create(records=5);entered=[Event() for _ in range(5)];release=Event()
        def block(request):
            packet=f.packet(request)
            if packet['batch']['stage']=='LEAF':
                index=packet['batch']['start'];entered[index].set()
                if index!=1 and not release.wait(30):raise AssertionError('synthetic call was not released')
        f.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(f.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[2].wait(10))
                with f.v.store() as (store,_,_):store.tokens(store.get(f.w.connection)).save('owner','synthetic-rotated-key',1)
            finally:release.set()
            result=future.result(timeout=15)
        self.assertEqual(('STALE','AI_CREDENTIAL_CHANGED'),(result['status'],result['error']))
        self.assertFalse(entered[3].is_set());self.assertFalse(entered[4].is_set())
        self.assertEqual(0,f.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_refill_preparation_failure_rolls_back_unsent_reservations_and_drains(self):
        from oa.sanity_ai_batch_model import BatchModel
        original=BatchModel.prepare
        f=self.f;job=self.create(records=4);blocked=Event();release=Event()
        def prepare(model,descriptor,nodes=None):
            if descriptor['kind']=='LEAF' and descriptor['start']==2:
                raise ReviewError('AI_INPUT_LIMIT','Synthetic refill preparation failure')
            return original(model,descriptor,nodes)
        def block(request):
            packet=f.packet(request)
            if packet['batch']['stage']=='LEAF' and packet['batch']['start']==0:
                blocked.set()
                if not release.wait(30):raise AssertionError('synthetic call was not released')
        f.c.callback=block
        with patch.object(BatchModel,'prepare',prepare),ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(f.run_wave)
            try:
                self.assertTrue(blocked.wait(10));f.wait_progress(job,1)
                from oa.sanity_ai_parallel_ledger import ParallelLedger
                until=time.monotonic()+10
                while time.monotonic() < until:
                    with f.c.service() as service:
                        ledger=ParallelLedger(service,service._load(job['id']));state=ledger.last()[0]['state']
                        if state.get('dispatch_error'):
                            self.assertEqual('AI_INPUT_LIMIT',state['dispatch_error'])
                            self.assertEqual(2,state['next_index'])
                            self.assertEqual(2,len([body for body,_ in ledger.rows('call.') if body['kind']=='RESERVATION']))
                            break
                    Event().wait(.02)
                else:self.fail('Synthetic preparation failure was not saved')
                self.assertFalse(future.done());self.assertEqual('RUNNING',f.status(job)['status'])
            finally:release.set()
            result=future.result(timeout=15)
        self.assertEqual(('FAILED','AI_INPUT_LIMIT'),(result['status'],result['error']))
        self.assertEqual(2,len(f.v.wire));self.assertEqual(2,result['progress']['completed_records'])

    def test_eta_updates_after_refills_and_uses_current_inflight_ages(self):
        from oa.sanity_ai_parallel_jobs import _execute
        f=self.f;job=self.create(records=5);clock=[datetime.now(timezone.utc)]
        entered=[Event() for _ in range(5)];release=[Event() for _ in range(5)]
        def block(request):
            packet=f.packet(request)
            if packet['batch']['stage']=='LEAF':
                index=packet['batch']['start'];entered[index].set()
                if not release[index].wait(30):raise AssertionError('synthetic call was not released')
        def timed(*args):
            value,error,_=_execute(*args)
            return value,error,10.0
        f.c.callback=block
        with patch('oa.sanity_ai_jobs._db_now',side_effect=lambda _db:clock[0]), \
                patch('oa.sanity_ai_rolling_jobs._execute',side_effect=timed),ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(f.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10));start=clock[0]
                clock[0]=start+timedelta(seconds=10);release[1].set();self.assertTrue(entered[2].wait(10))
                self.assertIsNone(f.status(job)['progress']['wall_stage_eta_seconds'])
                clock[0]=start+timedelta(seconds=20);release[2].set();self.assertTrue(entered[3].wait(10))
                first=f.status(job)['progress']
                self.assertEqual(2,first['round_completed']);self.assertAlmostEqual(10,first['wall_stage_eta_seconds'])
                self.assertEqual(clock[0].isoformat(),first['wall_measured_at'])
                clock[0]=start+timedelta(seconds=30);release[3].set();self.assertTrue(entered[4].wait(10))
                latest=f.status(job)['progress']
                self.assertEqual(3,latest['round_completed']);self.assertAlmostEqual(10,latest['wall_stage_eta_seconds'])
                self.assertNotEqual(first['wall_measured_at'],latest['wall_measured_at'])
                clock[0]=start+timedelta(seconds=40)
            finally:
                for signal in release:signal.set()
            result=future.result(timeout=15)
        self.assertEqual('MERGE',result['progress']['phase']);self.assertIsNone(result['progress']['wall_stage_eta_seconds'])
        f.c.callback=None;self.assertEqual('COMPLETED',f.finish()['status'])
