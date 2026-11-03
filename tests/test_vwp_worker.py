"""Synthetic VWP placement, lightweight web requests and recovery boundaries."""
from uuid import uuid4


from common.sanity_ai_execution import executing_on
from tests.support import PgTestCase


class VwpWorkerTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_inference import VwpInferenceDatabaseTests
        self.v=VwpInferenceDatabaseTests();self.v.setUp();self.addCleanup(self.v.doCleanups)
        self.c=self.v.c;self.admin,self.schema=self.c.admin,self.c.schema
        self.connection=self.v.create()

    def request(self,identity=None,**changes):
        with self.c.service() as service:
            return service.request(*self.c.path,expected_revision=changes.pop("expected_revision",0),
                request_id=identity or uuid4(),connection_id=self.connection,**changes)

    def prepare(self):
        with executing_on("vwp"):return self.c.run_job()
