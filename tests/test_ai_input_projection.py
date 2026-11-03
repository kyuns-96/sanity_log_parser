"""Synthetic item-specific AI input selection; source evidence stays unchanged."""


from tests.support import PgTestCase


class ProjectionJobTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w=VwpWorkerTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.c=self.w.c;self.v=self.w.v;self.admin,self.schema=self.c.admin,self.c.schema

    def test_policy_is_pinned_and_change_stops_batch_without_another_http_call(self):
        from common.sanity_db import json_value
        from oa.sanity_ai_budget import BatchSettings
        from tests.test_vwp_inference import configuration
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
            overhead_tokens=1024,max_records=1,output_parameter='max_tokens')
        self.w.connection=self.v.create(configuration(batch=settings.public()))
        report=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='report'").fetchone()[0]
        report.update(ai_fields=['object'],include_raw=False,include_details=False)
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key='report'",(json_value(report),))
        job=self.w.request();self.w.prepare();first=self.w.prepare()
        self.assertEqual(1,first['progress']['completed_calls'])
        with self.c.service() as service:
            pinned=service._load(job['id'])['data']['pins']['source']
            self.assertIn('ai_input_policy_sha256',pinned)
        stored=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='report'").fetchone()[0]
        self.assertEqual(report,stored)
        report['ai_fields']=['mode']
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key='report'",(json_value(report),))
        result=self.w.prepare()
        self.assertEqual('STALE',result['status'],result)
        self.assertEqual(1,len(self.v.wire))
