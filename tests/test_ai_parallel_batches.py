"""Synthetic parallel batch jobs: bounded dispatch, durable results and recovery."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from dataclasses import replace
import json
from threading import Event, Lock
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_ai_execution import executing_on, worker_site
from common.sanity_common import ReviewError
from common.sanity_db import json_value
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_jobs import _now
from tests.support import PgTestCase


class ParallelSettingsTests(unittest.TestCase):
    def test_concurrency_is_opt_in_bounded_and_legacy_serial_public_is_unchanged(self):
        self.assertEqual(1,BatchSettings().max_concurrency)
        self.assertNotIn('max_concurrency',BatchSettings().public())
        self.assertNotIn('max_concurrency',BatchSettings(max_concurrency=1).public())
        self.assertEqual(2,BatchSettings(max_concurrency=2).public()['max_concurrency'])
        self.assertEqual(8,BatchSettings(max_concurrency=8).public()['max_concurrency'])
        for value in (0,9,-1,True,1.5,'2',None):
            with self.subTest(value=value),self.assertRaises(ReviewError):BatchSettings(max_concurrency=value)


class ParallelWallMetricsTests(unittest.TestCase):
    def test_wall_rates_use_stage_samples_and_correct_phase_units(self):
        from oa.sanity_ai_parallel_jobs import _wall_metrics
        event={'status':'RUNNING','data':{'batch':{'total_records':100}}}
        state={'round':0,'phase':'CLASSIFY','round_total':10,'index':0,'round_samples':2,'records':20,
               'wall_record_sample':{'seconds':10.0,'records':20},
               'wall_timing':{'round':0,'resume_count':0,'started_at':'2026-10-30T00:00:00+00:00',
                              'measured_at':'2026-10-30T00:00:10+00:00','base_samples':0,'base_records':0,'base_units':0}}
        wave=[{'status':'DONE'},{'status':'DONE'},{'status':'RESERVED'}]
        rate,eta,at=_wall_metrics(event,state,wave)
        self.assertEqual((.5,40.0,'2026-10-30T00:00:10+00:00'),(rate,eta,at))
        self.assertEqual((rate,eta,at),_wall_metrics(event,{**state,'phase':'REVIEW'},wave))
        merge={**state,'round':1,'phase':'MERGE','round_total':6,'records':100,
               'wall_timing':{**state['wall_timing'],'round':1,'base_records':100}}
        self.assertEqual((.5,20.0,'2026-10-30T00:00:10+00:00'),_wall_metrics(event,merge,wave))
        for phase in ('TEXT','TEXT_MERGE','REVIEW_MERGE'):
            with self.subTest(phase=phase):
                self.assertEqual((.5,20.0,'2026-10-30T00:00:10+00:00'),
                                 _wall_metrics(event,{**merge,'phase':phase},wave))
        self.assertEqual((.5,None,None),_wall_metrics(event,{**state,'round_samples':1},wave))
        self.assertEqual((.5,None,None),_wall_metrics(event,{**state,'round':1},wave))
        self.assertEqual((.5,None,None),_wall_metrics({**event,'data':{**event['data'],'resume_count':1}},state,wave))
        for status in ('FAILED','UNKNOWN','CANCELLED','COMPLETED'):
            self.assertIsNone(_wall_metrics({**event,'status':status},state,wave)[1])
        self.assertIsNone(_wall_metrics(event,{**state,'cancel_requested':True},wave)[1])
        self.assertIsNone(_wall_metrics(event,state,[{'status':'DONE'},{'status':'DONE'},
                                                      {'status':'RETRY','retry_after':'2026-10-30T00:00:20+00:00'}])[1])
        self.assertEqual((None,None,None),_wall_metrics(event,{'round':0},[]))
        for changes in ({'started_at':'invalid'},{'measured_at':'invalid'}, {'base_records':True},
                        {'base_samples':-1}, {'base_units':None}):
            with self.subTest(changes=changes):
                broken={**state,'wall_timing':{**state['wall_timing'],**changes}}
                self.assertEqual((.5,None,None),_wall_metrics(event,broken,wave))


class ParallelVwpJobsTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w=VwpWorkerTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.c=self.w.c;self.v=self.w.v;self.admin,self.schema=self.c.admin,self.c.schema

    def create_job(self, *, width=2, records=2, max_calls=20000, merge_groups=20):
        from tests.test_vwp_inference import configuration
        report=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='report'").fetchone()[0]
        if records>2:
            for index in range(2,records):
                row={**report['records'][0], 'id':'extra-'+str(index), 'fields':{**report['records'][0]['fields'],'object':'top/extra-'+str(index)}}
                report['records'].append(row)
            report['record_count']=len(report['records'])
            self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key='report'",(json_value(report),))
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
            overhead_tokens=1024,max_records=1,output_parameter='max_tokens',max_concurrency=width,max_calls=max_calls,
            merge_groups=merge_groups)
        self.w.connection=self.v.create(configuration(batch=settings.public()))
        job=self.w.request();prepared=self.w.prepare()
        self.assertEqual('PENDING',prepared['status'],prepared)
        return job

    def run_wave(self):
        with executing_on('vwp'):return self.c.run_job()

    def status(self,job):
        with self.c.service() as service:return service.status('owner',job['id'])

    def wait_progress(self,job,count):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            result=self.status(job)
            if result.get('progress',{}).get('completed_calls',0)>=count:return result
            Event().wait(.03)
        self.fail('Completed parallel sibling was not persisted while the other was blocked')

    @staticmethod
    def packet(request):return json.loads(json.loads(request.content)['messages'][1]['content'])

    def finish(self):
        with executing_on('vwp'):
            for _ in range(30):
                result=self.c.run_job()
                if result is None or result['status'] in ('COMPLETED','STALE','FAILED','UNKNOWN','CANCELLED'):return result
        self.fail('Synthetic parallel job did not finish')

    def test_true_overlap_out_of_order_commit_and_merge_waits_for_all_siblings(self):
        job=self.create_job(records=3);entered=[Event(),Event()];release=[Event(),Event()];calls=[];lock=Lock()
        counters={'active':0,'peak':0}
        def block(request):
            packet=self.packet(request);stage=packet['batch']['stage'];index=packet['batch'].get('start')
            with lock:
                calls.append((stage,index,worker_site()))
                counters['active']+=1;counters['peak']=max(counters['peak'],counters['active'])
            if stage=='LEAF':
                if index in (0,1):
                    entered[index].set()
                    if not release[index].wait(20):raise AssertionError('synthetic leaf was not released')
            with lock:counters['active']-=1
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                self.assertFalse(future.done())
                with lock:self.assertEqual({('LEAF',0,'vwp'),('LEAF',1,'vwp')},set(calls))
                release[1].set()
                partial=self.wait_progress(job,1)
                self.assertEqual('RUNNING',partial['status'])
                self.assertFalse(future.done())
                with lock:self.assertFalse(any(stage=='MERGE' for stage,_,_ in calls))
                self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])
                release[0].set();future.result(timeout=10)
            finally:
                for event in release:event.set()
        self.c.callback=None;result=self.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(2,counters['peak'])
        with self.c.service() as service:
            event=service._load(job['id'])
            self.assertEqual('parallel-v1',event['data']['batch']['execution'])
            self.assertEqual(2,event['data']['batch']['max_concurrency'])
        self.assertEqual(4,len(self.v.wire))

    def test_wall_samples_follow_overlapping_completions_and_reset_at_merge(self):
        job=self.create_job(records=4);entered=[Event(),Event()];release=[Event(),Event()]
        clock=[_now()]
        def block(request):
            packet=self.packet(request)
            if packet['batch']['stage']=='LEAF' and packet['batch']['start'] in (0,1):
                index=packet['batch']['start'];entered[index].set()
                if not release[index].wait(20):raise AssertionError('synthetic leaf was not released')
        self.c.callback=block
        with patch('oa.sanity_ai_jobs._db_now',side_effect=lambda _db:clock[0]),ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                start=clock[0];clock[0]=start+timedelta(seconds=6);release[1].set()
                partial=self.wait_progress(job,1)
                self.assertIsNone(partial['progress']['wall_seconds_per_record'])
                self.assertIsNone(partial['progress']['wall_stage_eta_seconds'])
                clock[0]=start+timedelta(seconds=10);release[0].set();first=future.result(timeout=10)
                p=first['progress']
                self.assertEqual((2,5.0,10.0),(p['completed_records'],p['wall_seconds_per_record'],p['wall_stage_eta_seconds']))
                self.assertEqual(clock[0].isoformat(),p['wall_measured_at'])
                self.c.callback=None;clock[0]=start+timedelta(seconds=20)
                second=self.run_wave();self.assertEqual(4,second['progress']['completed_records'])
                self.assertEqual(5.0,second['progress']['wall_seconds_per_record'])
                self.assertIsNone(second['progress']['wall_stage_eta_seconds'])
                merge=self.run_wave();self.assertEqual('MERGE',merge['progress']['phase'])
                self.assertEqual(5.0,merge['progress']['wall_seconds_per_record'])
                self.assertIsNone(merge['progress']['wall_stage_eta_seconds'])
            finally:
                for signal in release:signal.set()

    def test_invalid_optional_timing_does_not_change_successful_model_result(self):
        from oa.sanity_ai_parallel_ledger import ParallelLedger
        job=self.create_job();entered=[Event(),Event()];release=Event()
        def block(request):
            packet=self.packet(request)
            if packet['batch']['stage']=='LEAF':
                entered[packet['batch']['start']].set()
                if not release.wait(20):raise AssertionError('synthetic leaf was not released')
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                with self.c.service() as service:
                    service._lock(job['id']);event=service._load(job['id'])
                    ledger=ParallelLedger(service,event);step,ref=ledger.last()
                    timing={**step['state']['wall_timing'],'base_records':True}
                    ledger.step(step,ref,'READY',{**step['state'],'wall_timing':timing})
                self.assertIsNone(self.status(job)['progress']['wall_stage_eta_seconds'])
            finally:release.set()
            result=future.result(timeout=10)
        self.assertEqual(('RUNNING',2),(result['status'],result['progress']['completed_calls']))
        self.assertIsNone(result['progress']['wall_seconds_per_record'])
        self.c.callback=None;result=self.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(3,len(self.v.wire))

    def test_two_workers_cannot_reserve_or_dispatch_a_child_twice(self):
        job=self.create_job();entered=[Event(),Event()];release=Event();second_started=Event();calls=[];lock=Lock()
        def block(request):
            packet=self.packet(request);stage=packet['batch']['stage'];index=packet['batch'].get('start')
            with lock:calls.append((stage,index))
            if stage=='LEAF':
                entered[index].set()
                if not release.wait(20):raise AssertionError('synthetic leaf was not released')
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=2) as executor:
            first=executor.submit(self.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                def other_worker():
                    second_started.set()
                    return self.run_wave()
                second=executor.submit(other_worker)
                self.assertTrue(second_started.wait(10))
                with lock:self.assertEqual(Counter({('LEAF',0):1,('LEAF',1):1}),Counter(calls))
            finally:release.set()
            first.result(timeout=10)
            second.result(timeout=10)
        self.c.callback=None
        result=self.status(job)
        if result['status']!='COMPLETED':result=self.finish()
        self.assertEqual('COMPLETED',result['status'])
        self.assertEqual(Counter({0:1,1:1}),Counter(packet['batch']['start'] for packet in self.c.requests
                         if packet.get('batch',{}).get('stage')=='LEAF'))
        self.assertEqual(3,len(self.v.wire))

    def test_two_unknown_children_recover_together_without_repeating_successful_sibling(self):
        job=self.create_job(width=3,records=3);calls=[];lock=Lock()
        def respond(request):
            packet=self.packet(request);stage=packet['batch']['stage'];index=packet['batch'].get('start')
            with lock:calls.append((stage,index))
            if stage=='LEAF' and index in (0,1):raise httpx.ReadTimeout('synthetic timeout',request=request)
        self.c.callback=respond
        unknown=self.run_wave()
        self.assertEqual('UNKNOWN',unknown['status'],unknown)
        self.assertEqual(1,unknown['progress']['completed_calls'])
        self.assertEqual(2,len(unknown['unknown_recoveries']))
        info=unknown['unknown_recovery']
        self.assertEqual({0,1},{item['index'] for item in unknown['unknown_recoveries']})
        self.assertEqual(3,len(self.v.wire))
        request_id=uuid4()
        with patch('oa.sanity_ai_jobs.AIJobService._context',side_effect=AssertionError('recovery hydrated source')):
            with self.c.service() as service:
                resumed=service.retry_unknown('owner',job['id'],request_id,info['event_sha256'],info['step_sha256'],True)
                replay=service.retry_unknown('owner',job['id'],request_id,info['event_sha256'],info['step_sha256'],True)
                self.assertEqual(resumed,replay)
                with self.assertRaises(ReviewError) as conflict:
                    service.retry_unknown('owner',job['id'],request_id,info['event_sha256'],'0'*64,True)
                self.assertEqual('REQUEST_CONFLICT',conflict.exception.code)
                with self.assertRaises(ReviewError) as forbidden:
                    service.retry_unknown('other',job['id'],request_id,info['event_sha256'],info['step_sha256'],True)
                self.assertEqual('FORBIDDEN',forbidden.exception.code)
        self.assertEqual('RUNNING',resumed['status'])
        self.c.callback=None;result=self.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        packets=self.c.requests
        leaf_counts=Counter(packet['batch']['start'] for packet in packets if packet.get('batch',{}).get('stage')=='LEAF')
        self.assertEqual(Counter({0:2,1:2,2:1}),leaf_counts)
        self.assertEqual(1,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_unknown_recovery_is_not_offered_while_other_siblings_are_in_flight(self):
        job=self.create_job(width=3,records=3);unknown_sent=Event();blocked=Event();release=Event()
        def respond(request):
            packet=self.packet(request)
            if packet['batch']['stage']!='LEAF':return None
            index=packet['batch']['start']
            if index==0:
                unknown_sent.set();raise httpx.ReadTimeout('synthetic timeout',request=request)
            if index==1:
                blocked.set()
                if not release.wait(20):raise AssertionError('synthetic sibling was not released')
        self.c.callback=respond
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_wave)
            try:
                self.assertTrue(unknown_sent.wait(10));self.assertTrue(blocked.wait(10))
                partial=self.wait_progress(job,1)
                self.assertEqual('RUNNING',partial['status'],partial)
                self.assertFalse(partial.get('can_retry_unknown',False))
                self.assertNotIn('unknown_recovery',partial)
                self.assertFalse(future.done())
            finally:release.set()
            unknown=future.result(timeout=10)
        self.assertEqual('UNKNOWN',unknown['status'],unknown)
        self.assertEqual(2,unknown['progress']['completed_calls'])
        self.assertEqual(1,len(unknown['unknown_recoveries']))

    def test_expired_reservation_is_unknown_after_restart_and_late_reply_is_discarded(self):
        job=self.create_job();started=Event();release=Event()
        def block(request):
            packet=self.packet(request)
            if packet['batch']['stage']=='LEAF' and packet['batch']['start']==0:
                started.set()
                if not release.wait(20):raise AssertionError('synthetic call was not released')
        self.c.callback=block
        at=_now()+timedelta(seconds=600)
        with ThreadPoolExecutor(max_workers=1) as executor:
            old=executor.submit(self.run_wave)
            try:
                self.assertTrue(started.wait(10));self.wait_progress(job,1)
                self.assertEqual(2,len(self.v.wire))
                # Advancing the synthetic clock models a worker that disappeared
                # after reserving a call; the old request is held for the late-reply check.
                with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):
                    unknown=self.c.run_job(now=at)
                    self.assertEqual('UNKNOWN',unknown['status'],unknown)
                    self.assertEqual(1,unknown['progress']['completed_calls'])
                    self.assertEqual([0],[item['index'] for item in unknown['unknown_recoveries']])
                    self.assertEqual(2,len(self.v.wire))
                    self.assertIsNone(self.c.run_job(now=at))
                    self.assertEqual(2,len(self.v.wire))
                    info=unknown['unknown_recovery']
                    with self.c.service() as service:
                        service.retry_unknown('owner',job['id'],uuid4(),info['event_sha256'],info['step_sha256'],True)
                    self.c.callback=None
                    recovered=self.c.run_job(now=at)
                    self.assertEqual(2,recovered['progress']['completed_calls'])
                    release.set();old.result(timeout=10)
                    result=self.status(job)
                    if result['status']!='COMPLETED':result=self.finish()
            finally:release.set()
        self.assertEqual('COMPLETED',result['status'],result)
        self.assertEqual(Counter({0:2,1:1}),Counter(packet['batch']['start'] for packet in self.c.requests
                         if packet.get('batch',{}).get('stage')=='LEAF'))
        self.assertEqual(1,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_cancel_during_parallel_calls_drains_without_dispatch_or_publication(self):
        job=self.create_job();entered=[Event(),Event()];release=Event()
        def block(request):
            packet=self.packet(request)
            if packet['batch']['stage']=='LEAF':
                entered[packet['batch']['start']].set()
                if not release.wait(20):raise AssertionError('synthetic leaf was not released')
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                with self.c.service() as service:cancel=service.cancel('owner',job['id'])
                self.assertEqual('RUNNING',cancel['status'])
                self.assertTrue(cancel['progress']['cancel_requested'])
                self.assertEqual(2,cancel['progress']['inflight_calls'])
                self.run_wave()
                self.assertEqual(2,len(self.v.wire))
                self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])
            finally:release.set()
            result=future.result(timeout=10)
        self.assertEqual('CANCELLED',result['status'])
        self.assertEqual(2,result['progress']['completed_calls'])
        self.assertEqual(0,result['progress']['inflight_calls'])
        self.assertEqual('CANCELLED',self.status(job)['status'])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_resume_after_drained_cancel_keeps_both_children_and_calls_only_merge(self):
        job=self.create_job();entered=[Event(),Event()];release=Event()
        def block(request):
            packet=self.packet(request)
            if packet['batch']['stage']=='LEAF':
                entered[packet['batch']['start']].set()
                if not release.wait(20):raise AssertionError('synthetic leaf was not released')
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                with self.c.service() as service:service.cancel('owner',job['id'])
            finally:release.set()
            cancelled=future.result(timeout=10)
        self.assertEqual(('CANCELLED',2),(cancelled['status'],cancelled['progress']['completed_calls']))
        self.assertTrue(cancelled['resumable'])
        with patch('oa.sanity_ai_jobs.AIJobService._context',side_effect=AssertionError('resume hydrated source')):
            with self.c.service() as service:resumed=service.resume('owner',job['id'],uuid4())
        self.assertEqual(('RUNNING',2),(resumed['status'],resumed['progress']['completed_calls']))
        self.c.callback=None;result=self.finish()
        self.assertEqual('COMPLETED',result['status'],result)
        packets=self.c.requests
        self.assertEqual(Counter({0:1,1:1}),Counter(packet['batch']['start'] for packet in packets
                         if packet.get('batch',{}).get('stage')=='LEAF'))
        self.assertEqual(1,sum(packet.get('batch',{}).get('stage')=='MERGE' for packet in packets))
        self.assertEqual(3,len(self.v.wire))

    def test_rotating_personal_key_during_parallel_calls_blocks_late_publication(self):
        job=self.create_job();entered=[Event(),Event()];release=Event()
        def block(request):
            packet=self.packet(request)
            if packet['batch']['stage']=='LEAF':
                entered[packet['batch']['start']].set()
                if not release.wait(20):raise AssertionError('synthetic leaf was not released')
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                with self.v.store() as (store,_,_):
                    store.tokens(store.get(self.w.connection)).save('owner','synthetic-rotated-key',1)
            finally:release.set()
            result=future.result(timeout=10)
        self.assertEqual(('STALE','AI_CREDENTIAL_CHANGED'),(result['status'],result['error']))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def assert_stale_during_calls(self, mutation):
        job=self.create_job();entered=[Event(),Event()];release=Event()
        def block(request):
            packet=self.packet(request)
            if packet['batch']['stage']=='LEAF':
                entered[packet['batch']['start']].set()
                if not release.wait(20):raise AssertionError('synthetic leaf was not released')
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.run_wave)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[1].wait(10))
                mutation()
            finally:release.set()
            result=future.result(timeout=10)
        self.assertEqual('STALE',result['status'],result)
        with self.c.service() as service:self.assertEqual('STALE',service._load(job['id'])['status'])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_revoked_access_during_parallel_calls_blocks_late_publication(self):
        self.assert_stale_during_calls(lambda:self.c.admin_query(
            "UPDATE {oa} SET payload=jsonb_set(payload,'{{owners}}','[]'::jsonb) WHERE record_type='WS_BLOCK'"))

    def test_source_change_during_parallel_calls_blocks_late_publication(self):
        self.assert_stale_during_calls(lambda:self.c.admin_query(
            "UPDATE {oa} SET payload=jsonb_set(payload,'{{rule}}',%s) WHERE record_type='WS_REPORT'",(json_value('synthetic changed'),)))

    def test_429_child_retries_five_times_without_repeating_successful_sibling(self):
        self.create_job();calls=[];lock=Lock()
        def busy(request):
            packet=self.packet(request);stage=packet['batch']['stage'];index=packet['batch'].get('start')
            with lock:calls.append((stage,index))
            if stage=='LEAF' and index==0:return httpx.Response(429,headers={'Retry-After':'20'})
        self.c.callback=busy
        first=self.run_wave()
        self.assertEqual('RUNNING',first['status'],first)
        self.assertEqual(1,first['progress']['completed_calls'])
        start=_now()
        for number in range(1,6):
            at=start+timedelta(seconds=400*number)
            with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):result=self.c.run_job(now=at)
        self.assertEqual('FAILED',result['status'],result)
        with lock:self.assertEqual(Counter({('LEAF',0):6,('LEAF',1):1}),Counter(calls))
        self.assertEqual(7,len(self.v.wire))
        self.assertIsNone(self.run_wave())

    def test_parallel_rationale_diagnostic_is_saved_and_clears_when_retry_starts(self):
        from oa.sanity_ai_parallel_ledger import ParallelLedger
        from tests.test_ai_retry_resume import ResumeCheckpointTests
        job=self.create_job()
        def invalid(request):
            packet=self.packet(request)
            if packet['batch']['stage']=='LEAF' and packet['batch']['start']==0:
                return ResumeCheckpointTests.invalid_rationale(request)
        self.c.callback=invalid
        first=self.run_wave()
        detail=first['validation_failure']
        self.assertEqual('RATIONALE_EVIDENCE_OUTSIDE_GROUP',detail['code'])
        self.assertEqual(1,first['progress']['completed_calls'])
        with self.c.service() as service:
            self.assertEqual(detail,service.status('owner',job['id'])['validation_failure'])
            ledger=ParallelLedger(service,service._load(job['id']))
            step,_=ledger.last();slot=next(s for s in step['state']['wave'] if s['index']==0)
            self.assertEqual(detail,slot['validation_failure'])
            outcome,_=ledger.get(slot['reservation']['key']+'.outcome')
            self.assertEqual(detail,outcome['validation_failure'])
            self.assertEqual({'kind','error','model_called','response_complete'},set(outcome['receipt']))
        entered=Event();release=Event()
        def block(_request):
            entered.set()
            if not release.wait(20):raise AssertionError('synthetic retry was not released')
        self.c.callback=block;at=_now()+timedelta(seconds=400)
        with patch('oa.sanity_ai_jobs._db_now',return_value=at),ThreadPoolExecutor(max_workers=1) as executor:
            def retry():
                with executing_on('vwp'):return self.c.run_job(now=at)
            future=executor.submit(retry)
            try:
                self.assertTrue(entered.wait(10))
                self.assertNotIn('validation_failure',self.status(job))
            finally:release.set()
            result=future.result(timeout=10)
        self.assertNotIn('validation_failure',result)
        self.c.callback=None;result=self.finish();self.assertEqual('COMPLETED',result['status'],result)
        packets=self.c.requests
        self.assertEqual(Counter({0:2,1:1}),Counter(packet['batch']['start'] for packet in packets
                         if packet.get('batch',{}).get('stage')=='LEAF'))
        with self.c.service() as service:
            ledger=ParallelLedger(service,service._load(job['id']));self.assertIsNotNone(ledger.audit())
            history=json.dumps([value for value,_ in ledger.rows('call.')],ensure_ascii=False)
            self.assertIn('RATIONALE_EVIDENCE_OUTSIDE_GROUP',history)
            self.assertNotIn('PRIVATE_SYNTHETIC_REASON',history);self.assertNotIn('PRIVATE_SYNTHETIC_FOREIGN_ID',history)

    def test_call_budget_includes_all_reserved_siblings_and_never_overdispatches(self):
        self.create_job(width=2,records=2,max_calls=2)
        first=self.run_wave()
        self.assertEqual('RUNNING',first['status'],first)
        self.assertEqual(2,first['progress']['completed_calls'])
        result=self.finish()
        self.assertEqual(('FAILED','AI_BATCH_LIMIT'),(result['status'],result['error']))
        self.assertEqual(2,len(self.v.wire))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_unlinked_synthetic_result_is_rejected_by_final_audit(self):
        from oa.sanity_ai_parallel_ledger import ParallelLedger
        job=self.create_job();first=self.run_wave()
        self.assertEqual(('RUNNING',2),(first['status'],first['progress']['completed_calls']))
        with self.c.service() as service:
            ledger=ParallelLedger(service,service._load(job['id']))
            original,_=ledger.get('result.000.000000')
            ledger.put('result.999.000000',original)
        result=self.finish()
        self.assertEqual(('FAILED','AI_BATCH_CORRUPT'),(result['status'],result['error']))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_singleton_merge_passthrough_is_audited_without_extra_model_call(self):
        from oa.sanity_ai_parallel_ledger import ParallelLedger
        job=self.create_job(width=2,records=3,merge_groups=2)
        result=self.finish();self.assertEqual('COMPLETED',result['status'],result)
        with self.c.service() as service:
            ledger=ParallelLedger(service,service._load(job['id']))
            self.assertIsNotNone(ledger.audit())
            passthrough=[row for row,_ in ledger.rows('result.') if row['metadata'] is None]
            self.assertTrue(passthrough)
        self.assertEqual(result['progress']['completed_calls'],len(self.v.wire))


class ParallelPreservedTextTests(unittest.TestCase):
    def test_complete_text_classification_confirmation_and_review(self):
        from common.sanity_common import digest
        from common.sanity_db import Database
        from oa.sanity_ai_jobs import AIJobService
        from oa.sanity_ai_parallel_ledger import ParallelLedger
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        from tests.test_ai_evidence_batches import EvidenceTextDatabaseTests
        from tests.test_submission_tokenless import OWNER

        fixture=EvidenceTextDatabaseTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.advisor.settings=replace(fixture.advisor.settings,
            batch=replace(fixture.advisor.settings.batch,max_concurrency=2))
        job=fixture.enqueue();self.assertEqual('TEXT',fixture.step()['progress']['phase'])
        result=fixture.finish();self.assertEqual('COMPLETED',result['status'],result)
        with Database('oa') as db,db.transaction():
            store=WorkspaceClusteringStore(db,ws=fixture.workspace(db))
            event,_=store.current(fixture.report['id']);group=event['groups'][0]['id']
            self.assertFalse(event['confirmed'])
            store.change(OWNER,'block-a1',fixture.report['version_id'],fixture.report['id'],action='CONFIRM',
                         expected_revision=1,request_id=uuid4(),reason='합성 분류 확인')
        classification=fixture.requests[:];fixture.requests=[]
        review_job=fixture.enqueue(group_id=group,expected_cluster_revision=2)
        result=fixture.finish();self.assertEqual('COMPLETED',result['status'],result)
        for packets,required in ((classification,{'TEXT_LEAF','TEXT_MERGE','LEAF','MERGE'}),
                                 (fixture.requests,{'TEXT_LEAF','TEXT_MERGE','REVIEW_LEAF','REVIEW_MERGE'})):
            stages={packet['batch']['stage'] for packet in packets}
            self.assertTrue(required<=stages,(required,stages))
            fragments=[fragment for packet in packets for fragment in packet.get('fragments',[])]
            self.assertGreater(len(fragments),4)
            source=fixture.report['preserved_sources'][1]['id']
            selected=sorted((f for f in fragments if f['source_id']==source),
                            key=lambda f:(f['source_id'],f['byte_start'],f['byte_end'],f['text_start']))
            self.assertEqual(fixture.original,''.join(f['text'] for f in selected))
            offsets={}
            for fragment in selected:
                parent=fragment['parent_id'];expected=offsets.get(parent,0)
                self.assertEqual(expected,fragment['text_start'])
                self.assertEqual(fragment['text_stop']-fragment['text_start'],len(fragment['text']))
                offsets[parent]=fragment['text_stop']
            analyses=[p['context']['preserved_analysis'] for p in packets
                      if 'preserved_analysis' in p.get('context',{})]
            self.assertTrue(analyses)
            self.assertTrue(all(key.startswith('pe-') for analysis in analyses for key in analysis['evidence_ids']))
        with Database('oa') as db,db.transaction():
            service=AIJobService(db,fixture.workspace(db),fixture.advisor,fixture.criteria)
            for item in (job,review_job):
                event=service._load(item['id']);ledger=ParallelLedger(service,event)
                self.assertEqual('parallel-v1',event['data']['batch']['execution'])
                self.assertEqual('COMPLETED',event['status']);self.assertIsNotNone(ledger.audit())
                final=ledger.get('final')[0]
                _,context=service._context(event['data']['command'],write=False)
                self.assertEqual(digest(context['preserved_evidence']),final['metadata']['preserved_input_sha256'])
                self.assertTrue(final['metadata']['preserved_citation_sources'])
                self.assertTrue(final['metadata']['preserved_citations'])
        self.assertEqual(fixture.report,fixture.c.collection_report());self.assertIsNone(fixture.step())
