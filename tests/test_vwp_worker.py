"""Synthetic VWP placement, lightweight web requests and recovery boundaries."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError
from common.sanity_db import json_value
from oa.sanity_ai_jobs import _db_now
from oa.sanity_ai_monitor import Monitor,heartbeat,worker_health
from oa.sanity_ai_submissions import Submissions
from oa.sanity_ai_budget import BatchSettings
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

    def test_web_submission_and_polling_do_not_hydrate_logs_or_retrieve_cases(self):
        identity=uuid4()
        with patch("oa.sanity_ai_jobs.AIJobService._context",side_effect=AssertionError("web hydrated input")), \
             patch("oa.sanity_ai_jobs.CaseRetriever",side_effect=AssertionError("web retrieved cases")):
            first=self.request(identity)
            self.assertEqual(first,self.request(identity))
            with self.c.service() as service:
                self.assertEqual(str(identity),service.capability(*self.c.path)["job"]["id"])
                self.assertEqual(first,service.status("owner",identity))
                cards=Monitor(service).items("owner")["items"]
                self.assertEqual(str(identity),cards[0]["id"])
                self.assertNotIn("raw",str(cards))
        self.assertEqual("QUEUED",first["preparation_phase"])
        self.assertEqual([],self.v.wire)
        self.assertIsNone(self.c.run_job())  # Physical OA cannot prepare it.
        prepared=self.prepare()
        self.assertEqual(("PENDING","vwp"),(prepared["status"],prepared["worker_site"]))
        self.assertEqual([],self.v.wire)
        self.assertEqual("COMPLETED",self.v.finish()["status"])
        self.assertEqual(1,len(self.v.wire))
        self.assertEqual("COMPLETED",self.request(identity)["status"])

    def test_source_changes_and_permission_revocation_before_preparation_stop_without_call(self):
        first=self.request()
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{rule}}',%s) WHERE record_type='WS_REPORT'",(json_value("synthetic-changed"),))
        result=self.prepare()
        self.assertEqual(("STALE","AI_INPUT_STALE"),(result["status"],result["error"]))
        self.assertEqual([],self.v.wire)
        with self.c.service() as service:
            self.assertEqual(first["id"],Monitor(service).items("owner")["items"][0]["id"])
        second=self.request()
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{owners}}','[]'::jsonb) WHERE record_type='WS_BLOCK'")
        result=self.prepare()
        self.assertIn(result["error"],("FORBIDDEN","UNAUTHORIZED"))
        self.assertEqual([],self.v.wire)
        with self.c.service() as service:
            self.assertEqual([],Monitor(service).items("owner")["items"])
            with self.assertRaises(ReviewError):service.cancel("owner",second["id"])

    def test_cancel_before_preparation_and_active_target_protection(self):
        first=self.request()
        with self.assertRaises(ReviewError) as caught:self.request()
        self.assertEqual("AI_JOB_ACTIVE",caught.exception.code)
        with self.c.service() as service:
            self.assertEqual("CANCELLED",service.cancel("owner",first["id"])["status"])
        self.assertIsNone(self.prepare());self.assertEqual([],self.v.wire)

    def test_crashed_preparation_is_bounded_and_never_claims_an_unexpired_lease(self):
        first=self.request()
        with self.c.service() as service:
            store=Submissions(service);event=store.load(first["id"])
            store.append(event,"PREPARING",{**event["data"],"attempts":1,"lease_until":(_db_now(service.db)+timedelta(seconds=300)).isoformat()})
        self.assertIsNone(self.prepare())
        with self.c.service() as service:
            store=Submissions(service);event=store.load(first["id"])
            store.append(event,"PREPARING",{**event["data"],"attempts":2,"lease_until":(_db_now(service.db)-timedelta(seconds=1)).isoformat()})
        result=self.prepare()
        self.assertEqual(("FAILED","AI_PREPARATION_LIMIT"),(result["status"],result["error"]))
        self.assertEqual([],self.v.wire)

    def test_vwp_prepared_job_is_skipped_by_oa_worker(self):
        self.request();self.prepare()
        self.assertIsNone(self.c.run_job())
        self.assertEqual([],self.v.wire)
        self.assertEqual("COMPLETED",self.v.finish()["status"])

    def test_only_proven_unsent_calls_retry_and_the_limit_is_persisted(self):
        job=self.request();self.prepare()
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ConnectError("synthetic",request=request))
        from oa.sanity_ai_jobs import _now
        for attempt in range(6):
            at=_now()+timedelta(seconds=400*attempt)
            with executing_on("vwp"),patch("oa.sanity_ai_jobs._db_now",return_value=at):result=self.c.run_job(now=at)
            self.assertEqual("FAILED" if attempt==5 else "PENDING",result["status"],result)
        self.assertEqual(6,len(self.v.wire))
        self.assertEqual(5,result["retry_count"])
        self.assertIsNone(self.prepare());self.assertEqual(6,len(self.v.wire))
        with self.c.service() as service:
            self.assertEqual("FAILED",service.status("owner",job["id"])["status"])

    def test_unknown_call_is_not_retried_and_progress_has_real_transport_stages(self):
        job=self.request();self.prepare()
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ReadTimeout("synthetic",request=request))
        result=self.v.finish();self.assertEqual("UNKNOWN",result["status"])
        self.assertIsNone(self.prepare());self.assertEqual(1,len(self.v.wire))
        rows=self.c.admin_query("SELECT payload->>'phase' FROM {oa} WHERE workspace_key LIKE %s",("ai.live."+job["id"]+".%",)).fetchall()
        self.assertIn(("SENDING",),rows)
        self.assertNotIn(("RESPONSE_RECEIVED",),rows)

    def test_malformed_received_reply_is_retried_five_times_and_recorded_as_called(self):
        self.request();self.prepare()
        self.c.callback=lambda request:httpx.Response(200,content=b"invalid synthetic JSON")
        from oa.sanity_ai_jobs import _now
        for attempt in range(6):
            at=_now()+timedelta(seconds=400*attempt)
            with executing_on("vwp"),patch("oa.sanity_ai_jobs._db_now",return_value=at):result=self.c.run_job(now=at)
            self.assertEqual("FAILED" if attempt==5 else "PENDING",result["status"],result)
        self.assertEqual("FAILED",result["status"])
        self.assertIs(result["model_called"],True)
        self.assertEqual(5,result["retry_count"])
        self.assertIsNone(self.prepare());self.assertEqual(6,len(self.v.wire))

    def test_batch_retries_only_the_unsent_child_and_keeps_measured_completed_work(self):
        from tests.test_vwp_inference import configuration
        from oa.sanity_ai_jobs import _now
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
                               overhead_tokens=1024,max_records=1,output_parameter="max_tokens")
        self.connection=self.v.create(configuration(batch=settings.public()))
        self.request();self.prepare()
        first=self.prepare()
        self.assertEqual(1,first["progress"]["completed_calls"])
        self.assertIsNone(first["progress"]["stage_eta_seconds"])
        self.assertGreater(first["progress"]["seconds_per_record"],0)
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ConnectError("synthetic",request=request))
        failed=self.prepare()
        self.assertEqual(("RUNNING",1,1),(failed["status"],failed["progress"]["completed_calls"],failed["progress"]["retry_count"]))
        self.c.callback=None
        at=_now()+timedelta(seconds=10)
        with executing_on("vwp"),patch("oa.sanity_ai_jobs._db_now",return_value=at):second=self.c.run_job(now=at)
        self.assertEqual(2,second["progress"]["completed_calls"])
        self.assertIsNotNone(second["progress"]["stage_eta_seconds"])
        # Continue at the same monotonic logical DB time; never repeat child 1.
        with patch("oa.sanity_ai_jobs._db_now",return_value=at):result=self.v.finish()
        self.assertEqual("COMPLETED",result["status"],result)
        self.assertEqual(4,len(self.v.wire))  # two leaves, one unsent retry, merge
        self.assertEqual(self.v.wire[1].content,self.v.wire[2].content)
        self.assertNotEqual(self.v.wire[0].content,self.v.wire[2].content)

    def test_existing_oa_model_runs_on_the_vwp_host_without_a_vwp_relay(self):
        with self.c.service() as service:
            service.request(*self.c.path,expected_revision=0,request_id=uuid4())
        self.assertIsNone(self.c.run_job())
        self.assertEqual("PENDING",self.prepare()["status"])
        result=self.prepare()
        self.assertEqual(("COMPLETED","vwp"),(result["status"],result["worker_site"]))
        self.assertEqual(1,len(self.c.requests));self.assertEqual([],self.v.wire)

    def test_heartbeat_uses_bounded_restricted_role_state_and_never_a_login_session(self):
        from common.sanity_db import Database
        with self.c.service() as service:self.assertFalse(worker_health(service.db)["online"])
        with executing_on("vwp"):
            heartbeat(lambda:Database("oa"));heartbeat(lambda:Database("oa"))
        with self.c.service() as service:
            self.assertTrue(worker_health(service.db)["online"])
            row=service.db.execute("SELECT * FROM {oa} WHERE record_type='WS_SESSION' AND workspace_key='ai.worker.vwp'").fetchone()
            self.assertEqual(1,row["version"])
            self.assertEqual("1970-01-01T00:00:00+00:00",row["payload"]["expires_at"])
            with self.assertRaises(ReviewError) as caught:service.ws.session("ai.worker.vwp")
            self.assertEqual("UNAUTHORIZED",caught.exception.code)
        self.assertEqual(1,self.c.admin_query("SELECT count(*) FROM {oa} WHERE workspace_key='ai.worker.vwp'").fetchone()[0])
