"""Focused PostgreSQL/API checks for explicit conditional review exceptions."""
import copy
import uuid

from fastapi.testclient import TestClient

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database
from oa.sanity_ai_cases import CaseRetriever
from oa.sanity_workspace import SYNTHETIC_PASSWORD, WorkspaceStore, record_id, seed_workspace
from oa.sanity_workspace_review import WorkspaceReviewStore
from oa.test_workspace import ORIGIN, api, fixture
from tests.support import PgTestCase


BASE = "/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/"
EXCEPTION = {"rule": "CLOCK_CHECK", "reason": "대체 synchronizer 회로를 확인했습니다.",
             "conditions": "M1 모드에서 대체 synchronizer 회로가 활성화된 경우에만 허용합니다."}


class WorkspaceReviewExceptionTests(PgTestCase):
    def assert_shared_knowledge(self, case):
        self.assertEqual("SHARED_CONFIRMED_KNOWLEDGE", case["evidence_mode"])
        self.assertFalse(case["example_complete"])
        self.assertEqual(1, case["source_record_count"])
        forbidden = {"records", "raw", "fields", "raw_report", "record_ids", "evidence_ids", "command", "group_evidence"}

        def verify(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    verify(child)
            elif isinstance(value, list):
                for child in value:
                    verify(child)

        verify(case)

    def setUp(self):
        super().setUp()
        data = fixture()
        for item in data["items"]:
            item.update(rule_version="exception-test-v1", record_count=1)
            item["records"] = [{"id": item["id"] + "-log", "raw": "CLOCK_CHECK mode=M1 synchronizer 확인",
                "fields": {"rule": "CLOCK_CHECK", "mode": "M1", "object": "top/a", "check_point": "P1"},
                "check_status": "FAIL", "line_start": 1, "line_end": 1, "reasons": []}]
            item["raw_report"] = item["records"][0]["raw"]
        source = data["items"][0]
        same = copy.deepcopy(source)
        same.update(id="same-condition")
        same["records"][0]["id"] = "same-log"
        missing = copy.deepcopy(source)
        missing.update(id="missing-evidence", records=[], record_count=0, raw_report=None,
                       parse_status="MISSING", analysis_ready=False)
        data["items"].extend((same, missing))
        data["dataset_sha256"] = digest({key: value for key, value in data.items() if key != "dataset_sha256"})
        with Database("oa") as db, db.transaction():
            seed_workspace(db, data)
            ws = WorkspaceStore(db)
            target = copy.deepcopy(ws.report("user-04", "project-1-cpu", "project-1-cpu-post-1", "same-condition"))
            target.update(id="other-version", version_id="project-1-cpu-post-0",
                          version_record_id=str(record_id("WS_VERSION", "project-1-cpu-post-0")))
            ws.put("WS_REPORT", target["id"], target, project=target["project_id"], block=target["block_id"])
        self.client = self.client_for("user04")
        self.path = ("user-04", "project-1-cpu", "project-1-cpu-post-1")

    def client_for(self, username):
        client = TestClient(api.create_app(origins=(ORIGIN,)), base_url=ORIGIN,
                            headers={"Origin": ORIGIN}, raise_server_exceptions=False)
        self.addCleanup(client.close)
        response = client.post("/workspace/api/login", json={"username": username, "password": SYNTHETIC_PASSWORD})
        self.assertEqual(200, response.status_code, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        return client

    def command(self, **changes):
        return {"conclusion": "PASS", "reason": "현재 원문과 M1 실행 조건을 확인했습니다.",
                "expected_revision": 0, "request_id": str(uuid.uuid4()), **changes}

    def post(self, suffix, body, status=200, client=None):
        response = (client or self.client).post(BASE + suffix, json=body)
        self.assertEqual(status, response.status_code, response.text)
        return response.json()

    def detail(self, report="sample-0001", suffix="review"):
        response = self.client.get(BASE + report + "/" + suffix)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_exception_roundtrip_and_correction_preserve_history_and_tool_result(self):
        original = self.client.get(BASE + "sample-0001").json()
        padded = {key: "  " + value + " \n" for key, value in EXCEPTION.items()}
        saved = self.post("sample-0001/review", self.command(exception=padded))["event"]
        self.assertEqual(EXCEPTION, saved["exception"])
        self.assertEqual(EXCEPTION, saved["command"]["exception"])
        self.assertFalse(saved["reusable"])
        self.assertEqual(EXCEPTION, self.detail()["current"]["exception"])
        corrected = self.post("sample-0001/review", self.command(conclusion="FAIL", expected_revision=1))["event"]
        self.assertIsNone(corrected["exception"])
        history = {event["id"]: event for event in self.detail()["history"]}
        self.assertEqual(EXCEPTION, history[saved["id"]]["exception"])
        self.assertEqual(saved["id"], corrected["previous_event_id"])
        self.assertEqual(original, self.client.get(BASE + "sample-0001").json())
        self.assertEqual("FAIL", original["check_status"])

    def test_ordinary_override_is_explicit_null_and_request_replay_is_idempotent(self):
        command = self.command(compatible_versions=True)
        saved = self.post("sample-0001/review", command)
        self.assertEqual("PASS", saved["event"]["conclusion"])
        self.assertIn("exception", saved["event"])
        self.assertIsNone(saved["event"]["exception"])
        self.assertIsNone(saved["event"]["command"]["exception"])
        self.assertTrue(saved["event"]["compatible_versions"])
        self.assertFalse(saved["event"]["reusable"])
        self.assertEqual(saved, self.post("sample-0001/review", {**command, "exception": None}))
        conflict = self.post("sample-0001/review", {**command, "exception": EXCEPTION}, 409)
        self.assertEqual("REQUEST_CONFLICT", conflict["code"])
        self.assertEqual(1, len(self.detail()["history"]))

    def test_historical_missing_field_replays_without_backfilling_or_inference(self):
        command = self.command()
        saved = self.post("sample-0001/review", command)["event"]
        # This DBA-only fixture models an immutable event written by the previous release.
        self.admin_query("UPDATE {oa} SET payload=(payload - 'exception') || "
            "jsonb_build_object('command',(payload->'command') - 'exception') "
            "WHERE record_type='WS_REVIEW_EVENT' AND workspace_key=%s", (saved["id"],))
        historical = self.detail()["current"]
        self.assertNotIn("exception", historical)
        self.assertNotIn("exception", historical["command"])
        self.assertEqual(historical, self.post("sample-0001/review", command)["event"])
        self.assertEqual(historical, self.post("sample-0001/review", {**command, "exception": None})["event"])
        stored = self.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REVIEW_EVENT' "
                                  "AND workspace_key=%s", (saved["id"],)).fetchone()[0]
        self.assertEqual(historical, stored)

    def test_invalid_exception_and_conflicting_semantics_never_write_an_event(self):
        for changes, code in (({"exception": {**EXCEPTION, "reason": " "}}, "INVALID_REQUEST"),
                              ({"exception": {**EXCEPTION, "extra": True}}, "INVALID_REQUEST"),
                              ({"exception": EXCEPTION, "conclusion": "DEFER"}, "INVALID_EXCEPTION"),
                              ({"exception": EXCEPTION, "reusable": True, "criteria": "검증한 조건",
                                "conditions": {"rule": "CLK_0003", "mode": "M1"}}, "INVALID_EXCEPTION"),
                              ({"compatible_versions": True, "criteria": "검증한 조건"}, "INVALID_CRITERIA"),
                              ({"compatible_versions": True, "conditions": {"rule": "CLK_0003"}}, "INVALID_CRITERIA")):
            with self.subTest(changes=changes):
                result = self.post("sample-0001/review", self.command(**changes), 400)
                self.assertEqual(code, result["code"])
        self.assertEqual((0, []), (self.detail()["revision"], self.detail()["history"]))

    def test_global_ai_reference_needs_no_version_opt_in_and_exception_never_drives_legacy_rules(self):
        first = self.post("sample-0001/review", self.command(exception=EXCEPTION))["event"]
        proposal = self.post("same-condition/review-proposals", {
            "request_id": str(uuid.uuid4()), "expected_revision": 0})
        self.assertEqual(("DEFER", []), (proposal["conclusion"], proposal["reference_ids"]))
        with Database("oa") as db, db.transaction():
            store = WorkspaceReviewStore(db)
            same = store.context(*self.path, "same-condition")
            other = store.context("user-04", "project-1-cpu", "project-1-cpu-post-0", "other-version")
            cases = CaseRetriever(db).select(same, stage="REVIEW")["cases"]
            self.assertEqual([first["id"]], [case["id"] for case in cases])
            self.assertEqual(EXCEPTION, cases[0]["exception"])
            self.assert_shared_knowledge(cases[0])
            other_cases = CaseRetriever(db).select(other, stage="REVIEW")["cases"]
            self.assertEqual([first["id"]], [case["id"] for case in other_cases])
            self.assertFalse(other_cases[0]["compatible_versions"])
            self.assertEqual(EXCEPTION, other_cases[0]["exception"])
            self.assert_shared_knowledge(other_cases[0])
        corrected = self.post("sample-0001/review", self.command(
            exception=EXCEPTION, expected_revision=1, compatible_versions=True))["event"]
        self.assertFalse(corrected["reusable"])
        self.assertTrue(corrected["compatible_versions"])
        with Database("oa") as db, db.transaction():
            context = WorkspaceReviewStore(db).context("user-04", "project-1-cpu", "project-1-cpu-post-0", "other-version")
            cases = CaseRetriever(db).select(context, stage="REVIEW")["cases"]
            self.assertEqual([corrected["id"]], [case["id"] for case in cases])
            self.assertEqual(EXCEPTION, cases[0]["exception"])
            self.assert_shared_knowledge(cases[0])
        legacy_proposal = self.post("same-condition/review-proposals", {
            "request_id": str(uuid.uuid4()), "expected_revision": 0})
        self.assertEqual(("DEFER", []), (legacy_proposal["conclusion"], legacy_proposal["reference_ids"]))

    def test_sql_search_finds_exception_only_condition_and_returns_shared_knowledge(self):
        marker = "metastability_reference_token"
        exception = {**EXCEPTION, "reason": "검증한 대체 회로가 있습니다.",
                     "conditions": marker + "이 활성화된 경우에만 허용합니다."}
        saved = self.post("sample-0001/review", self.command(exception=exception))["event"]
        with Database("oa") as db, db.transaction():
            ws = WorkspaceStore(db)
            target = copy.deepcopy(ws.report(*self.path, "same-condition"))
            target.pop("rule", None)
            target.update(id="exception-query-report", raw_report=marker,
                          records=[{"id": "exception-query-log", "raw": marker, "fields": {}}])
            ws.put("WS_REPORT", target["id"], target, project=target["project_id"], block=target["block_id"])
            context = WorkspaceReviewStore(db).context(*self.path, target["id"])
            self.assertEqual({}, context["facts"])
            cases = CaseRetriever(db).select(context, stage="REVIEW")["cases"]
            self.assertEqual([saved["id"]], [case["id"] for case in cases])
            self.assertEqual(exception, cases[0]["exception"])
            self.assert_shared_knowledge(cases[0])
        self.post("sample-0001/review", self.command(expected_revision=1))
        with Database("oa") as db, db.transaction():
            context = WorkspaceReviewStore(db).context(*self.path, "exception-query-report")
            self.assertEqual([], CaseRetriever(db).select(context, stage="REVIEW")["cases"])

    def test_exception_does_not_bypass_permissions_csrf_or_missing_evidence(self):
        body = self.command(exception=EXCEPTION)
        self.assertEqual("FORBIDDEN", self.post("sample-0001/review", body, 403, client=self.client_for("user01"))["code"])
        response = self.client.post(BASE.replace("project-1-cpu", "project-1-gpu") + "sample-0001/review", json=body)
        self.assertEqual(403, response.status_code)
        response = self.client.post(BASE + "sample-0001/review", json=body, headers={"X-CSRF-Token": "wrong"})
        self.assertEqual(403, response.status_code)
        self.assertEqual("REVIEW_BLOCKED", self.post("missing-evidence/review", body, 409)["code"])
        self.assertEqual([], self.detail()["history"])
        self.assertEqual([], self.detail("missing-evidence")["history"])

    def test_group_exception_requires_confirmation_and_preserves_cluster_binding(self):
        draft = self.post("sample-0001/clustering/proposals", {
            "expected_revision": 0, "request_id": str(uuid.uuid4())})
        group = draft["groups"][0]
        route = "sample-0001/groups/" + group["id"] + "/review"
        self.assertEqual("REVIEW_BLOCKED", self.post(route, self.command(
            exception=EXCEPTION, expected_cluster_revision=draft["revision"]), 409)["code"])
        self.post("sample-0001/groups/" + group["id"] + "/rationale", {
            "action": "SAVE", "request_id": str(uuid.uuid4()), "expected_revision": draft["revision"],
            "reason": "원문을 확인하여 분류 근거 확정", "reviewed": True,
            "content": {"reason": "동일 회로와 모드의 로그를 함께 검토합니다.",
                        "evidence_ids": group["record_ids"], "reference_groups": []}})
        current = self.detail(suffix="clustering")
        cluster = self.post("sample-0001/clustering", {"action": "CONFIRM", "request_id": str(uuid.uuid4()),
            "expected_revision": current["revision"], "reason": "분류와 근거를 확인했습니다."})
        saved = self.post(route + "?compact=true", self.command(
            exception=EXCEPTION, expected_cluster_revision=cluster["revision"]))["event"]
        self.assertEqual((group["id"], cluster["id"], EXCEPTION),
                         (saved["group_id"], saved["cluster_event_id"], saved["exception"]))
        self.assertNotIn("command", saved)
        detail = self.detail(suffix="groups/" + group["id"] + "/review")
        self.assertEqual(EXCEPTION, detail["current"]["command"]["exception"])
        self.assertEqual("REVISION_CONFLICT", self.post(route, self.command(
            exception=EXCEPTION, expected_revision=1, expected_cluster_revision=cluster["revision"] - 1), 409)["code"])
        self.assertEqual(1, len(self.detail(suffix="groups/" + group["id"] + "/review")["history"]))

    def test_corrupt_stored_exception_fails_closed_in_view_and_ai_retrieval(self):
        saved = self.post("sample-0001/review", self.command(exception=EXCEPTION))["event"]
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,ARRAY['exception','reason'],%s::jsonb) "
                         "WHERE record_type='WS_REVIEW_EVENT' AND workspace_key=%s", ('" "', saved["id"]))
        response = self.client.get(BASE + "sample-0001/review")
        self.assertEqual((503, "STATE_CORRUPT"), (response.status_code, response.json()["code"]))
        with Database("oa") as db, db.transaction():
            context = WorkspaceReviewStore(db).context(*self.path, "same-condition")
            with self.assertRaises(ReviewError) as caught:
                CaseRetriever(db).select(context, stage="REVIEW")
            self.assertEqual("STATE_CORRUPT", caught.exception.code)
