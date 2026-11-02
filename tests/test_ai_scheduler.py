"""Global DB slots, FIFO leases, and separate classification/judgment calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError
from common.sanity_db import Database,json_value
from oa.sanity_ai_jobs import _db_now
from oa.sanity_ai_scheduler import Settings,Scheduler,limits,executing_job,allocation,reserve,release
from tests.support import PgTestCase


class SchedulerContractTests(unittest.TestCase):
    def test_global_limits_accept_more_than_eight_and_reject_invalid_or_extra_fields(self):
        self.assertEqual({'max_concurrency':64,'max_jobs':12},limits({'max_concurrency':64,'max_jobs':12}))
        for bad in ({'max_concurrency':0,'max_jobs':2},{'max_concurrency':True,'max_jobs':2},
                    {'max_concurrency':2,'max_jobs':-1},{'max_concurrency':2,'max_jobs':2,'extra':1},
                    {'max_concurrency':'2','max_jobs':2},{'max_concurrency':1000001,'max_jobs':2}):
            with self.subTest(bad=bad),self.assertRaises(ReviewError):limits(bad)


class SchedulerApiTests(unittest.TestCase):
    def test_scheduler_api_requires_mutation_protection_and_passes_only_server_actor(self):
        from tests.test_ai_connections import ConnectionApiTests
        f=ConnectionApiTests();f.setUp();self.addCleanup(f.doCleanups)
        value={'max_concurrency':64,'max_jobs':4};store=Mock()
        store.read.return_value={'revision':0,'limits':value};store.save.return_value={'revision':1,'limits':value}
        with patch('oa.sanity_ai_scheduler.Settings',return_value=store):
            path='/api/admin/ai/scheduler'
            self.assertEqual(200,f.client.get(path).status_code)
            self.assertIn(f.client.post(path,json={'expected_revision':0,'limits':value}).status_code,(401,403))
            for body in ({'expected_revision':True,'limits':value},
                         {'expected_revision':0,'limits':value,'user_id':'other'}):
                self.assertEqual(400,f.client.post(path,headers=f.headers,json=body).status_code)
            store.save.assert_not_called()
            self.assertEqual(200,f.client.post(path,headers=f.headers,json={'expected_revision':0,'limits':value}).status_code)
            store.save.assert_called_once_with(f.actor,value,0)


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

    def test_independent_workers_claim_fifo_and_share_one_call_budget(self):
        self.settings()
        first=self.c.enqueue();self.another_report();second=self.c.enqueue()
        self.assertEqual(first['id'],self.a.claim())
        self.assertEqual(second['id'],self.b.claim())
        self.assertIsNone(self.a.claim())
        tokens=[str(uuid4()) for _ in range(3)]
        for scheduler,job,token in ((self.a,first,tokens[0]),(self.b,second,tokens[1])):
            with executing_job(job['id'],scheduler.owner),self.c.service() as service:
                self.assertEqual(1,allocation(service,job['id']))
                deadline=(_db_now(service.db)+timedelta(seconds=600)).isoformat()
                self.assertTrue(reserve(service,job['id'],token,deadline))
                self.assertFalse(reserve(service,job['id'],tokens[2],deadline))
        self.assertEqual(2,len(self.runtime()['calls']))
        with executing_job(first['id'],self.a.owner),self.c.service() as service:release(service,first['id'],tokens[0])
        self.a.finish(first['id'])
        with executing_job(second['id'],self.b.owner),self.c.service() as service:
            self.assertEqual(2,allocation(service,second['id']))
            self.assertTrue(reserve(service,second['id'],tokens[2],(_db_now(service.db)+timedelta(seconds=600)).isoformat()))
        self.assertEqual(2,len(self.runtime()['calls']))

    def test_expired_claim_fences_old_dispatch_and_old_owner_cannot_release_new_claim(self):
        self.settings();job=self.c.enqueue();self.assertEqual(job['id'],self.a.claim())
        state=self.runtime();state['jobs'][job['id']]['until']='2000-01-01T00:00:00+00:00'
        self.c.admin_query("UPDATE {oa} SET state=%s WHERE workspace_key='ai.scheduler.runtime'",(json_value(state),))
        self.assertEqual(job['id'],self.b.claim());self.a.finish(job['id'])
        self.assertEqual(self.b.owner,self.runtime()['jobs'][job['id']]['owner'])
        with executing_job(job['id'],self.a.owner),self.c.service() as service:
            self.assertEqual(0,allocation(service,job['id']))
            self.assertFalse(reserve(service,job['id'],str(uuid4()),(_db_now(service.db)+timedelta(seconds=60)).isoformat()))
        self.assertEqual([],self.c.requests)

    def test_revision_checked_settings_do_not_repin_existing_jobs(self):
        self.settings(calls=16,jobs=4)
        with self.c.service() as service:
            self.assertEqual({'max_concurrency':16,'max_jobs':4},Settings(service.db).current()['limits'])
        self.c.assert_code('REVISION_CONFLICT',lambda:self.settings(calls=3,jobs=2))
        self.assertEqual(2,self.settings(calls=3,jobs=2,revision=1)['revision'])

    def test_judgment_model_finishes_while_classification_model_is_blocked(self):
        from oa.sanity_ai_model import OAAdvisor
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        self.c.advisor=OAAdvisor(replace(self.c.advisor.settings,models=('synthetic-model','synthetic-review')),
                                transport=httpx.MockTransport(self.c.respond))
        self.c.enqueue();self.assertEqual('COMPLETED',self.c.run_job()['status'])
        with self.c.service() as service:
            WorkspaceClusteringStore(service.db,ws=service.ws).change(*self.c.path,action='CONFIRM',
                expected_revision=1,request_id=uuid4(),reason='합성 확정 근거')
        self.settings()
        cluster=self.c.enqueue(expected_revision=2)
        review=self.c.enqueue(group_id='g1',expected_cluster_revision=2,model_id='synthetic-review')
        self.assertEqual(cluster['id'],self.a.claim());self.assertEqual(review['id'],self.b.claim())
        entered=Event();unblock=Event();models=[]
        def transport(request):
            body=json.loads(request.content);models.append(body['model'])
            if body['model']=='synthetic-model':
                entered.set()
                if not unblock.wait(20):raise AssertionError('Synthetic classifier was not released')
                return None
            packet=json.loads(body['messages'][1]['content']);records=packet['context']['group_evidence']
            advice={'schema_version':2,'conclusion':'FAIL','reason':'현재 근거의 동기화기 누락을 확인했습니다.',
                    'evidence_ids':[records[0]['id']],'reference_ids':[],'criterion_ids':['C1']}
            return httpx.Response(200,json={'model':'synthetic-review','choices':[{'finish_reason':'stop',
                'message':{'role':'assistant','content':json.dumps(advice,ensure_ascii=False)}}]})
        self.c.callback=transport
        def process(job,scheduler):
            with executing_job(job['id'],scheduler.owner):return self.c.run_job(job_id=job['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(process,cluster,self.a)
            try:
                self.assertTrue(entered.wait(10))
                second=pool.submit(process,review,self.b)
                result=second.result(timeout=10)
                self.assertEqual(('COMPLETED','synthetic-review'),(result['status'],result['model']))
                self.assertFalse(first.done())
                self.assertEqual({'synthetic-model','synthetic-review'},set(models))
                self.assertEqual(1,len(self.runtime()['calls']))
            finally:unblock.set()
            self.assertEqual('COMPLETED',first.result(timeout=10)['status'])
        self.assertEqual({},self.runtime()['calls'])

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
