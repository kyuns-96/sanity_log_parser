"""Real PostgreSQL/API checks for the human judgment and reusable-criteria loop."""
from concurrent.futures import ThreadPoolExecutor
import copy
import uuid

from tests.http_support import TestClient

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database
from oa.sanity_workspace import SYNTHETIC_PASSWORD, WorkspaceStore, record_id, seed_workspace
from oa.sanity_workspace_review import WorkspaceReviewStore
from oa.test_workspace import api, fixture, ORIGIN
from tests.support import PgTestCase

BASE = "/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/"


class WorkspaceReviewTests(PgTestCase):
    def setUp(self):
        super().setUp()
        data = fixture()
        for item in data["items"]:
            item["rule_version"] = "approved-test-v1"
            item["records"] = [{"fields": {"mode": "M1", "object": "top/a", "check_point": "P1"}}]
        source = data["items"][0]
        for key, changes in (("same-condition", {}), ("other-mode", {"mode": "M2"}), ("counterexample", {})):
            item = copy.deepcopy(source)
            item["id"] = key
            item["records"][0]["fields"].update(changes)
            data["items"].append(item)
        missing = copy.deepcopy(source)
        missing.update(id="missing-evidence", raw_report=None, parse_status="MISSING", analysis_ready=False, records=[])
        data["items"].append(missing)
        data["dataset_sha256"] = digest({k: v for k, v in data.items() if k != "dataset_sha256"})
        with Database("oa") as db, db.transaction():
            seed_workspace(db, data)
        self.client = self.client_for("user04")

    def client_for(self, username):
        client = TestClient(api.create_app(origins=(ORIGIN,)), base_url=ORIGIN, headers={"Origin": ORIGIN}, raise_server_exceptions=False)
        self.addCleanup(client.close)
        value = client.post("/workspace/api/login", json={"username": username, "password": SYNTHETIC_PASSWORD})
        self.assertEqual(200, value.status_code, value.text)
        client.headers["X-CSRF-Token"] = value.json()["csrf"]
        return client

    def command(self, **changes):
        return {"conclusion": "PASS", "reason": "원문과 적용 모드를 확인한 허용 사례입니다.", "expected_revision": 0,
                "request_id": str(uuid.uuid4()), **changes}

    def criteria(self, **changes):
        return self.command(reusable=True, criteria="같은 검사 규칙과 M1 모드에서 허용한 조건", conditions={"rule": "CLK_0003", "mode": "M1"}, **changes)

    def save(self, report="sample-0001", command=None):
        result = self.client.post(BASE + report + "/review", json=command or self.command())
        self.assertEqual(200, result.status_code, result.text)
        return result.json()

    def propose(self, report="same-condition", **changes):
        result = self.client.post(BASE + report + "/review-proposals", json={"expected_revision": 0, "request_id": str(uuid.uuid4()), **changes})
        self.assertEqual(200, result.status_code, result.text)
        return result.json()

    def test_human_pass_on_tool_fail_is_persistent_and_preserves_source_and_por(self):
        source = self.client.get(BASE + "sample-0001").json()
        por = self.client.get("/workspace/api/blocks/project-1-cpu/versions").json()["por"]
        saved = self.save()
        self.assertEqual("PASS", saved["event"]["conclusion"])
        self.assertEqual("user-04", saved["event"]["actor"]["id"])
        top = self.client_for("user01")
        current = top.get(BASE + "sample-0001/review").json()
        self.assertEqual(("PASS", 1, 1), (current["current"]["conclusion"], current["revision"], len(current["history"])))
        self.assertEqual(source, self.client.get(BASE + "sample-0001").json())
        self.assertEqual("FAIL", source["check_status"])
        self.assertEqual(por, self.client.get("/workspace/api/blocks/project-1-cpu/versions").json()["por"])
        listing = self.client.get(BASE + "?eco=chip_finish").json()
        self.assertEqual("PASS", next(item for item in listing["items"] if item["id"] == "sample-0001")["human_review"]["conclusion"])

    def test_confirmed_criteria_are_reused_with_provenance_and_never_self_train(self):
        event = self.save(command=self.criteria())["event"]
        result = self.propose()
        self.assertEqual(("PASS", False, "CONFIRMED_CRITERIA_V1"), (result["conclusion"], result["model_called"], result["advisor"]))
        self.assertEqual([event["id"]], result["reference_ids"])
        self.assertEqual("M1", result["references"][0]["conditions"]["mode"])
        self.assertEqual("DEFER", self.propose("sample-0001", expected_revision=1)["conclusion"])
        self.assertEqual("DEFER", self.propose("other-mode")["conclusion"])

    def test_one_time_decisions_are_cases_but_not_automatically_generalized(self):
        self.save()
        self.assertEqual("DEFER", self.propose()["conclusion"])
        detail = self.client.get(BASE + "same-condition/review").json()
        self.assertEqual((1, 0), (detail["case_count"], detail["reusable_count"]))
        self.assertFalse(detail["corporate_ai_connected"])

    def test_conflicting_criteria_defer_instead_of_selecting_the_latest_pass(self):
        self.save(command=self.criteria())
        self.save("counterexample", self.criteria(conclusion="FAIL"))
        result = self.propose()
        self.assertEqual("DEFER", result["conclusion"])
        self.assertEqual(2, len(result["references"]))
        self.assertIn("충돌", result["reason"])

    def test_corrections_replace_active_criteria_and_make_old_proposals_stale(self):
        original = self.save(command=self.criteria())["event"]
        proposal = self.propose()
        changed = self.save(command=self.command(conclusion="FAIL", expected_revision=1))["event"]
        self.assertEqual(original["id"], changed["previous_event_id"])
        view = self.client.get(BASE + "same-condition/review").json()
        self.assertTrue(view["proposal"]["stale"])
        response = self.client.post(BASE + "same-condition/review", json=self.command(proposal_id=proposal["id"]))
        self.assertEqual((409, "PROPOSAL_STALE"), (response.status_code, response.json()["code"]))
        self.assertEqual("DEFER", self.propose()["conclusion"])
        self.assertEqual(2, len(self.client.get(BASE + "sample-0001/review").json()["history"]))

    def test_a_new_conflicting_case_invalidates_an_earlier_proposal(self):
        self.save(command=self.criteria())
        proposal = self.propose()
        self.save("counterexample", self.criteria(conclusion="FAIL"))
        view = self.client.get(BASE + "same-condition/review").json()
        self.assertTrue(view["proposal"]["stale"])
        response = self.client.post(BASE + "same-condition/review", json=self.command(proposal_id=proposal["id"]))
        self.assertEqual((409, "PROPOSAL_STALE"), (response.status_code, response.json()["code"]))

    def test_accepting_or_correcting_a_proposal_is_explicit_and_audited(self):
        self.save(command=self.criteria())
        proposal = self.propose()
        self.assertIsNone(self.client.get(BASE + "same-condition/review").json()["current"])
        event = self.save("same-condition", self.command(conclusion="FAIL", proposal_id=proposal["id"]))["event"]
        self.assertEqual("PASS", event["proposal_conclusion"])
        self.assertFalse(event["proposal_agreed"])
        self.assertEqual("FAIL", event["conclusion"])

    def test_missing_evidence_only_allows_defer_and_never_becomes_a_pass_case(self):
        result = self.client.post(BASE + "missing-evidence/review", json=self.command())
        self.assertEqual((409, "REVIEW_BLOCKED"), (result.status_code, result.json()["code"]))
        self.save("missing-evidence", self.command(conclusion="DEFER"))
        self.assertEqual("DEFER", self.propose("missing-evidence", expected_revision=1)["conclusion"])

    def test_request_uuid_replay_and_different_content_are_distinct(self):
        command = self.criteria()
        first = self.save(command=command)
        self.assertEqual(first, self.save(command=command))
        result = self.client.post(BASE + "sample-0001/review", json={**command, "reason": "different"})
        self.assertEqual(409, result.status_code)
        self.assertEqual(1, len(self.client.get(BASE + "sample-0001/review").json()["history"]))

    def test_concurrent_first_reviews_have_one_winner_and_no_lost_audit(self):
        def save(conclusion):
            try:
                with Database("oa") as db, db.transaction():
                    return WorkspaceReviewStore(db).save("user-04", "project-1-cpu", "project-1-cpu-post-1", "sample-0001",
                        conclusion=conclusion, reason="동시 검토 검증", criteria="", conditions={}, reusable=False,
                        compatible_versions=False, expected_revision=0, request_id=uuid.uuid4())["status"]
            except ReviewError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(save, ("PASS", "FAIL")))
        self.assertCountEqual(["REVIEW_SAVED", "REVISION_CONFLICT"], values)
        self.assertEqual(1, self.client.get(BASE + "sample-0001/review").json()["revision"])

    def test_top_foreign_scopes_forged_actor_and_csrf_are_rejected(self):
        top = self.client_for("user01")
        self.assertEqual(403, top.post(BASE + "sample-0001/review", json=self.command()).status_code)
        self.assertEqual(403, top.post(BASE + "sample-0001/review-proposals", json={"expected_revision": 0, "request_id": str(uuid.uuid4())}).status_code)
        self.assertEqual(403, self.client.get(BASE.replace("project-1-cpu", "project-1-gpu") + "sample-0001/review").status_code)
        self.assertEqual(404, self.client.get(BASE.replace("post-1", "pre-1") + "sample-0001/review").status_code)
        self.assertEqual(400, self.client.post(BASE + "sample-0001/review", json=self.command(actor="user01")).status_code)
        self.assertEqual(403, self.client.post(BASE + "sample-0001/review", json=self.command(), headers={"X-CSRF-Token": "wrong"}).status_code)

    def test_reusable_conditions_are_verified_not_free_form_matching_instructions(self):
        for changes in ({"conditions": {}}, {"conditions": {"rule": "CLK_0003"}},
                        {"conditions": {"rule": "CLK_0003", "mode": "M2"}}, {"conditions": {"rule": "CLK_0003", "mode": "M1", "shell": "anything"}},
                        {"criteria": " "}, {"conclusion": "DEFER"}):
            body = self.criteria(); body.update(changes)
            response = self.client.post(BASE + "sample-0001/review", json=body)
            self.assertEqual(400, response.status_code, response.text)

    def test_company_ai_is_explicitly_unconnected_without_a_mock_success(self):
        response = self.client.post(BASE + "sample-0001/review-proposals", json={"expected_revision": 0, "request_id": str(uuid.uuid4()), "mode": "CORPORATE"})
        self.assertEqual((503, "AI_NOT_CONFIGURED"), (response.status_code, response.json()["code"]))
        self.assertIsNone(self.client.get(BASE + "sample-0001/review").json()["proposal"])

    def test_version_reuse_requires_explicit_compatibility_and_keeps_rule_and_block_scope(self):
        with Database("oa") as db, db.transaction():
            ws = WorkspaceStore(db)
            source = ws.report("user-04", "project-1-cpu", "project-1-cpu-post-1", "sample-0001")
            for key, version, rule in (("other-version", "post-0", "approved-test-v1"),
                                       ("other-rule-version", "post-1", "incompatible-v2")):
                target = {**source, "id": key, "version_id": "project-1-cpu-" + version, "rule_version": rule,
                          "version_record_id": str(record_id("WS_VERSION", "project-1-cpu-" + version))}
                ws.put("WS_REPORT", key, target, project="project-1", block="project-1-cpu")
        endpoint = BASE.replace("post-1", "post-0") + "other-version/review-proposals"
        def propose_other_version():
            result = self.client.post(endpoint, json={"expected_revision": 0, "request_id": str(uuid.uuid4())})
            self.assertEqual(200, result.status_code, result.text)
            return result.json()
        self.save(command=self.criteria())
        self.assertEqual("DEFER", propose_other_version()["conclusion"])
        self.save(command=self.criteria(expected_revision=1, compatible_versions=True))
        self.assertEqual("PASS", propose_other_version()["conclusion"])
        self.assertEqual("DEFER", self.propose("other-rule-version")["conclusion"])
        result = self.client.post(BASE.replace("project-1-cpu", "project-3-gpu") + "sample-0008/review-proposals",
                                  json={"expected_revision": 0, "request_id": str(uuid.uuid4())})
        self.assertEqual((200, "DEFER"), (result.status_code, result.json()["conclusion"]))

    def test_changed_reviewed_source_cannot_be_silently_rejudged_or_used_for_advice(self):
        from common.sanity_db import json_value
        self.save()
        self.admin_query("UPDATE {oa} SET payload=payload || %s WHERE record_type='WS_REPORT' AND workspace_key='sample-0001'",
                         (json_value({"raw_report": "Administrator changed the immutable fixture"}),))
        for response in (
            self.client.get(BASE + "sample-0001/review"),
            self.client.post(BASE + "sample-0001/review", json=self.command(expected_revision=1)),
            self.client.post(BASE + "sample-0001/review-proposals", json={"expected_revision": 1, "request_id": str(uuid.uuid4())}),
        ):
            self.assertEqual((503, "STATE_CORRUPT"), (response.status_code, response.json()["code"]))
