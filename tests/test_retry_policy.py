"""Verify retry eligibility from actual synthetic OA/VWP HTTP outcomes."""
from datetime import datetime, timezone
import unittest

import httpx

from common.sanity_common import ReviewError, canonical_json
from common.sanity_integration_examples import clustering_example
from common.sanity_vwp_ai_http import invoke as vwp_invoke
from oa.sanity_ai_model import OAAdvisor
from oa.sanity_ai_retry import MAX_RETRIES, MAX_RESUMES, receipt, retry_at, valid_receipt, resumable
from tests.test_ai_model import environment
from tests.test_vwp_inference import PERSONAL_TOKEN, body, profile


def invoke(raw, private, **options):
    return vwp_invoke(raw, {**private, "token":PERSONAL_TOKEN}, **options)


class RetryPolicyTests(unittest.TestCase):
    def failure(self, client, handler):
        sent = []
        def capture(request):
            sent.append(request)
            return handler(request)
        transport=httpx.MockTransport(capture)
        with self.assertRaises(ReviewError) as raised:
            if client == "oa":
                adapter=OAAdvisor(environment(),transport=transport)
                packet=clustering_example()
                adapter.cluster(packet["context"],packet["cases"],packet["identity_fields"])
            else:
                invoke(canonical_json(body()),profile(),timeout=5,max_output_bytes=4096,transport=transport)
        self.assertEqual(1,len(sent))
        return raised.exception

    def test_server_busy_statuses_have_a_known_failure_and_bounded_delay(self):
        now=datetime(2026,10,30,tzinfo=timezone.utc)
        for client in ("oa","vwp"):
            for status in (429,500,502,503,504):
                with self.subTest(client=client,status=status):
                    error=self.failure(client,lambda _:httpx.Response(status,headers={"Retry-After":"9999"}))
                    proof=receipt(error)
                    self.assertEqual({"kind":"HTTP","error":"AI_CALL_FAILED","model_called":True,"http_status":status},proof)
                    self.assertTrue(valid_receipt(proof))
                    self.assertEqual(300,(datetime.fromisoformat(retry_at(now,1,error))-now).total_seconds())
        self.assertEqual([5,10,20,40,60],[(datetime.fromisoformat(retry_at(now,i))-now).total_seconds()
                                        for i in range(1,MAX_RETRIES+1)])

    def test_auth_client_errors_and_redirects_are_not_automatically_retried(self):
        for client in ("oa","vwp"):
            for status in (307,400,401,403,404):
                with self.subTest(client=client,status=status):
                    error=self.failure(client,lambda _:httpx.Response(status,headers={"Location":"https://other.invalid"}))
                    self.assertIsNone(receipt(error))

    def test_received_invalid_json_is_retryable_but_early_rejection_is_not(self):
        for client in ("oa","vwp"):
            with self.subTest(client=client):
                invalid=self.failure(client,lambda _:httpx.Response(200,content=b"not synthetic JSON"))
                self.assertTrue(invalid.model_called)
                self.assertTrue(invalid.response_complete)
                self.assertEqual("INVALID_RESPONSE",receipt(invalid)["kind"])
                compressed=self.failure(client,lambda _:httpx.Response(200,headers={"Content-Encoding":"gzip"}))
                self.assertFalse(compressed.response_complete)
                self.assertIsNone(receipt(compressed))

    def test_connection_failure_can_retry_but_read_timeout_is_unknown(self):
        for client in ("oa","vwp"):
            with self.subTest(client=client):
                def connect(request):raise httpx.ConnectError("synthetic",request=request)
                error=self.failure(client,connect)
                self.assertEqual({"kind":"CONNECT","error":"AI_CONNECT_FAILED","model_called":False},receipt(error))
                def timeout(request):raise httpx.ReadTimeout("synthetic",request=request)
                error=self.failure(client,timeout)
                self.assertEqual("AI_OUTCOME_UNKNOWN",error.code)
                self.assertIsNone(receipt(error))

    def test_unknown_transport_reason_is_bounded_without_enabling_auto_retry(self):
        for client in ("oa","vwp"):
            for kind,reason in ((httpx.ReadTimeout,"READ_TIMEOUT"),(httpx.WriteTimeout,"WRITE_TIMEOUT"),
                                (httpx.ReadError,"TRANSPORT_ERROR")):
                def fail(request,kind=kind):raise kind("synthetic transport interruption",request=request)
                with self.subTest(client=client,reason=reason):
                    error=self.failure(client,fail)
                    self.assertEqual("AI_OUTCOME_UNKNOWN",error.code)
                    self.assertEqual(reason,error.unknown_reason)
                    self.assertIsNone(receipt(error))

    def test_resume_needs_safe_checkpoint_and_never_replays_unknown_or_result(self):
        proof={"kind":"HTTP","error":"AI_CALL_FAILED","model_called":True,"http_status":503}
        failed={"status":"FAILED","data":{"fail_safe":True,"error":"AI_CALL_FAILED","retry_failure":proof,"batch":{"version":1}}}
        self.assertTrue(resumable(failed,checkpoint_ready=True))
        self.assertFalse(resumable(failed,checkpoint_ready=False))
        self.assertFalse(resumable({**failed,"status":"UNKNOWN"},checkpoint_ready=True))
        self.assertFalse(resumable({**failed,"data":{**failed["data"],"result_ref":{}}},checkpoint_ready=True))
        self.assertFalse(resumable({**failed,"data":{**failed["data"],"resume_count":MAX_RESUMES}},checkpoint_ready=True))
        self.assertFalse(resumable({**failed,"data":{**failed["data"],"error":"AI_BATCH_LIMIT"}},checkpoint_ready=True))
        self.assertFalse(resumable({**failed,"data":{**failed["data"],"error":"AI_AUTH_FAILED"}},checkpoint_ready=True))
        self.assertTrue(resumable({**failed,"status":"CANCELLED"},checkpoint_ready=True))
        self.assertFalse(resumable({**failed,"status":"CANCELLED"},checkpoint_ready=False))

    def test_failure_proof_cannot_be_forged_with_unknown_or_extra_fields(self):
        for value in (None, {}, {"kind":"CONNECT","error":"AI_OUTCOME_UNKNOWN","model_called":False},
                      {"kind":"HTTP","error":"AI_CALL_FAILED","model_called":True,"http_status":401},
                      {"kind":"HTTP","error":"AI_CALL_FAILED","model_called":True,"http_status":503,"other":"synthetic"},
                      {"kind":"INVALID_RESPONSE","error":"INVALID_AI_RESPONSE","model_called":True,"response_complete":False}):
            with self.subTest(value=value):self.assertFalse(valid_receipt(value))
