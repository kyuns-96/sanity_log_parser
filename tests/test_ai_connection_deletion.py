"""Synthetic connection deletion, revision history and in-flight job boundaries."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError
from oa.sanity_ai_connections import PREFIX
from tests.support import PgTestCase
from tests.test_ai_connections import configuration


class ConnectionDeletionDatabaseTests(PgTestCase):
    def setUp(self):
        from tests.test_ai_connections import ConnectionDatabaseTests
        self.f=ConnectionDatabaseTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c=self.f.c;self.admin,self.schema=self.c.admin,self.c.schema

    def history(self,connection):
        return self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s ORDER BY workspace_key",
                                  (PREFIX+connection+'.%',)).fetchall()

    def delete(self,connection,revision=1):
        with self.f.store() as (store,actor,_):return store.delete(actor,connection,revision)

    def test_deletion_creates_one_tombstone_and_preserves_history_and_personal_tokens(self):
        connection=self.f.create();self.f.token(connection)
        before=self.history(connection)
        tokens=self.c.admin_query("SELECT workspace_key,payload FROM {oa} WHERE payload->>'namespace'='oa-ai-user-token-v2' ORDER BY workspace_key").fetchall()
        result=self.delete(connection)
        self.assertEqual({'id':connection,'revision':2,'deleted':True},result)
        after=self.history(connection)
        self.assertEqual(2,len(after));self.assertEqual(before[0],after[0])
        tombstone=after[-1][0]
        self.assertTrue(tombstone['deleted']);self.assertFalse(tombstone['config']['enabled'])
        self.assertIsNone(tombstone['client_key_ciphertext'])
        self.assertEqual(tokens,self.c.admin_query("SELECT workspace_key,payload FROM {oa} WHERE payload->>'namespace'='oa-ai-user-token-v2' ORDER BY workspace_key").fetchall())
        with self.f.store() as (store,actor,_):
            self.assertEqual([],store.rows());self.assertEqual([],store.user_connections('owner',include_disabled=True))
            for enabled in (True,False):
                with self.assertRaises(ReviewError):store.get(connection,enabled=enabled)
            self.assertEqual(result,store.delete(actor,connection,1))
            for revision in (0,2):
                with self.assertRaises(ReviewError):store.save(actor,connection,configuration(),revision)
        self.assertEqual(after,self.history(connection))

    def test_revision_conflicts_bounds_and_current_sso_admin_are_enforced(self):
        connection=self.f.create();self.f.edit(connection,label='합성 수정')
        with self.f.store() as (store,actor,_):
            for revision in (0,True,-1,1.5,'2',None,9999999999):
                with self.subTest(revision=revision),self.assertRaises(ReviewError):store.delete(actor,connection,revision)
            with self.assertRaises(ReviewError) as stale:store.delete(actor,connection,1)
            self.assertEqual('REVISION_CONFLICT',stale.exception.code)
            for denied in (SimpleNamespace(user_id='other',auth_method='sso',access=actor.access),
                           SimpleNamespace(user_id='owner',auth_method='local_token',access=actor.access),
                           SimpleNamespace(user_id='owner',auth_method='sso',access=SimpleNamespace(db=object(),_admin=lambda _:None))):
                with self.assertRaises(ReviewError):store.delete(denied,connection,2)
        self.assertEqual(2,len(self.history(connection)))
        self.assertEqual(3,self.delete(connection,2)['revision'])
        with self.f.store() as (store,actor,_),self.assertRaises(ReviewError) as stale:
            store.delete(actor,connection,1)
        self.assertEqual('REVISION_CONFLICT',stale.exception.code)

    def test_gateway_deletion_needs_neither_cipher_nor_shared_key_decryption(self):
        cfg=configuration(auth_mode='bearer_x_client',client_name='synthetic-app',client_version='1',session_mode='per_request')
        connection=self.f.create(cfg,'synthetic-client-key')
        with self.f.store() as (store,actor,_):
            store.settings={}
            with patch.object(store,'_cipher',side_effect=AssertionError('deletion attempted encryption')), \
                 patch.object(store,'_client_key',side_effect=AssertionError('deletion decrypted key')):
                self.assertEqual(2,store.delete(actor,connection,1)['revision'])
        self.assertIsNone(self.history(connection)[-1][0]['client_key_ciphertext'])

    def test_deleted_connection_returns_catalog_slot_but_id_remains_reserved(self):
        ids=[]
        with self.f.store() as (store,actor,_):
            for _ in range(100):
                connection=str(uuid4());store.save(actor,connection,configuration(),0);ids.append(connection)
            with self.assertRaises(ReviewError) as limit:store.save(actor,str(uuid4()),configuration(),0)
            self.assertEqual('AI_CONNECTION_LIMIT',limit.exception.code)
            store.delete(actor,ids[0],1)
            new=str(uuid4());self.assertEqual(1,store.save(actor,new,configuration(),0)['revision'])
            self.assertEqual(100,len(store.rows()))
            with self.assertRaises(ReviewError):store.save(actor,ids[0],configuration(),0)
            for old in ids[1:]:store.delete(actor,old,1)
            store.delete(actor,new,1)
            self.assertEqual([],store.rows())
            self.assertEqual(1,store.save(actor,str(uuid4()),configuration(),0)['revision'])

    def test_queued_job_stales_without_model_call_after_deletion(self):
        connection=self.f.create();self.f.token(connection)
        job=self.c.enqueue(connection_id=connection);self.delete(connection)
        result=self.c.run_job()
        self.assertEqual('STALE',result['status'],result);self.assertEqual([],self.f.wire)
        with self.c.service() as service:self.assertEqual('STALE',service._load(job['id'])['status'])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_deletion_during_http_rejects_returned_result_and_publication(self):
        connection=self.f.create();self.f.token(connection);self.c.enqueue(connection_id=connection)
        self.c.callback=lambda _:self.delete(connection) and None
        result=self.c.run_job()
        self.assertEqual('STALE',result['status'],result);self.assertEqual(1,len(self.f.wire))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_completed_job_and_published_decision_history_are_preserved(self):
        connection=self.f.create();self.f.token(connection)
        job=self.c.enqueue(connection_id=connection)
        self.assertEqual('COMPLETED',self.c.run_job()['status'])
        def records():
            return self.c.admin_query("SELECT record_type,workspace_key,payload FROM {oa} WHERE "
                "record_type='WS_CLUSTER_EVENT' OR workspace_key LIKE %s ORDER BY record_type,workspace_key",
                ('ai1.job.'+job['id']+'.%',)).fetchall()
        before=records();self.assertTrue(any(row[0]=='WS_CLUSTER_EVENT' for row in before))
        self.delete(connection)
        self.assertEqual(before,records())
        with self.c.service() as service:self.assertEqual('COMPLETED',service._load(job['id'])['status'])

    def test_deletion_during_ambiguous_batch_call_remains_unknown(self):
        from tests.test_ai_batches import batch_settings
        connection=self.f.create(configuration(batch=batch_settings(max_records=1).public()))
        self.f.token(connection)
        with executing_on('vwp'):self.c.enqueue(connection_id=connection,_worker_site='vwp')
        def timeout(request):
            self.delete(connection)
            raise httpx.ReadTimeout('synthetic unknown',request=request)
        self.c.callback=timeout
        with executing_on('vwp'):result=self.c.run_job()
        self.assertEqual('UNKNOWN',result['status'],result);self.assertEqual(1,len(self.f.wire))
        with executing_on('vwp'):self.assertIsNone(self.c.run_job())
        self.assertEqual(1,len(self.f.wire))
        info=result['unknown_recovery']
        with self.c.service() as service:
            with self.assertRaises(ReviewError):
                service.retry_unknown('owner',result['id'],uuid4(),info['event_sha256'],info['step_sha256'],True)
            self.assertEqual('UNKNOWN',service._load(result['id'])['status'])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_deletion_during_parallel_calls_blocks_all_late_results(self):
        from dataclasses import replace
        from tests.test_ai_batches import batch_settings
        settings=replace(batch_settings(max_records=1),max_concurrency=2)
        connection=self.f.create(configuration(batch=settings.public()))
        self.f.token(connection);self.c.enqueue(connection_id=connection)
        count=[0];started=Event();release=Event()
        from threading import Lock
        lock=Lock()
        def block(_request):
            with lock:
                count[0]+=1
                if count[0]==2:started.set()
            if not release.wait(20):raise AssertionError('synthetic calls were not released')
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.c.run_job)
            try:
                self.assertTrue(started.wait(10));self.delete(connection)
            finally:release.set()
            result=future.result(timeout=10)
        self.assertEqual('STALE',result['status'],result)
        self.assertEqual(2,len(self.f.wire))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def assert_unavailable_resume_display(self, *, failed, deleted):
        from oa.sanity_ai_jobs import _now
        from oa.sanity_ai_monitor import Monitor
        from tests.test_ai_batches import batch_settings
        connection=self.f.create(configuration(batch=batch_settings(max_records=1).public()))
        self.f.token(connection)
        with executing_on('vwp'):
            job=self.c.enqueue(connection_id=connection,_worker_site='vwp')
            self.assertEqual(1,self.c.run_job()['progress']['completed_calls'])
        if failed:
            def refused(request):raise httpx.ConnectError('synthetic connection failure',request=request)
            self.c.callback=refused
            start=_now()
            for n in range(6):
                at=start+timedelta(seconds=400*n)
                with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):result=self.c.run_job(now=at)
            self.assertEqual('FAILED',result['status'],result)
        else:
            with self.c.service() as service:result=service.cancel('owner',job['id'])
            self.assertEqual('CANCELLED',result['status'])
        self.assertTrue(result['resumable'])
        if deleted:self.delete(connection)
        else:self.f.edit(connection,enabled=False)
        def snapshots():
            return self.c.admin_query("SELECT workspace_key,payload FROM {oa} WHERE workspace_key LIKE %s ORDER BY workspace_key",
                                      ('ai.live.'+job['id']+'.%',)).fetchall()
        before=snapshots();count=len(self.f.wire)
        with patch('oa.sanity_ai_jobs.AIJobService._context',side_effect=AssertionError('source hydrated')), \
             patch('oa.sanity_ai_jobs.CaseRetriever',side_effect=AssertionError('cases hydrated')), \
             patch('oa.sanity_ai_jobs.AIJobService._job_advisor',side_effect=AssertionError('model loaded')), \
             patch('oa.sanity_ai_connections.ConnectionStore._cipher',side_effect=AssertionError('cipher loaded')):
            with self.c.service() as service:
                with patch.object(service.connections,'get',wraps=service.connections.get) as reads:
                    status=service.status('owner',job['id'])
                    item=next(row for row in Monitor(service).items('owner')['items'] if row['id']==job['id'])
                    capability=service.capability(*self.c.path)['job']
                    self.assertGreaterEqual(reads.call_count,3)
                    for call in reads.call_args_list:self.assertEqual(connection,call.args[0])
                for view in (status,item,capability):
                    self.assertFalse(view['resumable'])
                    self.assertEqual('AI_CONNECTION_UNAVAILABLE',view['resume_error'])
                self.assertFalse(item['can_resume'])
                with patch.object(service.connections,'get',side_effect=ReviewError('AI_CONNECTION_CORRUPT','Synthetic corrupt connection')):
                    for read in (lambda:service.status('owner',job['id']),
                                 lambda:Monitor(service).items('owner'),lambda:service.capability(*self.c.path)):
                        with self.assertRaises(ReviewError) as caught:read()
                        self.assertEqual('AI_CONNECTION_CORRUPT',caught.exception.code)
                self.assertEqual(result['status'],service._load(job['id'])['status'])
        self.assertEqual(before,snapshots());self.assertEqual(count,len(self.f.wire))

    def test_deleted_connection_hides_cancelled_job_resume(self):
        self.assert_unavailable_resume_display(failed=False,deleted=True)

    def test_disabled_connection_hides_failed_job_resume(self):
        self.assert_unavailable_resume_display(failed=True,deleted=False)


class VwpConnectionDeletionTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.f=VwpWorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c=self.f.c;self.admin,self.schema=self.c.admin,self.c.schema

    def test_offline_profile_and_missing_cipher_do_not_block_deletion(self):
        connection=self.f.connection
        with self.f.v.store() as (store,actor,_):
            store.settings={}
            with patch('oa.sanity_vwp_inference.Catalog.get',side_effect=AssertionError('offline catalog accessed')), \
                 patch.object(store,'_cipher',side_effect=AssertionError('cipher accessed')):
                self.assertEqual({'id':connection,'revision':2,'deleted':True},store.delete(actor,connection,1))
            self.assertEqual([],store.user_connections('owner',include_disabled=True))

    def test_vwp_queued_job_never_dispatches_after_deletion(self):
        self.f.request()
        with self.f.v.store() as (store,actor,_):store.delete(actor,self.f.connection,1)
        result=self.f.prepare()
        self.assertEqual('STALE',result['status'],result);self.assertEqual([],self.f.v.wire)


class ConnectionDeletionApiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_connections import ConnectionApiTests
        self.f=ConnectionApiTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.path='/api/admin/ai/connections/'+self.f.connection+'/delete'

    def test_admin_csrf_and_strict_body(self):
        self.f.actor.user_id='admin'
        for body in ({},{'expected_revision':0},{'expected_revision':True},{'expected_revision':'1'},
                     {'expected_revision':1,'user_id':'other'}):
            with self.subTest(body=body):self.assertEqual(400,self.f.client.post(self.path,headers=self.f.headers,json=body).status_code)
        self.assertEqual(403,self.f.client.post(self.path,json={'expected_revision':1}).status_code)
        self.f.store.delete.assert_not_called()
        expected={'id':self.f.connection,'revision':2,'deleted':True}
        self.f.store.delete.return_value=expected
        response=self.f.client.post(self.path,headers=self.f.headers,json={'expected_revision':1})
        self.assertEqual(200,response.status_code);self.assertEqual(expected,response.json())
        self.f.store.delete.assert_called_once_with(self.f.actor,self.f.connection,1)

    def test_non_admin_and_non_sso_cannot_delete(self):
        self.assertEqual(403,self.f.client.post(self.path,headers=self.f.headers,json={'expected_revision':1}).status_code)
        self.f.actor.user_id='admin';self.f.actor.auth_method='local_token'
        self.assertEqual(403,self.f.client.post(self.path,headers=self.f.headers,json={'expected_revision':1}).status_code)
        self.f.store.delete.assert_not_called()
