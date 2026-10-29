"""Approved OA model selection and connection-only failover; synthetic data only."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_integration_examples import clustering_example
from oa.sanity_ai_batch_jobs import Ledger
from oa.sanity_ai_model import OAAdvisor, OASettings
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from tests.support import PgTestCase
from tests.test_ai_model import chat, environment


FALLBACK = "https://backup.example.test/v1/chat/completions"


class RoutingConfigurationTests(unittest.TestCase):
    def test_choices_are_explicit_and_binding_preserves_personal_credentials(self):
        advisor = OAAdvisor(environment(SANITY_OA_AI_MODELS='["synthetic-model-v1","synthetic-model-v2"]',
                                        SANITY_OA_AI_TOKEN_SOURCE="user", SANITY_OA_AI_FALLBACK_URL=FALLBACK))
        bound = advisor.for_model("synthetic-model-v2").with_user_token("synthetic-personal-bearer")
        self.assertEqual(["synthetic-model-v1", "synthetic-model-v2"], bound.model_choices())
        self.assertEqual("synthetic-model-v2", bound.settings.model)
        self.assertEqual("synthetic-model-v1", advisor.settings.model)
        self.assertEqual("", advisor.settings.token)
        self.assertEqual("synthetic-personal-bearer", bound.settings.token)
        self.assertEqual(FALLBACK, bound.settings.fallback_endpoint)
        with self.assertRaises(ReviewError) as caught:
            advisor.for_model("unapproved-model")
        self.assertEqual("AI_MODEL_NOT_ALLOWED", caught.exception.code)

    def test_single_model_configuration_remains_selectable_without_new_settings(self):
        advisor = OAAdvisor(environment())
        self.assertEqual(["synthetic-model-v1"], advisor.model_choices())
        self.assertIs(advisor, advisor.for_model("synthetic-model-v1"))

    def test_invalid_model_catalogs_fail_without_echoing_values(self):
        for raw in ('[]', '{}', '"value"', 'null', '[true]', '[{}]', '[null]',
                    '["synthetic-model-v1","synthetic-model-v1"]', '["different-default"]',
                    '["synthetic-model-v1","has spaces"]', '["synthetic-model-v1",NaN]',
                    json.dumps(["synthetic-model-v1"] + [f"model-{i}" for i in range(100)]),
                    '[' * 2000):
            with self.subTest(raw=raw[:60]), self.assertRaises(ReviewError) as caught:
                OASettings.from_env(environment(SANITY_OA_AI_MODELS=raw))
            self.assertEqual("AI_CONFIG", caught.exception.code)
            self.assertNotIn(raw, str(caught.exception))

    def test_fallback_requires_a_distinct_full_https_endpoint(self):
        for value in (environment()["SANITY_OA_AI_URL"], "http://backup.example.test/api",
                      "https://backup.example.test/", "https://user:private@backup.example.test/api",
                      "https://backup.example.test/api?q=private", "https://backup.example.test/api#secret",
                      "https://backup.example.test:bad/api", "https://backup.example.test/\napi",
                      "https://backup.example.test/a\\b"):
            with self.subTest(value=value), self.assertRaises(ReviewError) as caught:
                OASettings.from_env(environment(SANITY_OA_AI_FALLBACK_URL=value))
            self.assertEqual("AI_CONFIG", caught.exception.code)
            self.assertNotIn(value, str(caught.exception))

    def test_routing_and_catalog_changes_invalidate_pins_but_tokens_can_rotate(self):
        base = OAAdvisor(environment())
        fallback = OAAdvisor(replace(base.settings, fallback_endpoint=FALLBACK))
        catalog = OAAdvisor(replace(fallback.settings, models=(base.settings.model, "second-model")))
        self.assertEqual(4, len({base.describe()["config_sha256"], fallback.describe()["config_sha256"],
                                 catalog.describe()["config_sha256"], catalog.for_model("second-model").describe()["config_sha256"]}))
        self.assertEqual(catalog.describe(), OAAdvisor(replace(catalog.settings, token="rotated-bearer")).describe())
        self.assertNotIn(FALLBACK, repr(catalog.settings) + json.dumps(catalog.describe()))

    def test_new_settings_are_loaded_from_private_dotenv_and_environment(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            path = Path(folder) / ".env"
            path.write_text('SANITY_OA_AI_FALLBACK_URL=' + FALLBACK + '\n'
                            'SANITY_OA_AI_MODELS=\'["model-a","model-b"]\'\n', encoding="utf-8")
            values = load_environment(path)
            self.assertEqual(FALLBACK, values["SANITY_OA_AI_FALLBACK_URL"])
            self.assertEqual('["model-a","model-b"]', values["SANITY_OA_AI_MODELS"])
            with patch.dict(os.environ, {"SANITY_OA_AI_FALLBACK_URL": "", "SANITY_OA_AI_MODELS": ""}):
                self.assertEqual("", load_environment(path)["SANITY_OA_AI_FALLBACK_URL"])
                self.assertEqual("", load_environment(path)["SANITY_OA_AI_MODELS"])


class RoutingTransportTests(unittest.TestCase):
    def setUp(self):
        self.packet = clustering_example()
        self.requests = []

    def invoke(self, respond, **settings):
        def capture(request):
            self.requests.append(request)
            return respond(request)
        advisor = OAAdvisor(environment(SANITY_OA_AI_FALLBACK_URL=FALLBACK, **settings),
                            transport=httpx.MockTransport(capture))
        return advisor.cluster(self.packet["context"], self.packet["cases"], self.packet["identity_fields"])

    def success(self):
        return httpx.Response(200, json=chat(self.packet["reply"]))

    def test_primary_success_does_not_contact_backup(self):
        result = self.invoke(lambda _: self.success())
        self.assertEqual(1, len(self.requests))
        self.assertEqual({"endpoint": "primary", "attempted_endpoints": ["primary"]}, result["metadata"]["routing"])

    def test_connect_failover_sends_identical_body_token_and_gateway_session_to_selected_model(self):
        for error_type in (httpx.ConnectError, httpx.ConnectTimeout):
            self.requests = []
            def respond(request):
                if len(self.requests) == 1:
                    raise error_type("private connection diagnostic " + FALLBACK, request=request)
                return self.success()
            with self.subTest(error_type=error_type):
                result = self.invoke(respond, SANITY_OA_AI_CLIENT_API_KEY="synthetic-client-key",
                                     SANITY_OA_AI_CLIENT_NAME="synthetic-app", SANITY_OA_AI_CLIENT_VERSION="1",
                                     SANITY_OA_AI_SESSION_MODE="per_request")
                self.assertEqual([environment()["SANITY_OA_AI_URL"], FALLBACK], [str(r.url) for r in self.requests])
                self.assertEqual(self.requests[0].content, self.requests[1].content)
                self.assertEqual({k: v for k, v in self.requests[0].headers.items() if k != 'host'},
                                 {k: v for k, v in self.requests[1].headers.items() if k != 'host'})
                self.assertEqual({"endpoint": "fallback", "attempted_endpoints": ["primary", "fallback"]},
                                 result["metadata"]["routing"])
                self.assertNotIn(FALLBACK, json.dumps(result))

    def test_http_status_auth_and_redirect_responses_never_trigger_fallback(self):
        for status in (307, 401, 403, 429, 500, 502, 503, 504):
            self.requests = []
            with self.subTest(status=status), self.assertRaises(ReviewError) as caught:
                self.invoke(lambda _: httpx.Response(status, text="private response", headers={"Location": FALLBACK}))
            self.assertEqual("AI_CALL_FAILED", caught.exception.code)
            self.assertTrue(caught.exception.model_called)
            self.assertEqual(1, len(self.requests))

    def test_read_write_and_protocol_uncertainty_never_trigger_fallback(self):
        for error_type in (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError,
                           httpx.RemoteProtocolError, httpx.PoolTimeout):
            self.requests = []
            def respond(request):
                raise error_type("private diagnostic " + FALLBACK, request=request)
            with self.subTest(error_type=error_type), self.assertRaises(ReviewError) as caught:
                self.invoke(respond)
            self.assertEqual("AI_OUTCOME_UNKNOWN", caught.exception.code)
            self.assertTrue(caught.exception.model_called)
            self.assertNotIn(FALLBACK, str(caught.exception))
            self.assertEqual(1, len(self.requests))

    def test_invalid_or_oversized_response_is_rejected_without_fallback(self):
        for response, options, code in ((httpx.Response(200, json={}), {}, "INVALID_AI_RESPONSE"),
                                       (self.success(), {"SANITY_OA_AI_MAX_OUTPUT_BYTES": "1"}, "AI_OUTPUT_LIMIT")):
            self.requests = []
            with self.subTest(code=code), self.assertRaises(ReviewError) as caught:
                self.invoke(lambda _: response, **options)
            self.assertEqual(code, caught.exception.code)
            self.assertEqual(1, len(self.requests))

    def test_both_connect_failures_are_known_unsent_and_never_expose_diagnostics(self):
        def respond(request):
            raise httpx.ConnectError("private diagnostic " + FALLBACK, request=request)
        with self.assertRaises(ReviewError) as caught:
            self.invoke(respond)
        self.assertEqual("AI_CONNECT_FAILED", caught.exception.code)
        self.assertFalse(caught.exception.model_called)
        self.assertEqual(2, len(self.requests))
        self.assertNotIn(FALLBACK, str(caught.exception))

    def test_fallback_read_timeout_remains_unknown_and_is_not_sent_back_to_primary(self):
        def respond(request):
            error_type = httpx.ConnectError if len(self.requests) == 1 else httpx.ReadTimeout
            raise error_type("synthetic failure", request=request)
        with self.assertRaises(ReviewError) as caught:
            self.invoke(respond)
        self.assertEqual("AI_OUTCOME_UNKNOWN", caught.exception.code)
        self.assertTrue(caught.exception.model_called)
        self.assertEqual(2, len(self.requests))

    def test_total_deadline_is_shared_and_prevents_late_fallback_dispatch(self):
        clock = [0]
        def respond(request):
            clock[0] = 121
            raise httpx.ConnectTimeout("synthetic failure", request=request)
        with patch("oa.sanity_ai_model.time.monotonic", side_effect=lambda: clock[0]), self.assertRaises(ReviewError) as caught:
            self.invoke(respond)
        self.assertEqual("AI_CONNECT_FAILED", caught.exception.code)
        self.assertFalse(caught.exception.model_called)
        self.assertEqual(1, len(self.requests))

    def test_fallback_receives_only_the_remaining_timeout(self):
        clock = [0]
        def respond(request):
            if len(self.requests) == 1:
                self.assertEqual(60, request.extensions["timeout"]["connect"])
                clock[0] = 10
                raise httpx.ConnectError("synthetic failure", request=request)
            self.assertEqual(110, request.extensions["timeout"]["connect"])
            return self.success()
        with patch("oa.sanity_ai_model.time.monotonic", side_effect=lambda: clock[0]):
            self.invoke(respond)
        self.assertEqual(2, len(self.requests))

    def test_a_connection_exception_after_headers_cannot_trigger_fallback(self):
        class BrokenBody(httpx.SyncByteStream):
            def __iter__(self):
                raise httpx.ConnectError("synthetic failure after response headers")
                yield b""
        with self.assertRaises(ReviewError) as caught:
            self.invoke(lambda _: httpx.Response(200, stream=BrokenBody()))
        self.assertEqual("AI_OUTCOME_UNKNOWN", caught.exception.code)
        self.assertEqual(1, len(self.requests))

    def test_responses_api_retains_selected_model_and_complete_input_after_failover(self):
        def respond(request):
            if len(self.requests) == 1:
                raise httpx.ConnectError("synthetic failure", request=request)
            body = json.loads(request.content)
            self.assertEqual("synthetic-model-v1", body["model"])
            self.assertFalse(body["store"])
            self.assertEqual(self.packet["context"]["report"]["records"],
                             json.loads(body["input"][1]["content"])["context"]["report"]["records"])
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message",
                "content": [{"type": "output_text", "text": json.dumps(self.packet["reply"])}]}]})
        result = self.invoke(respond, SANITY_OA_AI_API="responses")
        self.assertEqual("fallback", result["metadata"]["routing"]["endpoint"])


class RoutingJobTests(PgTestCase):
    def setUp(self):
        # Reuse the same isolated synthetic workspace as existing job tests.
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c = AIJobDatabaseTests()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.wire = []
        self.settings = replace(self.c.advisor.settings, models=("synthetic-model", "selected-model"),
                                fallback_endpoint=FALLBACK)
        self.bind()

    def bind(self, *, batch=False, handler=None, **changes):
        from tests.test_ai_batches import batch_settings
        settings = replace(self.settings, **changes)
        if batch:
            settings = replace(settings, batch=batch_settings(max_records=1))
        def respond(request):
            self.wire.append(request)
            if handler:
                failure = handler(request)
                if failure is not None:
                    return failure
            response = self.c.respond(request)
            envelope = json.loads(response.content)
            envelope["model"] = json.loads(request.content)["model"]
            return httpx.Response(200, json=envelope)
        self.c.advisor = OAAdvisor(settings, transport=httpx.MockTransport(respond))

    def finish(self):
        for _ in range(30):
            result = self.c.run_job()
            if result is None or result["status"] in ("COMPLETED", "FAILED", "STALE", "UNKNOWN", "CANCELLED"):
                return result
        self.fail("Synthetic batch did not terminate")

    def test_selected_model_is_pinned_idempotent_and_saved_as_proposal_provenance(self):
        request_id = uuid4()
        job = self.c.enqueue(request_id, model_id="selected-model")
        self.assertEqual("selected-model", job["model"])
        self.assertEqual(job, self.c.enqueue(request_id, model_id="selected-model"))
        self.c.assert_code("REQUEST_CONFLICT", lambda: self.c.enqueue(request_id, model_id="synthetic-model"))
        self.assertEqual("COMPLETED", self.c.run_job()["status"])
        with self.c.service() as service:
            proposal, _ = WorkspaceClusteringStore(service.db, ws=service.ws).current("report")
            self.assertEqual("selected-model", proposal["model_metadata"]["requested_model"])
            self.assertEqual("selected-model", proposal["model_metadata"]["model"])
            capability = service.capability(*self.c.path)
            self.assertEqual(["synthetic-model", "selected-model"], capability["models"])
            self.assertEqual("selected-model", capability["job"]["model"])
        self.assertEqual(["selected-model"], [json.loads(r.content)["model"] for r in self.wire])

    def test_unapproved_model_is_rejected_before_writing_or_dispatch(self):
        self.c.assert_code("AI_MODEL_NOT_ALLOWED", lambda: self.c.enqueue(model_id="arbitrary-model"))
        self.assertIsNone(self.c.run_job())
        self.assertEqual([], self.wire)

    def test_catalog_or_fallback_change_stales_the_queued_job_before_http(self):
        self.c.enqueue(model_id="selected-model")
        self.bind(fallback_endpoint="https://changed.example.test/v1/chat/completions")
        result = self.c.run_job()
        self.assertEqual(("STALE", "AI_CONFIG_CHANGED"), (result["status"], result["error"]))
        self.assertEqual([], self.wire)

    def test_removed_selected_model_stales_the_job_and_does_not_use_default(self):
        self.c.enqueue(model_id="selected-model")
        self.bind(models=())
        result = self.c.run_job()
        self.assertEqual(("STALE", "AI_CONFIG_CHANGED"), (result["status"], result["error"]))
        self.assertEqual([], self.wire)

    def test_changing_default_does_not_replace_an_already_selected_allowed_model(self):
        self.c.enqueue(model_id="synthetic-model")
        self.bind(model="selected-model")
        self.assertEqual("COMPLETED", self.c.run_job()["status"])
        self.assertEqual("synthetic-model", json.loads(self.wire[0].content)["model"])

    def test_both_connection_failures_are_terminal_without_worker_replay(self):
        def failed(request):
            raise httpx.ConnectError("synthetic failure", request=request)
        for batch in (False, True):
            with self.subTest(batch=batch):
                self.wire = []
                self.bind(batch=batch, handler=failed)
                self.c.enqueue(model_id="selected-model")
                result = self.c.run_job()
                self.assertEqual(("FAILED", "AI_CONNECT_FAILED", False),
                                 (result["status"], result["error"], result["model_called"]))
                self.assertIsNone(self.c.run_job())
                self.assertEqual(2, len(self.wire))

    def test_batch_resume_keeps_selected_model_and_audits_fallback_calls(self):
        def fail_primary(request):
            if str(request.url) != FALLBACK:
                raise httpx.ConnectError("synthetic primary failure", request=request)
        self.bind(batch=True, handler=fail_primary)
        job = self.c.enqueue(model_id="selected-model")
        self.assertEqual("RUNNING", self.c.run_job()["status"])
        self.bind(batch=True, handler=fail_primary)  # Fresh worker/advisor.
        self.assertEqual("COMPLETED", self.finish()["status"])
        self.assertEqual(["selected-model"] * 6, [json.loads(r.content)["model"] for r in self.wire])
        with self.c.service() as service:
            ledger = Ledger(service, service._load(job["id"]))
            final = ledger.get("final")[0]["metadata"]
            self.assertEqual("selected-model", final["requested_model"])
            self.assertEqual({"primary_calls": 0, "fallback_calls": 3, "connect_failures": 3}, final["routing_summary"])
            self.assertEqual(3, len([step for step, _ in ledger.rows("step.") if step["status"] == "RESERVED"]))
            self.assertIsNotNone(ledger.audit())
        self.assertIsNone(self.c.run_job())

    def test_selected_model_and_personal_token_are_both_used_on_backup(self):
        self.c.configure_personal_tokens()
        from tests.test_ai_credentials import synthetic_jwt
        token = synthetic_jwt(subject="owner")
        self.c.save_personal_token("owner", token, 0)
        def fail_primary(request):
            if str(request.url) != FALLBACK:
                raise httpx.ConnectError("synthetic primary failure", request=request)
        self.bind(token_source="user", token="", handler=fail_primary)
        self.c.enqueue(model_id="selected-model")
        self.assertEqual("COMPLETED", self.c.run_job()["status"])
        self.assertEqual(["Bearer " + token] * 2, [r.headers["Authorization"] for r in self.wire])
        self.assertEqual(["selected-model"] * 2, [json.loads(r.content)["model"] for r in self.wire])

    def test_single_call_group_judgment_uses_a_separately_selected_model(self):
        self.c.enqueue(model_id="synthetic-model")
        self.assertEqual("COMPLETED", self.c.run_job()["status"])
        with self.c.service() as service:
            WorkspaceClusteringStore(service.db, ws=service.ws).change(*self.c.path, action="CONFIRM",
                expected_revision=1, request_id=uuid4(), reason="합성 사람이 분류를 확정했습니다.")
        self.c.enqueue(group_id="g1", expected_cluster_revision=2, model_id="selected-model")
        self.assertEqual("COMPLETED", self.c.run_job()["status"])
        self.assertEqual(["synthetic-model", "selected-model"], [json.loads(r.content)["model"] for r in self.wire])

    def test_batch_group_judgment_pins_the_selected_model_through_leaf_and_merge(self):
        from tests.test_ai_evidence_batches import EvidenceBatchDatabaseTests, envelope, reply
        fixture = EvidenceBatchDatabaseTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.c = fixture.c
        self.settings = replace(self.c.advisor.settings, models=("synthetic-model", "selected-model"),
                                fallback_endpoint=FALLBACK)
        def respond(request):
            if str(request.url) != FALLBACK:
                raise httpx.ConnectError("synthetic primary failure", request=request)
            packet = json.loads(json.loads(request.content)["messages"][1]["content"])
            value = json.loads(envelope(reply(packet)).content)
            value["model"] = "selected-model"
            return httpx.Response(200, json=value)
        self.bind(batch=True, handler=respond)
        job = self.c.enqueue(group_id="g1", expected_cluster_revision=2, model_id="selected-model")
        self.assertEqual("COMPLETED", self.finish()["status"])
        self.assertEqual(["selected-model"] * 6, [json.loads(r.content)["model"] for r in self.wire])
        with self.c.service() as service:
            event = service._load(job["id"])
            self.assertEqual(2, event["data"]["batch"]["version"])
            self.assertEqual("selected-model", Ledger(service, event).get("final")[0]["metadata"]["model"])

    def test_model_catalog_change_between_batch_children_stops_before_next_call(self):
        self.bind(batch=True)
        self.c.enqueue(model_id="selected-model")
        self.assertEqual("RUNNING", self.c.run_job()["status"])
        self.bind(batch=True, models=("synthetic-model", "selected-model", "third-model"))
        result = self.finish()
        self.assertEqual(("STALE", "AI_CONFIG_CHANGED"), (result["status"], result["error"]))
        self.assertEqual(1, len(self.wire))
