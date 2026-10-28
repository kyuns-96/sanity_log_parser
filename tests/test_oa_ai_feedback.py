"""Production-authorized A→B feedback cycle with real HTTP adapter contracts.

All identities, reports, criteria and model replies are synthetic. MockTransport
replaces only network I/O; durable jobs, retrieval, prompts, response validation,
human review and PostgreSQL persistence run through their production code.
This proves the feedback plumbing, not a real model's semantic accuracy.
"""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_ai_knowledge import shared_case
from common.sanity_db import Database, json_value, stable_uuid
from oa.sanity_access import AccessStore, access_lock
from oa.sanity_ai_criteria import load_criteria
from oa.sanity_ai_cases import CaseRetriever
from oa.sanity_ai_jobs import AIJobService, run_once
from oa.sanity_ai_model import OAAdvisor
from oa.sanity_auth import Actor
from oa.sanity_production_workspace import ProductionWorkspaceStore
from oa.sanity_workspace import record_id
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from tests.support import PgTestCase, ROOT


ADMIN = Actor("feedback.test.admin", "Synthetic administrator", "sso")
OWNER = Actor("feedback.test.owner", "Synthetic reviewer", "sso")
PROJECT, BLOCK, VERSION = "feedback.project", "feedback.block", "feedback.version"
SCOPE = {"project_id": PROJECT, "block_id": BLOCK, "item_id": "clock_check", "family": "GCA",
         "parser": "synthetic-clock-parser-v1", "rule_version": "clock-policy-v1", "layout": "POST"}
HUMAN_GROUP_REASON = "동일한 파생 클록 경로 두 건은 승인된 예외이며, 실제 클록 누락 경로와 분리해서 검토한다."
HUMAN_PASS_REASON = "파생 클록 경로의 예외 조건을 사람이 확인했다. 실제 클록 누락과 달라 PASS로 정정한다."
HUMAN_FAIL_REASON = "이 경로는 파생 클록 예외가 없고 실제 클록 정의가 누락되어 FAIL이다."


