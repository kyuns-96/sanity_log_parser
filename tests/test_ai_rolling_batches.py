"""Synthetic rolling dispatch, bounded durable windows and concurrency-aware ETA."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from uuid import uuid4


from tests.support import PgTestCase


class RollingJobsTests(PgTestCase):
    def setUp(self):
        from tests.test_ai_parallel_batches import ParallelVwpJobsTests
        self.f = ParallelVwpJobsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def create(self, **kwargs):
        return self.f.create_job(execution='parallel-v2',**kwargs)


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
