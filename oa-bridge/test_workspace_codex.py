"""No network calls: persistence/ACL checks for externally generated mock advice."""
import copy
import json
import uuid

from sanity_codex_mock import CodexMockClusteringAdvisor, fixture_report
from sanity_common import ReviewError, digest
from sanity_db import Database
from sanity_workspace import seed_workspace
from sanity_workspace_clustering import WorkspaceClusteringStore
from test_workspace import fixture, api, ORIGIN
from tests_support import PgTestCase
from fastapi.testclient import TestClient

FIELDS = ["check_point", "mode", "object"]


def answer(context):
    ids = [record["id"] for record in context["report"]["records"]]
    value = {"groups": [{"id": "first", "label": "First test category", "record_ids": ids[:4]},
                       {"id": "second", "label": "Second test category", "record_ids": ids[4:]}],
            "reason": "Simulated transport for DB validation", "reference_ids": []}
    value["group_rationales"] = [{"group_id": group["id"], "reason": group["label"] + ": 原文の共通パターンを確認するための合成試験の根拠です。",
        "evidence_ids": group["record_ids"][:2], "reference_groups": []} for group in value["groups"]]
    return value


def events(value):
    return (json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}})+"\n"+
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})).encode()


class WorkspaceCodexTests(PgTestCase):
    def setUp(self):
        super().setUp()
        data = fixture(); data["items"] = [fixture_report(name) for name in
            ("codex-mock-source", "codex-mock-control", "codex-mock-feedback")]
        data["dataset_sha256"] = digest({key: value for key, value in data.items() if key != "dataset_sha256"})
        with Database("oa") as db, db.transaction(): seed_workspace(db, data)

    def inputs(self, name):
        with Database("oa") as db, db.transaction(readonly=True):
            store = WorkspaceClusteringStore(db)
            context = store.context("user-04", "project-1-cpu", "project-1-cpu-post-1", name, write=True)
            return context, store.catalog(context)

    def receipt(self, name="codex-mock-source"):
        context, cases = self.inputs(name)
        advisor = CodexMockClusteringAdvisor(enabled=True, executable="/unused/codex", transport=lambda *_: events(answer(context)))
        advisor.propose(context, cases, FIELDS)
        return advisor.receipt

    def change(self, name="codex-mock-source", **args):
        with Database("oa") as db, db.transaction():
            return WorkspaceClusteringStore(db).change("user-04", "project-1-cpu", "project-1-cpu-post-1", name, **args)

    def propose(self, receipt=None, request_id=None, name="codex-mock-source"):
        return self.change(name, action="PROPOSE", mode="MOCK_CODEX", expected_revision=0,
            request_id=request_id or str(uuid.uuid4()), identity_fields=FIELDS,
            mock_receipt=receipt if receipt is not None else self.receipt(name))

    def test_model_names_usage_and_uuid_retries_are_persisted_without_another_model_call(self):
        receipt = self.receipt(); rid = str(uuid.uuid4())
        event = self.propose(receipt, rid)
        self.assertEqual((True, "gpt-6-luna", "CODEX_SYNTHETIC_FEEDBACK_V1"),
            (event["model_called"], event["model_metadata"]["model"], event["advisor"]))
        self.assertFalse(event["confirmed"])
        self.assertEqual(event, self.propose(receipt, rid))
        changed = copy.deepcopy(receipt); changed["advice"]["groups"][0]["label"] = "Changed payload"
        with self.assertRaises(ReviewError) as raised: self.propose(changed, rid)
        self.assertEqual("REQUEST_CONFLICT", raised.exception.code)

    def test_default_confirmation_is_a_case_for_the_next_ai_input_and_keeps_ai_provenance(self):
        original, _ = self.inputs("codex-mock-source")
        self.propose()
        self.change(action="EDIT", expected_revision=1, request_id=str(uuid.uuid4()), reason="모든 제약 설정 누락을 한 그룹으로 검토",
                    groups=[{"id": "human", "label": "사용자가 확정한 이름", "record_ids": [row["id"] for row in original["report"]["records"]]}])
        self.change(action="EDIT", expected_revision=2, request_id=str(uuid.uuid4()), reason="새로 병합한 그룹의 분류 근거 작성",
                    rationale_operation={"kind": "SAVE", "group_id": "human", "reviewed": True,
                        "content": {"reason": "클록 및 입력 지연 설정의 누락을 제약 설정 누락으로 통합하며, 폭 불일치와는 구분합니다.",
                                    "evidence_ids": [original["report"]["records"][0]["id"]], "reference_groups": []}})
        confirmed = self.change(action="CONFIRM", expected_revision=3, request_id=str(uuid.uuid4()), reason="분류 구성과 이름 확인")
        self.assertFalse(confirmed["reusable"])
        self.assertFalse(confirmed["model_called"])
        self.assertEqual("gpt-6-luna", confirmed["model_metadata"]["model"])
        target, cases = self.inputs("codex-mock-feedback")
        self.assertEqual([confirmed["id"]], [case["id"] for case in cases])
        output = answer(target); output["reference_ids"] = [confirmed["id"]]
        output["group_rationales"][0]["reference_groups"] = [{"case_id": confirmed["id"], "group_id": "human", "reason": "사람이 확정한 설정 누락의 분류 의도 참고"}]
        advisor = CodexMockClusteringAdvisor(enabled=True, executable="/unused/codex", transport=lambda *_: events(output))
        advisor.propose(target, cases, FIELDS)
        event = self.propose(advisor.receipt, name="codex-mock-feedback")
        self.assertEqual([confirmed["id"]], [ref["id"] for ref in event["references"]])
        self.assertEqual(original["source_sha256"], self.inputs("codex-mock-source")[0]["source_sha256"])

    def test_new_or_withdrawn_case_catalog_rejects_late_model_result(self):
        late = self.receipt("codex-mock-feedback")
        self.propose()
        self.change(action="CONFIRM", expected_revision=1, request_id=str(uuid.uuid4()), reason="確定例")
        with self.assertRaises(ReviewError) as raised: self.propose(late, name="codex-mock-feedback")
        self.assertEqual("PROPOSAL_STALE", raised.exception.code)
        with Database("oa") as db, db.transaction(readonly=True):
            self.assertEqual((None, 0), WorkspaceClusteringStore(db).current("codex-mock-feedback"))

    def test_http_cannot_enable_external_calls_and_corporate_stays_unconnected(self):
        from sanity_workspace import SYNTHETIC_PASSWORD
        with TestClient(api.create_app(origins=(ORIGIN,)), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
            login = client.post("/workspace/api/login", json={"username": "user04", "password": SYNTHETIC_PASSWORD})
            client.headers["X-CSRF-Token"] = login.json()["csrf"]
            url = "/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/codex-mock-source/clustering/proposals"
            response = client.post(url, json={"expected_revision": 0, "request_id": str(uuid.uuid4()), "mode": "MOCK_CODEX"})
            self.assertEqual(400, response.status_code)
            self.assertEqual("INVALID_REQUEST", response.json()["code"])
            self.assertEqual(503, client.post(url, json={"expected_revision": 0, "request_id": str(uuid.uuid4()), "mode": "CORPORATE"}).status_code)

    def test_top_cannot_accept_a_prepared_model_draft(self):
        receipt = self.receipt()
        with Database("oa") as db, db.transaction(), self.assertRaises(ReviewError) as raised:
            WorkspaceClusteringStore(db).change("user-01", "project-1-cpu", "project-1-cpu-post-1", "codex-mock-source",
                action="PROPOSE", mode="MOCK_CODEX", expected_revision=0, request_id=str(uuid.uuid4()), mock_receipt=receipt)
        self.assertEqual("FORBIDDEN", raised.exception.code)