class OAFeedbackCycleTests(PgTestCase):
    def assert_shared_knowledge(self, case, *, source_count):
        self.assertEqual("SHARED_CONFIRMED_KNOWLEDGE", case["evidence_mode"])
        self.assertFalse(case["example_complete"])
        self.assertEqual(source_count, case["source_record_count"])
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
        for name in ("008_submissions.sql", "009_sso_identity.sql"):
            self.admin.execute((ROOT / "db" / name).read_text().replace("public.", self.schema + "."))
        timestamp = "2026-10-22T00:00:00.000000Z"
        for actor in (ADMIN, OWNER):
            self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload,state) "
                "VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",
                (stable_uuid("sso-identity-v1", "SSO_IDENTITY", actor.user_id), actor.user_id,
                 json_value({"issuer": "https://synthetic.example.test/idp", "subject": actor.user_id,
                             "display_name": actor.reviewer, "first_seen_at": timestamp}),
                 json_value({"enabled": True, "last_seen_at": timestamp})))
        with self.store(management=True) as ws:
            access = ws.authorization
            access.create_project(ADMIN, uuid4(), PROJECT, "Synthetic feedback project")
            access.create_block(ADMIN, uuid4(), PROJECT, BLOCK, "Synthetic feedback block")
            request = uuid4()
            requested = access.request(OWNER, request, "OWNER", PROJECT, BLOCK, "Synthetic feedback assignment")
            access.decide(ADMIN, request, uuid4(), "APPROVE", "Synthetic approval", requested["revision"])
            pre = {"id": VERSION + ".pre", "project_id": PROJECT, "block_id": BLOCK,
                   "net_ver": "N1", "revision": "R1", "layout": "PRE", "source_pre_id": None, "ecos": [None]}
            post = {**pre, "id": VERSION, "layout": "POST", "source_pre_id": pre["id"], "ecos": ["eco01", "eco02"]}
            ws.put("WS_VERSION", pre["id"], pre, project=PROJECT, block=BLOCK)
            ws.put("WS_VERSION", VERSION, post, project=PROJECT, block=BLOCK)
            self.originals = {}
            for suffix, eco in (("A", "eco01"), ("B", "eco02")):
                report_id = "report-" + suffix
                records = [{"id": suffix + str(index), "check_status": "FAIL", "line_start": index,
                    "line_end": index, "raw": "clock definition missing " + kind,
                    "fields": {"object": "top/" + kind, "code": "CLK_MISSING", "severity": "ERROR",
                               "check_point": "clock_definition", "mode": "FUNCTIONAL"}}
                    for index, kind in enumerate(("derived_a", "derived_b", "root_missing"), 1)]
                report = {**{key: value for key, value in SCOPE.items() if key != "layout"},
                    "id": report_id, "version_id": VERSION, "version_record_id": str(record_id("WS_VERSION", VERSION)),
                    "eco": eco, "rule": "CLK_MISSING", "check_status": "FAIL", "parse_status": "COMPLETE",
                    "analysis_ready": True, "record_count": len(records), "records": records, "blockers": [],
                    "raw_report": "\n".join(record["raw"] for record in records)}
                ws.put("WS_REPORT", report_id, report, project=PROJECT, block=BLOCK)
                self.originals[report_id] = copy.deepcopy(report)
        directory = tempfile.TemporaryDirectory(prefix="synthetic-feedback-criteria-")
        self.addCleanup(directory.cleanup)
        criteria_path = Path(directory.name) / "criteria.json"
        self.criterion = {"id": "synthetic-clock-definition", "version": "1", "scope": SCOPE.copy(),
                          "text": "필수 클록 정의 누락은 FAIL로 제안한다. 확인된 파생 클록 예외는 근거를 비교한다."}
        criteria_path.write_bytes(canonical_json({"schema_version": 1, "criteria": [self.criterion]}))
        environment = patch.dict(os.environ, {"SANITY_OA_AI_CRITERIA_FILE": str(criteria_path)})
        environment.start()
        self.addCleanup(environment.stop)
        self.packets, self.bodies = [], []
        self.advisor = OAAdvisor({"SANITY_OA_AI_ENABLED": "true",
            "SANITY_OA_AI_URL": "https://synthetic-model.invalid/v1/chat/completions",
            "SANITY_OA_AI_TOKEN": "synthetic-feedback-credential", "SANITY_OA_AI_MODEL": "synthetic-feedback-v1",
            "SANITY_OA_AI_JSON_MODE": "true"}, transport=httpx.MockTransport(self.model))

    def workspace(self, db):
        return ProductionWorkspaceStore(db, AccessStore(db, ADMIN.user_id))

    @contextmanager
    def store(self, *, management=False):
        with Database("oa") as db, db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
            access_lock(db, exclusive=management)
            yield self.workspace(db)

    def model(self, request):
        body = json.loads(request.content)
        packet = json.loads(body["messages"][1]["content"])
        self.bodies.append(body)
        self.packets.append(packet)
        context, cases = packet["context"], packet["cases"]
        report_id = context["report"]["id"]
        if "identity_fields" in packet:
            if report_id == "report-A":
                groups = [{"id": "ai-all", "label": "클록 정의 누락", "record_ids": ["A1", "A2", "A3"]}]
                refs = []
            else:
                groups = [{"id": "b-derived", "label": "파생 클록 예외", "record_ids": ["B1", "B2"]},
                          {"id": "b-missing", "label": "실제 클록 누락", "record_ids": ["B3"]}]
                refs = [cases[0]["id"]]
            rationales = []
            for index, group in enumerate(groups):
                references = [] if not refs else [{"case_id": refs[0],
                    "group_id": "human-derived" if index == 0 else "human-missing",
                    "reason": "사람이 확정한 파생 클록 예외와 실제 누락의 구분을 이번 로그와 비교했다."}]
                rationales.append({"group_id": group["id"], "reason": "클록 정의와 경로 조건을 함께 비교한 분류 제안이다.",
                                   "evidence_ids": group["record_ids"], "reference_groups": references})
            reply = {"groups": groups, "reason": "전체 로그의 클록 정의와 경로 조건을 비교했다.",
                     "reference_ids": refs, "group_rationales": rationales}
        else:
            reply = {"schema_version": 2, "conclusion": "FAIL" if report_id == "report-A" else "PASS",
                "reason": ("현재 로그의 클록 정의 누락을 제공된 기준과 비교하여 FAIL을 제안한다." if report_id == "report-A" else
                           "현재 파생 클록 경로는 사람이 정정한 허용 사례와 같으며, 실제 누락 FAIL 반례와 조건이 달라 PASS를 제안한다."),
                "evidence_ids": [record["id"] for record in context["group_evidence"]],
                "reference_ids": [case["id"] for case in cases], "criterion_ids": [packet["criteria"][0]["id"]]}
        return httpx.Response(200, json={"model": "synthetic-feedback-v1", "choices": [{"finish_reason": "stop",
            "message": {"role": "assistant", "content": canonical_json(reply).decode("utf-8")}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

    def run_job(self, report_id, *, revision=0, group_id=None, cluster_revision=None):
        request_id = str(uuid4())
        with self.store() as ws:
            queued = AIJobService(ws.db, ws, advisor=self.advisor).enqueue(
                OWNER.user_id, BLOCK, VERSION, report_id, expected_revision=revision, request_id=request_id,
                group_id=group_id, expected_cluster_revision=cluster_revision)
            self.assertEqual("PENDING", queued["status"])
            self.assertFalse(queued["model_called"])
        result = run_once(lambda: Database("oa"), self.workspace, self.advisor)
        self.assertEqual("COMPLETED", result["status"], result)
        self.assertTrue(result["model_called"])
        self.assertEqual(request_id, result["event_id"])
        return request_id

    def test_production_http_routes_use_sso_grants_jobs_and_separate_human_confirmation(self):
        from fastapi.testclient import TestClient
        from oa.dashboard_api import create_app
        from oa.sanity_auth import AuthError, LocalUser, SSOIdentityProvider, UserDirectory, VerifiedIdentity

        class Provider(SSOIdentityProvider):
            def authenticate(self, request):
                if request.cookies.get("synthetic_session") != "synthetic-owner-session":
                    raise AuthError("UNAUTHORIZED")
                return VerifiedIdentity("https://synthetic.example.test/idp", OWNER.user_id)

        class Directory(UserDirectory):
            def resolve(self, identity):
                return LocalUser(OWNER.user_id, OWNER.reviewer) if identity.subject == OWNER.user_id else None

        app = create_app(auth_mode="sso", identity_provider=Provider(), user_directory=Directory())
        base = f"/api/workbench/blocks/{BLOCK}/versions/{VERSION}/reports/report-A"
        headers = {"X-Sanity-Request": "1", "Origin": "http://127.0.0.1", "Sec-Fetch-Site": "same-origin"}
        factory = lambda scope: AIJobService(scope.db, scope.ws, advisor=self.advisor)
        with patch.dict(os.environ, {"SANITY_ADMIN_USER_ID": ADMIN.user_id}), \
                patch("oa.ai_workbench_api.jobs", side_effect=factory), \
                TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
            self.assertEqual(401, client.get("/api/workbench/session").status_code)
            client.cookies.set("synthetic_session", "synthetic-owner-session")
            session = client.get("/api/workbench/session")
            self.assertEqual(200, session.status_code, session.text)
            self.assertEqual("PRODUCTION_SSO", session.json()["environment"])
            view = client.get(base + "/workbench")
            self.assertEqual(200, view.status_code, view.text)
            self.assertTrue(view.json()["ai"]["available"])
            request = {"request_id": str(uuid4()), "expected_revision": 0}
            self.assertEqual(403, client.post(base + "/clustering/ai", json=request).status_code)
            accepted = client.post(base + "/clustering/ai", json=request, headers=headers)
            self.assertEqual(202, accepted.status_code, accepted.text)
            self.assertEqual("PENDING", accepted.json()["status"])
            self.assertEqual([], self.packets)
            result = run_once(lambda: Database("oa"), self.workspace, self.advisor)
            self.assertEqual("COMPLETED", result["status"], result)
            view = client.get(base + "/workbench").json()
            self.assertEqual("COMPLETED", view["ai"]["job"]["status"])
            self.assertFalse(view["current"]["confirmed"])
            groups = client.get(base + "/clustering/groups?expected_revision=1")
            self.assertEqual(200, groups.status_code, groups.text)
            self.assertEqual(3, groups.json()["items"][0]["record_count"])
            confirmed = client.post(base + "/clustering?compact=true", headers=headers, json={
                "action": "CONFIRM", "expected_revision": 1, "request_id": str(uuid4()),
                "reason": "사람이 전체 로그와 분류 근거를 검토했다."})
            self.assertEqual(200, confirmed.status_code, confirmed.text)
            review_base = base + "/groups/ai-all/review"
            requested = client.post(review_base + "/ai", headers=headers, json={
                "expected_revision": 0, "expected_cluster_revision": 2, "request_id": str(uuid4())})
            self.assertEqual(202, requested.status_code, requested.text)
            self.assertEqual("COMPLETED", run_once(lambda: Database("oa"), self.workspace, self.advisor)["status"])
            reviewed = client.get(review_base + "?compact=true")
            self.assertEqual(200, reviewed.status_code, reviewed.text)
            proposal = reviewed.json()["proposal"]
            self.assertIsNone(reviewed.json()["current"])
            self.assertEqual(["A1", "A2", "A3"], proposal["evidence_ids"])
            saved = client.post(review_base + "?compact=true", headers=headers, json={
                "conclusion": "PASS", "reason": "사람이 예외를 확인하고 AI 제안을 정정했다.",
                "expected_revision": 0, "expected_cluster_revision": 2,
                "request_id": str(uuid4()), "proposal_id": proposal["id"]})
            self.assertEqual(200, saved.status_code, saved.text)
            self.assertFalse(saved.json()["event"]["proposal_agreed"])
            self.assertEqual(404, client.get("/workspace/api/session").status_code)
            self.assertEqual(404, client.post(base + "/clustering/proposals", headers=headers, json={}).status_code)
            self.admin_query("UPDATE {oa} SET state=jsonb_set(state,'{{enabled}}','false'::jsonb) "
                             "WHERE record_type='SSO_IDENTITY' AND workspace_key=%s", (OWNER.user_id,))
            self.assertEqual(403, client.get(base + "/workbench").status_code)
        self.assertEqual(2, len(self.packets))

    def test_revoked_source_grant_keeps_global_knowledge_but_not_original_report_access(self):
        project, block, version = "feedback.shared.project", "feedback.shared.block", "feedback.shared.version"
        report_id, sentinel = "shared-source-report", "PRIVATE-SOURCE-RAW-SENTINEL-47c8"
        request_id = uuid4()
        with self.store(management=True) as ws:
            access = ws.authorization
            access.create_project(ADMIN, uuid4(), project, "Synthetic shared knowledge project")
            access.create_block(ADMIN, uuid4(), project, block, "Synthetic source block")
            requested = access.request(OWNER, request_id, "OWNER", project, block, "Synthetic source review assignment")
            access.decide(ADMIN, request_id, uuid4(), "APPROVE", "Synthetic source approval", requested["revision"])
            source_version = {"id": version, "project_id": project, "block_id": block,
                              "net_ver": "N9", "revision": "R9", "layout": "POST",
                              "source_pre_id": None, "ecos": ["eco09"]}
            ws.put("WS_VERSION", version, source_version, project=project, block=block)
            source = copy.deepcopy(self.originals["report-A"])
            source.update(id=report_id, project_id=project, block_id=block, version_id=version,
                          version_record_id=str(record_id("WS_VERSION", version)), eco="eco09",
                          record_count=1, raw_report=sentinel)
            source["records"] = [{**source["records"][0], "id": "C1", "raw": sentinel,
                                  "fields": {"object": "PRIVATE-SOURCE-FIELD-47c8", "mode": "FUNCTIONAL",
                                             "check_point": "clock_definition"}}]
            ws.put("WS_REPORT", report_id, source, project=project, block=block)
        with self.store() as ws:
            clusters = WorkspaceClusteringStore(ws.db, ws=ws)
            reason = "클록 정의와 파생 경로를 사람이 확인한 확정 분류 사례입니다."
            draft = clusters.apply_ai(OWNER.user_id, block, version, report_id,
                expected_revision=0, request_id=str(uuid4()), cases=[], retrieval={"selected": []},
                metadata={"provider": "OA_OPENAI_COMPATIBLE", "model_called": True,
                          "model": "synthetic-only", "prompt_version": "synthetic-receipt-v1", "input_sha256": "a" * 64},
                advice={"groups": [{"id": "shared-clock", "label": "사람이 확인한 클록 분류", "record_ids": ["C1"]}],
                        "reason": reason, "reference_ids": [], "group_rationales": [{
                            "group_id": "shared-clock", "reason": reason,
                            "evidence_ids": ["C1"], "reference_groups": []}]})
            confirmed = clusters.change(OWNER.user_id, block, version, report_id, action="CONFIRM",
                expected_revision=draft["revision"], request_id=str(uuid4()), reason=reason)
            self.assertEqual(report_id, ws.report(OWNER.user_id, block, version, report_id)["id"])
        with self.store(management=True) as ws:
            access = ws.authorization
            snapshot = access.admin_snapshot(ADMIN)
            grant = next(grant for grant in snapshot["grants"] if grant["block_id"] == block)
            access.revoke(ADMIN, grant["grant_id"], uuid4(), "Synthetic source grant revocation", snapshot["revision"])
        with self.store() as ws:
            with self.assertRaises(ReviewError) as error:
                ws.report(OWNER.user_id, block, version, report_id)
            self.assertEqual("RESOURCE_NOT_FOUND", error.exception.code)
            # The original target grant is still active. Only its complete logs
            # are evidence; the other block contributes confirmed knowledge.
            context = WorkspaceClusteringStore(ws.db, ws=ws).context(OWNER.user_id, BLOCK, VERSION, "report-A")
            self.assertTrue(context["can_review"])
            self.assertEqual(self.originals["report-A"]["records"], context["report"]["records"])
            retriever = CaseRetriever(ws.db, ws)
            cases = retriever.select(context, stage="CLUSTER")["cases"]
            self.assertEqual([confirmed["id"]], [case["id"] for case in cases])
            self.assert_shared_knowledge(cases[0], source_count=1)
            self.assertEqual((project, block, version), (cases[0]["scope"]["project_id"],
                             cases[0]["scope"]["block_id"], cases[0]["version_id"]))
            self.assertTrue(retriever.revalidate(context, cases, stage="CLUSTER"))
            self.advisor.cluster(context, cases, ["object", "check_point", "mode"])
        self.assertEqual(1, len(self.packets))
        self.assertEqual(cases, self.packets[0]["cases"])
        self.assert_shared_knowledge(self.packets[0]["cases"][0], source_count=1)
        self.assertNotIn(sentinel, canonical_json(self.packets[0]).decode())
        self.assertNotIn("PRIVATE-SOURCE-FIELD-47c8", canonical_json(self.packets[0]).decode())

    def test_human_corrections_and_counterexamples_reach_the_next_eco_model_calls(self):
        # First report: the actual adapter proposes a single group with no cases.
        initial_id = self.run_job("report-A")
        self.assertEqual([], self.packets[0]["cases"])
        self.assertEqual(self.originals["report-A"]["records"], self.packets[0]["context"]["report"]["records"])
        with self.store() as ws:
            clusters = WorkspaceClusteringStore(ws.db, ws=ws)
            first, revision = clusters.current("report-A")
            self.assertTrue(first["model_called"])
            self.assertFalse(first["confirmed"])
            self.assertEqual("OA_OPENAI_COMPATIBLE", first["advisor"])
            self.assertEqual(digest(self.bodies[0]), first["model_metadata"]["input_sha256"])
            self.assertEqual("oa-semantic-clustering-v3", first["model_metadata"]["prompt_version"])
            groups = [{"id": "human-derived", "label": "파생 클록 예외", "record_ids": ["A1", "A2"]},
                      {"id": "human-missing", "label": "실제 클록 누락", "record_ids": ["A3"]}]
            edited = clusters.change(OWNER.user_id, BLOCK, VERSION, "report-A", action="EDIT",
                expected_revision=revision, request_id=str(uuid4()), groups=groups, reason=HUMAN_GROUP_REASON)
            for group in groups:
                edited = clusters.change(OWNER.user_id, BLOCK, VERSION, "report-A", action="EDIT",
                    expected_revision=edited["revision"], request_id=str(uuid4()), reason=HUMAN_GROUP_REASON,
                    rationale_operation={"kind": "SAVE", "group_id": group["id"], "reviewed": True,
                        "content": {"reason": HUMAN_GROUP_REASON + " " + group["label"],
                                    "evidence_ids": group["record_ids"], "reference_groups": []}})
            confirmed = clusters.change(OWNER.user_id, BLOCK, VERSION, "report-A", action="CONFIRM",
                expected_revision=edited["revision"], request_id=str(uuid4()), reason=HUMAN_GROUP_REASON,
                reusable=False, compatible_versions=False)
            self.assertTrue(confirmed["confirmed"])
            self.assertFalse(confirmed["reusable"])
            self.assertEqual(initial_id, confirmed["initial_ai_event_id"])
            self.assertEqual(first["model_metadata"], confirmed["model_metadata"])

        # Cold-start review cites current group evidence and an approved criterion.
        review_id = self.run_job("report-A", group_id="human-derived", cluster_revision=confirmed["revision"])
        first_review_packet = self.packets[1]
        self.assertEqual([], first_review_packet["cases"])
        self.assertEqual([self.criterion], first_review_packet["criteria"])
        self.assertEqual(["A1", "A2"], [row["id"] for row in first_review_packet["context"]["group_evidence"]])
        self.assertNotIn("records", first_review_packet["context"]["report"])
        with self.store() as ws:
            reviews = WorkspaceGroupReviewStore(ws.db, "human-derived", confirmed["revision"], ws=ws)
            self.assertEqual((None, 0), reviews.current("report-A"))
            proposal = reviews.row("WS_GROUP_REVIEW_PROPOSAL", review_id)["payload"]
            self.assertEqual("FAIL", proposal["conclusion"])
            self.assertTrue(proposal["model_called"])
            self.assertEqual(["A1", "A2"], proposal["evidence_ids"])
            self.assertEqual([], proposal["reference_ids"])
            corrected = reviews.save(OWNER.user_id, BLOCK, VERSION, "report-A", conclusion="PASS",
                reason=HUMAN_PASS_REASON, criteria="", conditions={}, reusable=False, compatible_versions=False,
                expected_revision=0, request_id=str(uuid4()), proposal_id=review_id)["event"]
            self.assertFalse(corrected["proposal_agreed"])
            self.assertEqual("FAIL", corrected["proposal_conclusion"])
            self.assertEqual("PASS", corrected["conclusion"])
            self.assertEqual("FAIL", corrected["ai_proposal"]["conclusion"])
            self.assertEqual(proposal["reason"], corrected["ai_proposal"]["reason"])
            self.assertFalse(corrected["reusable"])
            negative = WorkspaceGroupReviewStore(ws.db, "human-missing", confirmed["revision"], ws=ws).save(
                OWNER.user_id, BLOCK, VERSION, "report-A", conclusion="FAIL", reason=HUMAN_FAIL_REASON,
                criteria="", conditions={}, reusable=False, compatible_versions=False,
                expected_revision=0, request_id=str(uuid4()))["event"]

        # Next ECO: confirmed human knowledge is shared; the current target's
        # complete records remain evidence, while prior raw sources stay private.
        self.run_job("report-B")
        b_cluster_packet = self.packets[2]
        self.assertEqual([confirmed["id"]], [case["id"] for case in b_cluster_packet["cases"]])
        case = b_cluster_packet["cases"][0]
        expected_knowledge = shared_case(confirmed)
        self.assertEqual(expected_knowledge["groups"], case["groups"])
        self.assertEqual(expected_knowledge["rationales"], case["rationales"])
        self.assertEqual(confirmed["scope"], case["scope"])
        self.assertEqual([2, 1], [group["record_count"] for group in case["groups"]])
        self.assert_shared_knowledge(case, source_count=3)
        self.assertEqual(self.originals["report-B"]["records"], b_cluster_packet["context"]["report"]["records"])
        self.assertEqual({"current": "eco02", "case": "eco01"}, case["retrieval_comparison"]["eco"])
        self.assertIn(HUMAN_GROUP_REASON, canonical_json(case).decode("utf-8"))
        self.assertFalse(case["reusable"])
        with self.store() as ws:
            clusters = WorkspaceClusteringStore(ws.db, ws=ws)
            proposal, _ = clusters.current("report-B")
            self.assertFalse(proposal["confirmed"])
            self.assertEqual([confirmed["id"]], [row["id"] for row in proposal["references"]])
            confirmed_b = clusters.change(OWNER.user_id, BLOCK, VERSION, "report-B", action="CONFIRM",
                expected_revision=proposal["revision"], request_id=str(uuid4()), reason="두 경로의 차이를 원문에서 확인",
                reusable=False, compatible_versions=False)

        self.run_job("report-B", group_id="b-derived", cluster_revision=confirmed_b["revision"])
        b_review_packet = self.packets[3]
        cases = {case["id"]: case for case in b_review_packet["cases"]}
        self.assertEqual({corrected["id"], negative["id"]}, set(cases))
        self.assertEqual(HUMAN_PASS_REASON, cases[corrected["id"]]["reason"])
        # The superseded AI verdict remains in the source's audit event above;
        # shared examples carry the final human outcome and confirmed reason.
        self.assertEqual("PASS", cases[corrected["id"]]["conclusion"])
        self.assertEqual("FAIL", cases[corrected["id"]]["proposal_conclusion"])
        self.assertFalse(cases[corrected["id"]]["proposal_agreed"])
        self.assertEqual({key: corrected["ai_proposal"][key] for key in ("id", "conclusion")},
                         cases[corrected["id"]]["ai_proposal"])
        self.assertEqual(HUMAN_FAIL_REASON, cases[negative["id"]]["reason"])
        self.assertEqual({"PASS", "FAIL"}, {case["conclusion"] for case in cases.values()})
        self.assert_shared_knowledge(cases[corrected["id"]], source_count=3)
        self.assert_shared_knowledge(cases[negative["id"]], source_count=3)
        self.assertEqual(2, cases[corrected["id"]]["group_snapshot"]["record_count"])
        self.assertEqual(1, cases[negative["id"]]["group_snapshot"]["record_count"])
        self.assertEqual(shared_case(corrected)["group_snapshot"], cases[corrected["id"]]["group_snapshot"])
        self.assertEqual(shared_case(negative)["group_snapshot"], cases[negative["id"]]["group_snapshot"])
        self.assertEqual(["B1", "B2"], [row["id"] for row in b_review_packet["context"]["group_evidence"]])
        self.assertTrue(all(case["report_id"] != "report-B" for case in cases.values()))
        self.assertTrue(all(not case["reusable"] for case in cases.values()))
        with self.store() as ws:
            review = WorkspaceGroupReviewStore(ws.db, "b-derived", confirmed_b["revision"], ws=ws)
            self.assertEqual((None, 0), review.current("report-B"))
            proposal = review.view(OWNER.user_id, BLOCK, VERSION, "report-B")["proposal"]
            self.assertEqual("PASS", proposal["conclusion"])
            self.assertEqual({corrected["id"], negative["id"]}, set(proposal["reference_ids"]))
            self.assertEqual(["B1", "B2"], proposal["evidence_ids"])
            self.assertEqual("oa-direct-evidence-review-v5", proposal["model_metadata"]["prompt_version"])
            for report_id, original in self.originals.items():
                self.assertEqual(original, ws.report(OWNER.user_id, BLOCK, VERSION, report_id))
        self.assertEqual(4, len(self.packets))
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type IN ('WS_USER','WS_SESSION')").fetchone()[0])
