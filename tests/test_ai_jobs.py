"""Focused PostgreSQL + mock HTTP checks of durable OA inference boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
import json
from unittest.mock import patch
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet

from common.sanity_common import ReviewError, canonical_json
from common.sanity_db import Database, json_value
from oa.sanity_access import access_lock
from oa.sanity_ai_jobs import AIJobService, PREFIX, _now, run_once
from oa.sanity_ai_model import OAAdvisor, OASettings
from oa.sanity_ai_tokens import UserTokenStore
from oa.sanity_workspace import WorkspaceStore, record_id
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from tests.support import PgTestCase


class SyntheticAIWorkspace(WorkspaceStore):
    # Explicit isolated test injection. The deployment CLI always constructs
    # ProductionWorkspaceStore with current SSO identities and business grants.
    case_policy = "OA_RETRIEVAL_V1"


class AIJobDatabaseTests(PgTestCase):
    path = ("owner", "block", "version", "report")

    def setUp(self):
        super().setUp()
        self.requests, self.callback = [], None
        self.criteria = [{"id": "C1", "version": "1", "text": "모든 클록 경로에는 승인된 동기화기가 필요합니다.",
                          "scope": {"item_id": "clock_check"}}]
        self.advisor = OAAdvisor(OASettings(enabled=True, endpoint="https://model.example.test/v1/chat/completions",
            token="synthetic-test-token", model="synthetic-model"), transport=httpx.MockTransport(self.respond))
        with Database("oa") as db, db.transaction():
            ws = SyntheticAIWorkspace(db)
            ws.put("WS_USER", "owner", {"id": "owner", "name": "합성 담당자", "username": "owner", "role": "OWNER"}, state={"active": True})
            ws.put("WS_PROJECT", "project", {"id": "project", "project_id": "project", "tops": []})
            ws.put("WS_BLOCK", "block", {"id": "block", "project_id": "project", "owners": ["owner"]}, project="project")
            ws.put("WS_VERSION", "version", {"id": "version", "project_id": "project", "block_id": "block",
                   "net_ver": "N1", "revision": "R1", "layout": "PRE", "ecos": [None]}, project="project", block="block")
            report = {"id": "report", "project_id": "project", "block_id": "block", "version_id": "version",
                "version_record_id": str(record_id("WS_VERSION", "version")), "eco": None,
                "item_id": "clock_check", "family": "GCA", "parser": "synthetic-v1", "rule_version": "1", "rule": "C1",
                "parse_status": "COMPLETE", "check_status": "FAIL", "analysis_ready": True,
                "raw_report": "합성 동기화기 누락", "record_count": 2,
                "records": [{"id": key, "raw": "클록 동기화기 누락", "fields": {"object": "top/" + key,
                    "check_point": "clock", "mode": "M1"}} for key in ("a", "b")]}
            ws.put("WS_REPORT", "report", report, project="project", block="block")

    def respond(self, request):
        packet = json.loads(json.loads(request.content)["messages"][1]["content"])
        self.requests.append(packet)
        if self.callback:
            response = self.callback(request)
            if response is not None:
                return response
        context = packet["context"]
        if "group_evidence" in context:
            advice = {"schema_version": 2, "conclusion": "FAIL", "reason": "현재 경로에 필요한 동기화기가 없어 실패입니다.",
                      "evidence_ids": [context["group_evidence"][0]["id"]], "reference_ids": [], "criterion_ids": ["C1"]}
        else:
            ids = [row["id"] for row in context["report"]["records"]]
            advice = {"groups": [{"id": "g1", "label": "동기화기 누락", "record_ids": ids}],
                "reason": "공통 클록 동기화기 누락을 묶었습니다.", "reference_ids": [],
                "group_rationales": [{"group_id": "g1", "reason": "두 경로에 동기화기가 없고 예외 조건은 확인되지 않았습니다.",
                                      "evidence_ids": [ids[0]], "reference_groups": []}]}
        return httpx.Response(200, json={"model": "synthetic-model", "choices": [{"finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(advice, ensure_ascii=False)}}]})

    @contextmanager
    def service(self, advisor=None):
        with Database("oa") as db, db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            access_lock(db)
            yield AIJobService(db, SyntheticAIWorkspace(db), advisor=advisor or self.advisor,
                               criteria_provider=lambda context: self.criteria)

    def enqueue(self, request_id=None, **kwargs):
        with self.service() as service:
            return service.enqueue(*self.path, expected_revision=kwargs.pop("expected_revision", 0),
                                   request_id=request_id or uuid4(), **kwargs)

    def run_job(self, **kwargs):
        return run_once(lambda: Database("oa"), SyntheticAIWorkspace, self.advisor,
                        criteria_provider=lambda context: self.criteria, **kwargs)

    def assert_code(self, code, callback):
        with self.assertRaises(ReviewError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def test_complete_result_is_a_proposal_and_same_request_never_calls_twice(self):
        request = uuid4()
        first = self.enqueue(request)
        self.assertEqual(first, self.enqueue(request))
        self.assert_code("AI_JOB_ACTIVE", lambda: self.enqueue())
        result = self.run_job()
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(str(request), result["event_id"])
        self.assertEqual(result, self.enqueue(request))
        self.assertIsNone(self.run_job())
        self.assertEqual(1, len(self.requests))
        with self.service() as service:
            event, revision = WorkspaceClusteringStore(service.db, ws=service.ws).current("report")
            self.assertEqual(1, revision)
            self.assertTrue(event["model_called"])
            self.assertFalse(event["confirmed"])
            self.assertEqual({"a", "b"}, set(event["groups"][0]["record_ids"]))
        rows = self.admin_query("SELECT state,version,created_at=updated_at,project,workspace_block_ref FROM {oa} "
                                "WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s", (PREFIX + "%",)).fetchall()
        self.assertEqual(4, len(rows))
        self.assertTrue(all(row == ({}, 0, True, None, None) for row in rows))

    def test_conflicting_request_and_concurrent_workers_have_one_call(self):
        request = uuid4()
        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs = list(executor.map(lambda _: self.enqueue(request), range(2)))
        self.assertEqual(jobs[0]["id"], jobs[1]["id"])
        self.assert_code("REQUEST_CONFLICT", lambda: self.enqueue(request, expected_revision=1))
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _: self.run_job(), range(2)))
        with self.service() as service:
            self.assertEqual("COMPLETED", service.status("owner", request)["status"])
        self.assertEqual(1, len(self.requests))

    def test_human_changes_during_http_are_unlocked_and_late_result_is_stale(self):
        self.enqueue()

        def human_change(request):
            with self.service() as service:
                # A separate transaction can take the exact review lock while
                # HTTP is in progress; retained worker locks would time out.
                WorkspaceClusteringStore(service.db, ws=service.ws).change(*self.path,
                    action="PROPOSE", expected_revision=0, request_id=uuid4())
        self.callback = human_change
        result = self.run_job()
        self.assertEqual(("STALE", "REVISION_CONFLICT"), (result["status"], result["error"]))
        with self.service() as service:
            event, revision = WorkspaceClusteringStore(service.db, ws=service.ws).current("report")
            self.assertEqual(1, revision)
            self.assertFalse(event["model_called"])

    def test_account_disabled_during_http_blocks_publication(self):
        self.enqueue()

        def disable(request):
            self.admin_query("UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() "
                "WHERE record_type='WS_USER' AND workspace_key='owner'", (json_value({"active": False}),))
        self.callback = disable
        result = self.run_job()
        self.assertEqual(("STALE", "UNAUTHORIZED"), (result["status"], result["error"]))
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_timeout_and_abandoned_running_are_unknown_without_retry(self):
        job = self.enqueue()

        def timeout(request):
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        self.callback = timeout
        result = self.run_job()
        self.assertEqual(("UNKNOWN", "AI_OUTCOME_UNKNOWN"), (result["status"], result["error"]))
        self.assertIsNone(self.run_job())
        self.assertEqual(1, len(self.requests))
        another = self.enqueue()
        with self.service() as service:
            service._lock(another["id"])
            event = service._load(another["id"])
            service._append(event, "RUNNING", {**event["data"], "deadline": (_now() - timedelta(seconds=1)).isoformat(),
                             "started_at": (_now() - timedelta(seconds=60)).isoformat(),
                             "call_token": str(uuid4()), "model_attempted": True})
        self.assertEqual("UNKNOWN", self.run_job()["status"])
        self.assertIsNone(self.run_job())
        self.assertEqual(1, len(self.requests))

    def test_result_ready_recovers_without_another_model_call(self):
        self.enqueue()
        with patch.object(AIJobService, "_apply", side_effect=RuntimeError("synthetic crash before application")):
            with self.assertRaises(RuntimeError):
                self.run_job()
        with self.service() as service:
            event = service._latest(work=True)
            self.assertEqual("RESULT_READY", event["status"])
        self.assertEqual("COMPLETED", self.run_job()["status"])
        self.assertEqual(1, len(self.requests))

    def test_completion_commit_failure_rolls_back_proposal_and_reuses_saved_result(self):
        self.enqueue()
        original = AIJobService._append

        def fail_completion(service, previous, status, data, **kwargs):
            if status == "COMPLETED":
                raise RuntimeError("synthetic interruption before commit")
            return original(service, previous, status, data, **kwargs)
        with patch.object(AIJobService, "_append", fail_completion):
            with self.assertRaises(RuntimeError):
                self.run_job()
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])
        self.assertEqual("COMPLETED", self.run_job()["status"])
        self.assertEqual(1, len(self.requests))

    def test_cancelled_running_ignores_late_response(self):
        job = self.enqueue()

        def cancel(request):
            with self.service() as service:
                self.assertEqual("CANCELLED", service.cancel("owner", job["id"])["status"])
        self.callback = cancel
        self.assertEqual("CANCELLED", self.run_job()["status"])
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_full_input_limit_prevents_http_and_records_failure_without_fallback(self):
        self.advisor = OAAdvisor(OASettings(enabled=True, endpoint="https://model.example.test/v1/chat/completions",
            token="synthetic-test-token", model="synthetic-model", max_input_bytes=10), transport=httpx.MockTransport(self.respond))
        self.enqueue()
        result = self.run_job()
        self.assertEqual(("FAILED", "AI_INPUT_LIMIT", False), (result["status"], result["error"], result["model_called"]))
        self.assertEqual([], self.requests)

    def test_result_ledger_limit_is_failed_without_an_ambiguous_retry(self):
        job = self.enqueue()

        def reduce_ledger_budget(request):
            with self.service() as service:
                running = service._load(job["id"])
            # The received response fits the transport budget, while the final
            # wrapped ledger event does not. Leave room for a failure event.
            budget = patch("oa.sanity_ai_jobs.MAX_EVENT_BYTES", len(canonical_json(running)) + 400)
            budget.start()
            self.addCleanup(budget.stop)
        self.callback = reduce_ledger_budget
        result = self.run_job()
        self.assertEqual(("FAILED", "AI_OUTPUT_LIMIT", True), (result["status"], result["error"], result["model_called"]))
        self.assertIsNone(self.run_job())
        self.assertEqual(1, len(self.requests))

    def test_input_and_output_secret_guards_preserve_actual_dispatch_state(self):
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{records,0,raw}}',%s) "
                         "WHERE record_type='WS_REPORT' AND workspace_key='report'", (json_value("synthetic-test-token"),))
        self.enqueue()
        first = self.run_job()
        self.assertEqual("FAILED", first["status"])
        self.assertFalse(first["model_called"])
        self.assertFalse(first["model_attempted"])
        self.assertEqual([], self.requests)
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{records,0,raw}}',%s) "
                         "WHERE record_type='WS_REPORT' AND workspace_key='report'", (json_value("클록 동기화기 누락"),))
        self.enqueue()

        def secret_output(request):
            advice = {"groups": [{"id": "g1", "label": "동기화기 누락", "record_ids": ["a", "b"]}],
                "reason": "synthetic-test-token", "reference_ids": [],
                "group_rationales": [{"group_id": "g1", "reason": "누락 확인", "evidence_ids": ["a"], "reference_groups": []}]}
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(advice)}}]})
        self.callback = secret_output
        second = self.run_job()
        self.assertEqual("FAILED", second["status"])
        self.assertTrue(second["model_called"])
        self.assertEqual(1, len(self.requests))

    def test_settings_change_before_dispatch_and_corrupt_event_fail_closed(self):
        job = self.enqueue()
        self.advisor = OAAdvisor(OASettings(enabled=True, endpoint="https://model.example.test/v1/chat/completions",
            token="synthetic-test-token", model="another-model"), transport=httpx.MockTransport(self.respond))
        self.assertEqual("STALE", self.run_job()["status"])
        self.assertEqual([], self.requests)
        # Fault injection in this disposable fixture only; ordinary app code
        # cannot update immutable WS_CONFIG payload columns.
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{sha256}}',%s) "
            "WHERE record_type='WS_CONFIG' AND workspace_key=%s", (json_value("f" * 64), PREFIX + job["id"] + ".0001"))
        with self.service() as service:
            self.assert_code("AI_JOB_CORRUPT", lambda: service.status("owner", job["id"]))

    def test_gateway_policy_change_invalidates_queued_work_before_http(self):
        settings = replace(self.advisor.settings, client_api_key="synthetic-private-client-key",
                           client_name="synthetic-private-client", client_version="synthetic-release-a",
                           session_mode="per_request")
        self.advisor = OAAdvisor(settings, transport=httpx.MockTransport(self.respond))
        self.enqueue()
        self.advisor = OAAdvisor(replace(settings, client_version="synthetic-release-b"),
                                 transport=httpx.MockTransport(self.respond))
        result = self.run_job()
        self.assertEqual(("STALE", "AI_CONFIG_CHANGED"), (result["status"], result["error"]))
        self.assertEqual([], self.requests)

    def test_gateway_credentials_can_rotate_without_recalling_a_completed_job(self):
        settings = replace(self.advisor.settings, client_api_key="synthetic-private-client-key",
                           client_name="synthetic-private-client", client_version="synthetic-release-a",
                           session_mode="per_request")
        self.advisor = OAAdvisor(settings, transport=httpx.MockTransport(self.respond))
        self.enqueue()
        rotated = replace(settings, token="synthetic-rotated-jwt", client_api_key="synthetic-rotated-client-key")
        self.advisor = OAAdvisor(rotated, transport=httpx.MockTransport(self.respond))

        def check_gateway(request):
            self.assertEqual("Bearer " + rotated.token, request.headers["authorization"])
            self.assertEqual(rotated.client_api_key, request.headers["x-client-api-key"])
            self.assertEqual(rotated.client_name, json.loads(request.content)["extraData"]["clientName"])
        self.callback = check_gateway
        self.assertEqual("COMPLETED", self.run_job()["status"])
        self.assertIsNone(self.run_job())
        self.assertEqual(1, len(self.requests))
        with self.service() as service:
            event, _ = WorkspaceClusteringStore(service.db, ws=service.ws).current("report")
            serialized = json.dumps(event)
            for value in (settings.client_api_key, rotated.client_api_key, rotated.token,
                          rotated.client_name, rotated.client_version):
                self.assertNotIn(value, serialized)

    def test_review_criteria_changes_during_http_make_saved_result_stale(self):
        self.enqueue()
        self.assertEqual("COMPLETED", self.run_job()["status"])
        with self.service() as service:
            WorkspaceClusteringStore(service.db, ws=service.ws).change(*self.path, action="CONFIRM",
                expected_revision=1, request_id=uuid4(), reason="사람이 분류 근거를 검토했습니다.")
        self.enqueue(group_id="g1", expected_cluster_revision=2)

        def revise_criterion(request):
            self.criteria = [{**self.criteria[0], "version": "2"}]
        self.callback = revise_criterion
        result = self.run_job()
        self.assertEqual(("STALE", "AI_CRITERIA_STALE"), (result["status"], result["error"]))
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_PROPOSAL'").fetchone()[0])

    def configure_personal_tokens(self):
        settings = {"SANITY_OA_AI_TOKEN_SOURCE": "user", "SANITY_OA_AI_TOKEN_STORE_SECRET": Fernet.generate_key().decode()}
        private = patch("oa.sanity_ai_tokens.load_environment", return_value=settings)
        private.start(); self.addCleanup(private.stop)
        self.advisor = OAAdvisor(replace(self.advisor.settings, token_source="user", token="",
            client_api_key="synthetic-shared-client-key", client_name="synthetic-client", client_version="1",
            session_mode="per_request"), transport=httpx.MockTransport(self.respond))

    def save_personal_token(self, user_id, token, revision):
        with Database("oa") as db, db.transaction():
            return UserTokenStore(db).save(user_id, token, revision)

    def test_each_requesting_user_uses_only_their_own_token(self):
        self.configure_personal_tokens()
        with Database("oa") as db, db.transaction():
            SyntheticAIWorkspace(db).put("WS_USER", "other", {"id": "other", "name": "다른 합성 사용자",
                "username": "other", "role": "OWNER"}, state={"active": True})
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{owners}}',%s) "
                         "WHERE record_type='WS_BLOCK' AND workspace_key='block'", (json_value(["owner", "other"]),))
        self.save_personal_token("owner", "synthetic-personal-owner-token", 0)
        self.save_personal_token("other", "synthetic-personal-other-token", 0)
        used = []
        self.callback = lambda request: used.append(request.headers["authorization"])
        self.enqueue()
        self.assertEqual("COMPLETED", self.run_job()["status"])
        with self.service() as service:
            service.enqueue("other", "block", "version", "report", expected_revision=1, request_id=uuid4())
        self.assertEqual("COMPLETED", self.run_job()["status"])
        self.assertEqual(["Bearer synthetic-personal-owner-token", "Bearer synthetic-personal-other-token"], used)
        self.assertEqual("", self.advisor.settings.token)
        rows = self.admin_query("SELECT payload FROM {oa}").fetchall()
        for token in ("synthetic-personal-owner-token", "synthetic-personal-other-token"):
            self.assertNotIn(token, json.dumps(rows, default=str))

    def test_missing_or_removed_personal_token_never_borrows_another_users_credential(self):
        self.configure_personal_tokens()
        self.save_personal_token("other", "synthetic-other-only-token", 0)
        self.assert_code("AI_USER_TOKEN_REQUIRED", lambda: self.enqueue())
        with self.service() as service:
            capability = service.capability(*self.path)
        self.assertFalse(capability["available"])
        self.assertEqual("AI_USER_TOKEN_REQUIRED", capability["credential_error"])
        self.save_personal_token("owner", "synthetic-owner-token", 0)
        self.enqueue()
        self.save_personal_token("owner", None, 1)
        result = self.run_job()
        self.assertEqual(("STALE", "AI_USER_TOKEN_REQUIRED"), (result["status"], result["error"]))
        self.assertEqual([], self.requests)

    def test_personal_token_change_during_http_prevents_late_result_publication(self):
        self.configure_personal_tokens()
        self.save_personal_token("owner", "synthetic-owner-original", 0)
        self.enqueue()
        def rotate(request):
            self.save_personal_token("owner", "synthetic-owner-replaced", 1)
        self.callback = rotate
        result = self.run_job()
        self.assertEqual(("STALE", "AI_CREDENTIAL_CHANGED"), (result["status"], result["error"]))
        self.assertEqual(1, len(self.requests))
        self.assertIsNone(self.run_job())
        with self.service() as service:
            self.assertEqual(0, WorkspaceClusteringStore(service.db, ws=service.ws).current("report")[1])

    def test_personal_gateway_401_is_terminal_and_has_no_authentication_fallback(self):
        self.configure_personal_tokens()
        self.save_personal_token("owner", "synthetic-rejected-owner-token", 0)
        self.enqueue()
        self.callback = lambda request: httpx.Response(401, text="synthetic-rejected-owner-token")
        result = self.run_job()
        self.assertEqual(("FAILED", "AI_AUTH_FAILED"), (result["status"], result["error"]))
        self.assertNotIn("synthetic-rejected-owner-token", json.dumps(result))
        self.assertIsNone(self.run_job())
        self.assertEqual(1, len(self.requests))

    def test_saved_result_is_not_recovered_after_the_personal_token_is_removed(self):
        self.configure_personal_tokens()
        self.save_personal_token("owner", "synthetic-owner-token", 0)
        self.enqueue()
        with patch.object(AIJobService, "_apply", side_effect=RuntimeError("synthetic publication interruption")):
            with self.assertRaises(RuntimeError):
                self.run_job()
        self.save_personal_token("owner", None, 1)
        result = self.run_job()
        self.assertEqual(("STALE", "AI_USER_TOKEN_REQUIRED"), (result["status"], result["error"]))
        self.assertEqual(1, len(self.requests))
