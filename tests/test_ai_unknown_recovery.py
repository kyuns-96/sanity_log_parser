"""Synthetic-only tests for acknowledged recovery of an UNKNOWN batch."""
import json
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError
from oa.sanity_ai_budget import BatchSettings

from tests.support import PgTestCase


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
