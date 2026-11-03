"""Synthetic parallel batch jobs: bounded dispatch, durable results and recovery."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event, Lock
import time
from unittest.mock import patch


from common.sanity_ai_execution import executing_on, worker_site
from common.sanity_db import json_value
from oa.sanity_ai_budget import BatchSettings

from tests.support import PgTestCase


class ParallelVwpJobsTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w=VwpWorkerTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.c=self.w.c;self.v=self.w.v;self.admin,self.schema=self.c.admin,self.c.schema

    def create_job(self, *, width=2, records=2, max_calls=20000, merge_groups=20, execution='parallel-v1'):
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
        # Preserve the original wave contracts as compatibility tests. Rolling
        # tests explicitly select the current production execution version.
        with patch('oa.sanity_ai_jobs.PARALLEL_EXECUTION',execution):
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
