"""Synthetic-only tests for acknowledged recovery of an UNKNOWN batch."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError, digest
from common.sanity_db import json_value
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_jobs import _now
from tests.support import PgTestCase


class UnknownRecoveryApiTests(unittest.TestCase):
    def test_explicit_true_and_mutation_guards_are_required(self):
        from tests.test_ai_workbench_api import ProductionWorkbenchApiTests, HEADERS, TARGET
        fixture=ProductionWorkbenchApiTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        job_id,request_id=uuid4(),uuid4()
        path=f'/api/workbench/ai/jobs/{job_id}/retry-unknown'
        body={'request_id':str(request_id),'expected_event_sha256':'a'*64,'expected_step_sha256':'b'*64,'acknowledge_duplicate':True}
        fixture.service.retry_unknown.return_value={'id':str(job_id),'status':'RUNNING'}
        self.assertEqual(403,fixture.client.post(path,json=body).status_code)
        for changes in ({'acknowledge_duplicate':False},{'acknowledge_duplicate':'true'},{'user_id':'other'},
                        {'expected_event_sha256':'invalid'}):
            self.assertEqual(400,fixture.client.post(path,headers=HEADERS,json={**body,**changes}).status_code)
        missing=dict(body);del missing['acknowledge_duplicate']
        self.assertEqual(400,fixture.client.post(path,headers=HEADERS,json=missing).status_code)
        fixture.service.retry_unknown.assert_not_called()
        response=fixture.client.post(path,headers=HEADERS,json=body)
        self.assertEqual(202,response.status_code,response.text)
        fixture.service.retry_unknown.assert_called_once_with(TARGET[0],str(job_id),request_id,'a'*64,'b'*64,True)


class UnknownRecoveryBatchTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w=VwpWorkerTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.c=self.w.c;self.v=self.w.v;self.admin,self.schema=self.c.admin,self.c.schema

    def prepare_batch(self, *, max_calls=20000):
        from tests.test_vwp_inference import configuration
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
            overhead_tokens=1024,max_records=1,output_parameter='max_tokens',max_calls=max_calls)
        self.w.connection=self.v.create(configuration(batch=settings.public()))
        job=self.w.request();self.w.prepare()
        first=self.w.prepare();self.assertEqual(1,first['progress']['completed_calls'])
        return job

    def unknown(self):
        def timeout(request):raise httpx.ReadTimeout('synthetic timeout',request=request)
        self.c.callback=timeout
        result=self.w.prepare()
        self.assertEqual('UNKNOWN',result['status'],result)
        info=result['unknown_recovery']
        self.assertEqual({'event_sha256','step_sha256','call_token','phase','round','index','total','input_bytes'},set(info))
        return result

    def recover(self,job,unknown,request_id=None,*,actor='owner',ack=True,event_hash=None,step_hash=None):
        info=unknown['unknown_recovery']
        with self.c.service() as service:
            return service.retry_unknown(actor,job['id'],request_id or uuid4(),
                event_hash or info['event_sha256'],step_hash or info['step_sha256'],ack)

    def assert_display_unavailable(self,job,expected_codes):
        from oa.sanity_ai_monitor import Monitor
        with patch('oa.sanity_ai_jobs.AIJobService._context',side_effect=AssertionError('display hydrated source')), \
             patch('oa.sanity_ai_jobs.CaseRetriever',side_effect=AssertionError('display retrieved cases')):
            with self.c.service() as service:
                card=next(item for item in Monitor(service).items('owner')['items'] if item['id']==job['id'])
                capability=service.capability(*self.c.path)['job']
                status=service.status('owner',job['id'])
                for value in (card,capability,status):
                    self.assertEqual('UNKNOWN',value['status'])
                    self.assertFalse(value['can_retry_unknown'])
                    self.assertIn(value['unknown_recovery_error'],expected_codes)
                self.assertEqual(card['unknown_recovery_error'],capability['unknown_recovery_error'])
                self.assertEqual(card['unknown_recovery_error'],status['unknown_recovery_error'])

    def test_acknowledged_retry_keeps_finished_child_and_is_idempotent(self):
        job=self.prepare_batch();unknown=self.unknown()
        self.assertEqual(2,len(self.v.wire))
        self.assertEqual(1,unknown['progress']['completed_calls'])
        rendered=json.dumps(unknown['unknown_recovery'])
        for secret in ('synthetic-vwp-personal-credential',self.v.private['endpoint'],'클록 동기화기 누락'):
            self.assertNotIn(secret,rendered)
        request_id=uuid4();first=self.recover(job,unknown,request_id)
        duplicate=self.recover(job,unknown,request_id)
        self.assertEqual(first,duplicate)
        self.assertEqual(('RUNNING',1),(first['status'],first['progress']['completed_calls']))
        with self.assertRaises(ReviewError) as error:self.recover(job,unknown,request_id,step_hash='f'*64)
        self.assertEqual('REQUEST_CONFLICT',error.exception.code)
        self.c.callback=None;finished=self.v.finish()
        self.assertEqual('COMPLETED',finished['status'],finished)
        self.assertEqual(4,len(self.v.wire))
        self.assertEqual(self.v.wire[1].content,self.v.wire[2].content)
        self.assertNotEqual(self.v.wire[0].content,self.v.wire[2].content)
        with self.c.service() as service:
            self.assertEqual('COMPLETED',service.status('owner',job['id'])['status'])
            self.assertEqual('COMPLETED',service.retry_unknown('owner',job['id'],request_id,
                unknown['unknown_recovery']['event_sha256'],unknown['unknown_recovery']['step_sha256'],True)['status'])

    def test_missing_ack_wrong_actor_and_changed_hashes_cannot_recover(self):
        job=self.prepare_batch();unknown=self.unknown()
        for kwargs,code in (({'ack':False},'INVALID_REQUEST'),({'actor':'other'},'FORBIDDEN'),
                            ({'event_hash':'f'*64},'REVISION_CONFLICT'),({'step_hash':'f'*64},'REVISION_CONFLICT')):
            with self.subTest(kwargs=kwargs),self.assertRaises(ReviewError) as error:self.recover(job,unknown,**kwargs)
            self.assertEqual(code,error.exception.code)
        self.assertEqual(2,len(self.v.wire))
        with self.c.service() as service:self.assertEqual('UNKNOWN',service.status('owner',job['id'])['status'])

    def test_old_unknown_snapshot_projects_recovery_without_hydrating_or_writing(self):
        from oa.sanity_ai_monitor import Monitor
        from oa.sanity_workspace import WorkspaceStore
        job=self.prepare_batch();unknown=self.unknown();count=len(self.v.wire)
        key,snapshot=self.c.admin_query("SELECT workspace_key,payload FROM {oa} WHERE record_type='WS_CONFIG' "
            "AND workspace_key LIKE %s ORDER BY workspace_key DESC LIMIT 1",('ai.live.'+job['id']+'.%',)).fetchone()
        self.assertEqual('UNKNOWN',snapshot['job']['status'])
        snapshot['job'].pop('unknown_recovery',None)
        snapshot['sha256']=digest({k:v for k,v in snapshot.items() if k!='sha256'})
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_CONFIG' AND workspace_key=%s",(json_value(snapshot),key))
        snapshot_count=self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                                          ('ai.live.'+job['id']+'.%',)).fetchone()[0]
        with self.c.service() as service:
            WorkspaceStore(service.db).put('WS_USER','other',{'id':'other','name':'합성 사용자','username':'other','role':'OWNER'},state={'active':True})
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{owners}}',%s) WHERE record_type='WS_BLOCK'",
                           (json_value(['owner','other']),))
        with patch('oa.sanity_ai_jobs.AIJobService._context',side_effect=AssertionError('display hydrated source')), \
             patch('oa.sanity_ai_jobs.CaseRetriever',side_effect=AssertionError('display retrieved cases')):
            with self.c.service() as service:
                owner=Monitor(service).items('owner')['items']
                shown=next(item for item in owner if item['id']==job['id'])
                self.assertEqual(unknown['unknown_recovery'],shown['unknown_recovery'])
                self.assertTrue(shown['can_retry_unknown'])
                capability=service.capability(*self.c.path)['job']
                self.assertEqual(unknown['unknown_recovery'],capability['unknown_recovery'])
                self.assertTrue(capability['can_retry_unknown'])
                legacy=Monitor(service).legacy(service._target('report',None))['job']
                self.assertEqual(unknown['unknown_recovery'],legacy['unknown_recovery'])
                self.assertEqual([],Monitor(service).items('other')['items'])
                other=service.capability('other',*self.c.path[1:])['job']
                self.assertFalse(other.get('can_retry_unknown',False))
        stored=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",(key,)).fetchone()[0]
        self.assertEqual(snapshot,stored)
        self.assertEqual(snapshot_count,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                                                          ('ai.live.'+job['id']+'.%',)).fetchone()[0])
        self.assertEqual(count,len(self.v.wire))

        # The projection cannot turn a corrupt authoritative job into an actionable hint.
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{sha256}}',%s) WHERE record_type='WS_CONFIG' "
            "AND workspace_key=%s",(json_value('0'*64),'ai1.job.'+job['id']+'.0003'))
        with self.c.service() as service:
            try:items=Monitor(service).items('owner')['items']
            except ReviewError as error:self.assertIn(error.code,('AI_JOB_CORRUPT','AI_BATCH_CORRUPT'))
            else:
                projected=next((item for item in items if item['id']==job['id']),{})
                self.assertFalse(projected.get('can_retry_unknown',False))
        self.assertEqual(count,len(self.v.wire))

    def test_single_call_unknown_cannot_use_the_batch_recovery_endpoint(self):
        job=self.w.request();self.w.prepare()
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ReadTimeout('synthetic',request=request))
        result=self.w.prepare();self.assertEqual('UNKNOWN',result['status'])
        self.assertNotIn('unknown_recovery',result)
        with self.c.service() as service,self.assertRaises(ReviewError) as error:
            service.retry_unknown('owner',job['id'],uuid4(),'a'*64,'b'*64,True)
        self.assertEqual('AI_RESUME_UNAVAILABLE',error.exception.code)
        self.assertEqual(1,len(self.v.wire))

    def test_exhausted_call_budget_cannot_recover(self):
        job=self.prepare_batch(max_calls=2);unknown=self.unknown()
        self.assert_display_unavailable(job,('AI_BATCH_LIMIT',))
        with self.assertRaises(ReviewError) as error:self.recover(job,unknown)
        self.assertEqual('AI_BATCH_LIMIT',error.exception.code)
        self.assertEqual(2,len(self.v.wire))

    def test_personal_key_rotation_blocks_recovery(self):
        job=self.prepare_batch();unknown=self.unknown()
        with self.v.store() as (store,_,_):
            store.tokens(store.get(self.w.connection)).save('owner','synthetic-rotated-key',1)
        self.assert_display_unavailable(job,('AI_CREDENTIAL_CHANGED',))
        with self.assertRaises(ReviewError) as error:self.recover(job,unknown)
        self.assertEqual('AI_CREDENTIAL_CHANGED',error.exception.code)
        self.assertEqual(2,len(self.v.wire))

    def test_profile_change_blocks_recovery(self):
        job=self.prepare_batch();unknown=self.unknown()
        self.v.private={**self.v.private,'fallback_endpoint':'https://changed.example.test/v1/chat/completions'}
        self.v.publish()
        self.assert_display_unavailable(job,('AI_CREDENTIAL_CHANGED','AI_CONFIG_CHANGED'))
        with self.assertRaises(ReviewError) as error:self.recover(job,unknown)
        self.assertIn(error.exception.code,('AI_CREDENTIAL_CHANGED','AI_CONFIG_CHANGED'))
        self.assertEqual(2,len(self.v.wire))

    def test_revoked_write_access_blocks_the_original_requester(self):
        job=self.prepare_batch();unknown=self.unknown()
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{owners}}','[]'::jsonb) WHERE record_type='WS_BLOCK'")
        with self.assertRaises(ReviewError) as error:self.recover(job,unknown)
        self.assertIn(error.exception.code,('FORBIDDEN','UNAUTHORIZED','AI_INPUT_STALE'))
        self.assertEqual(2,len(self.v.wire))

    def test_changed_source_stops_recovered_job_before_another_call(self):
        job=self.prepare_batch();unknown=self.unknown()
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{rule}}',%s) WHERE record_type='WS_REPORT'",(json_value('synthetic changed'),))
        try:recovered=self.recover(job,unknown)
        except ReviewError as error:self.assertEqual('AI_INPUT_STALE',error.code)
        else:
            self.assertEqual('RUNNING',recovered['status'])
            self.c.callback=None;result=self.w.prepare()
            self.assertEqual('STALE',result['status'])
        self.assertEqual(2,len(self.v.wire))

    def test_explicit_unknown_recovery_shares_the_five_resume_limit(self):
        job=self.prepare_batch()
        for _ in range(5):
            unknown=self.unknown();self.assertEqual('RUNNING',self.recover(job,unknown)['status'])
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ReadTimeout('synthetic timeout',request=request))
        exhausted=self.w.prepare();self.assertEqual('UNKNOWN',exhausted['status'])
        self.assertNotIn('unknown_recovery',exhausted)
        with self.c.service() as service,self.assertRaises(ReviewError) as error:
            service.retry_unknown('owner',job['id'],uuid4(),'a'*64,'b'*64,True)
        self.assertEqual('AI_RESUME_UNAVAILABLE',error.exception.code)
        self.assertEqual(7,len(self.v.wire))

    def test_late_old_response_cannot_overwrite_the_recovered_child(self):
        job=self.prepare_batch();started=Event();release=Event()
        def block_first(_request):
            started.set()
            if not release.wait(20):raise AssertionError('synthetic call not released')
        self.c.callback=block_first
        def worker():
            with executing_on('vwp'):return self.c.run_job()
        at=_now()+timedelta(seconds=300)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending=executor.submit(worker)
            try:
                self.assertTrue(started.wait(10),'synthetic call did not start')
                with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):
                    unknown=self.c.run_job(now=at)
                    self.assertEqual('UNKNOWN',unknown['status'],unknown)
                    self.recover(job,unknown)
                    self.c.callback=None
                    new=self.c.run_job(now=at)
                    self.assertEqual(('RUNNING',2),(new['status'],new['progress']['completed_calls']))
                    release.set();late=pending.result(timeout=10)
                    self.assertEqual(('RUNNING',2),(late['status'],late['progress']['completed_calls']))
                    finished=self.v.finish()
                self.assertEqual('COMPLETED',finished['status'],finished)
                self.assertEqual(4,len(self.v.wire))
                self.assertEqual(self.v.wire[1].content,self.v.wire[2].content)
            finally:release.set()


class UnknownRecoveryReviewTests(unittest.TestCase):
    def test_group_review_v2_retries_only_unknown_child_and_audits_complete_result(self):
        from tests.test_ai_evidence_batches import EvidenceBatchDatabaseTests
        from oa.sanity_ai_batch_jobs import Ledger
        fixture=EvidenceBatchDatabaseTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        c=fixture.c;self.admin,self.schema=c.admin,c.schema
        with c.service() as service:
            job=service.request(*c.path,expected_revision=0,request_id=uuid4(),
                group_id='g1',expected_cluster_revision=2)
        with executing_on('vwp'):
            prepared=c.run_job();self.assertEqual('PENDING',prepared['status'])
            first=c.run_job()
            self.assertEqual(('REVIEW',1),(first['progress']['phase'],first['progress']['completed_records']))
            def timeout(_packet):raise httpx.ReadTimeout('synthetic review timeout')
            fixture.callback=timeout
            unknown=c.run_job();self.assertEqual('UNKNOWN',unknown['status'],unknown)
            self.assertEqual(1,unknown['progress']['completed_calls'])
            info=unknown['unknown_recovery'];self.assertEqual('REVIEW',info['phase'])
            self.assertEqual(0,c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_PROPOSAL'").fetchone()[0])
            with c.service() as service:
                resumed=service.retry_unknown('owner',job['id'],uuid4(),info['event_sha256'],info['step_sha256'],True)
            self.assertEqual(('RUNNING',1),(resumed['status'],resumed['progress']['completed_calls']))
            fixture.callback=None
            finished=fixture.finish()
        self.assertEqual('COMPLETED',finished['status'],finished)
        self.assertEqual(4,len(c.requests))  # first child, unknown child, same child, merge
        self.assertEqual(c.requests[1],c.requests[2])
        self.assertNotEqual(c.requests[0],c.requests[2])
        with c.service() as service:
            event=service._load(job['id']);self.assertEqual(2,event['data']['batch']['version'])
            ledger=Ledger(service,event);self.assertIsNotNone(ledger.audit())
            self.assertEqual(2,ledger.get('final')[0]['metadata']['batch_receipt']['records'])
        self.assertEqual(1,c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_PROPOSAL'").fetchone()[0])
        self.assertEqual(0,c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_EVENT'").fetchone()[0])
