"""Synthetic no-cache transport, immutable job pins and retry boundaries."""
import hashlib
import json


from tests.support import PgTestCase
from tests.test_vwp_inference import PERSONAL_TOKEN, configuration


class VwpCacheJobTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.f=VwpWorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c=self.f.c;self.admin,self.schema=self.c.admin,self.c.schema

    def cached_connection(self,**changes):
        self.f.connection=self.f.v.create(configuration(cache={'no-cache':True},**changes))
        return self.f.connection

    def test_vwp_job_sends_cache_and_persists_exact_wire_hash(self):
        self.cached_connection();job=self.f.request();self.f.prepare()
        result=self.f.v.finish();self.assertEqual('COMPLETED',result['status'],result)
        request=self.f.v.wire[0]
        self.assertEqual({'no-cache':True},json.loads(request.content)['cache'])
        self.assertEqual('Bearer '+PERSONAL_TOKEN,request.headers['authorization'])
        with self.c.service() as service:
            metadata=service._load(job['id'])['data']['result']['metadata']
            self.assertEqual(hashlib.sha256(request.content).hexdigest(),metadata['input_sha256'])
