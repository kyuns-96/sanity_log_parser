"""Focused PostgreSQL checks for trusted OA receipts and human review races."""
import copy
import uuid
from unittest.mock import patch

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database
from oa.sanity_ai_cases import CaseRetriever
from oa.sanity_workspace import WorkspaceStore, seed_workspace
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from oa.test_workspace import fixture
from tests.support import PgTestCase


class SyntheticOAWorkspace(WorkspaceStore):
    """Use synthetic auth fixtures while exercising the production case policy."""
    case_policy = "OA_RETRIEVAL_V1"


def metadata():
    return {"provider": "OA_OPENAI_COMPATIBLE", "model_called": True, "model": "synthetic-only",
            "prompt_version": "synthetic-prompt-v1", "input_sha256": "a" * 64}


def grouping(context, cases=()):
    ids = [record["id"] for record in context["report"]["records"]]
    refs = [{"case_id": case["id"], "group_id": case["groups"][0]["id"],
             "reason": "이전 사람이 확인한 클록 누락 패턴과 조건을 비교했습니다."} for case in cases]
    return {"groups": [{"id": "clock", "label": "클록 정의 누락", "record_ids": ids}],
            "reason": "전체 원본의 클록 정의 누락을 함께 검토합니다.", "reference_ids": [case["id"] for case in cases],
            "group_rationales": [{"group_id": "clock", "reason": "클록 정의가 빠진 공통 패턴이며 근본 원인은 미확인입니다.",
                                  "evidence_ids": ids[:20], "reference_groups": refs}]}


class AIStoreTests(PgTestCase):
    def setUp(self):
        super().setUp()
        data = fixture()
        source = data["items"][0]
        source.update(rule_version="oa-store-v1", record_count=1)
        source["records"] = [{"id": "original-log", "raw": "clock definition 누락",
                              "fields": {"object": "top/clock", "mode": "M1", "check_point": "P1"},
                              "check_status": "FAIL"}]
        source["raw_report"] = source["records"][0]["raw"]
        target = copy.deepcopy(source)
        target.update(id="next-report", eco="eco01")
        target["records"][0]["id"] = "next-log"
        data["items"].append(target)
        data["dataset_sha256"] = digest({key: value for key, value in data.items() if key != "dataset_sha256"})
        with Database("oa") as db, db.transaction():
            seed_workspace(db, data)
        self.path = ("user-04", "project-1-cpu", "project-1-cpu-post-1")

    def store(self, db):
        return WorkspaceClusteringStore(db, ws=SyntheticOAWorkspace(db))

    def apply(self, store, report="sample-0001", *, cases=(), retrieval=None, expected_revision=0, request_id=None):
        context = store.context(*self.path, report)
        return store.apply_ai(*self.path, report, expected_revision=expected_revision,
                              request_id=request_id or str(uuid.uuid4()), advice=grouping(context, cases),
                              cases=list(cases), metadata=metadata(), retrieval=retrieval or {"selected": []})

    def confirm(self, store, event, report="sample-0001"):
        return store.change(*self.path, report, action="CONFIRM", expected_revision=event["revision"],
                            request_id=str(uuid.uuid4()), reason="전체 로그와 그룹별 근거를 사람이 확인했습니다.")

    def review(self, db, cluster, criteria):
        return WorkspaceGroupReviewStore(db, "clock", cluster["revision"], ws=SyntheticOAWorkspace(db),
                                         criteria_provider=lambda _: criteria)

    def criterion(self, context):
        return {"id": "clock-rule", "version": "1", "text": "필수 클록 정의가 누락되면 실패로 제안합니다.",
                "scope": {key: context["scope"][key] for key in ("project_id", "block_id", "item_id")}}

    def review_args(self, context, criteria):
        return {"expected_revision": 0, "request_id": str(uuid.uuid4()), "cases": [], "metadata": metadata(),
                "retrieval": {"selected": []}, "criteria": copy.deepcopy(criteria), "advice": {
                    "schema_version": 2, "conclusion": "FAIL", "reason": "현재 클록 정의 누락이 점검 기준에 위배됩니다.",
                    "evidence_ids": [context["group_evidence"][0]["id"]], "reference_ids": [],
                    "criterion_ids": [criteria[0]["id"]]}}

    def test_receipt_replay_is_exact_and_browser_corporate_command_stays_blocked(self):
        with Database("oa") as db, db.transaction():
            store = self.store(db)
            rid = str(uuid.uuid4())
            first = self.apply(store, request_id=rid)
            self.assertEqual(first, self.apply(store, request_id=rid))
            self.assertTrue(first["model_called"])
            self.assertFalse(first["confirmed"])
            context = store.context(*self.path, "sample-0001")
            changed = grouping(context)
            changed["reason"] = "다른 응답"
            with self.assertRaises(ReviewError) as error:
                store.apply_ai(*self.path, "sample-0001", expected_revision=0, request_id=rid,
                               advice=changed, cases=[], metadata=metadata(), retrieval={"selected": []})
            self.assertEqual("REQUEST_CONFLICT", error.exception.code)
            with self.assertRaises(ReviewError) as error:
                store.change(*self.path, "sample-0001", action="PROPOSE", expected_revision=1,
                             request_id=str(uuid.uuid4()), mode="CORPORATE", prepared=changed)
            self.assertEqual("AI_NOT_CONFIGURED", error.exception.code)
            self.assertEqual(first["id"], store.current("sample-0001")[0]["id"])

    def test_incomplete_metadata_and_old_revision_cannot_publish(self):
        with Database("oa") as db, db.transaction():
            store = self.store(db)
            context = store.context(*self.path, "sample-0001")
            with self.assertRaises(ReviewError) as error:
                store.apply_ai(*self.path, "sample-0001", expected_revision=0, request_id=str(uuid.uuid4()),
                               advice=grouping(context), cases=[], metadata={**metadata(), "model_called": False},
                               retrieval={"selected": []})
            self.assertEqual("INVALID_AI_RECEIPT", error.exception.code)
            self.assertEqual((None, 0), store.current("sample-0001"))
            first = self.apply(store)
            with self.assertRaises(ReviewError) as error:
                self.apply(store, expected_revision=0)
            self.assertEqual("REVISION_CONFLICT", error.exception.code)
            self.assertEqual(first["id"], store.current("sample-0001")[0]["id"])

    def test_direct_review_remains_a_proposal_and_criteria_change_blocks_human_adoption(self):
        with Database("oa") as db, db.transaction():
            store = self.store(db)
            cluster = self.confirm(store, self.apply(store))
            criteria = [self.criterion(store.context(*self.path, "sample-0001"))]
            reviews = self.review(db, cluster, criteria)
            context = reviews.context(*self.path, "sample-0001")
            args = self.review_args(context, criteria)
            proposal = reviews.apply_ai(*self.path, "sample-0001", **args)
            self.assertEqual(proposal, reviews.apply_ai(*self.path, "sample-0001", **args))
            self.assertEqual((None, 0), reviews.current("sample-0001"))
            self.assertFalse(reviews.stale(proposal, context, 0))
            criteria[0]["version"] = "2"
            self.assertTrue(reviews.stale(proposal, context, 0))
            with self.assertRaises(ReviewError) as error:
                reviews.save(*self.path, "sample-0001", expected_revision=0, request_id=str(uuid.uuid4()),
                             conclusion="PASS", reason="수정된 조건으로 재검토가 필요합니다.", criteria="", conditions={},
                             reusable=False, compatible_versions=False, proposal_id=proposal["id"])
            self.assertEqual("PROPOSAL_STALE", error.exception.code)
            self.assertEqual((None, 0), reviews.current("sample-0001"))

    def test_group_review_requires_confirmed_partition_and_current_revision(self):
        with Database("oa") as db, db.transaction():
            store = self.store(db)
            draft = self.apply(store)
            criteria = [self.criterion(store.context(*self.path, "sample-0001"))]
            reviews = self.review(db, draft, criteria)
            args = self.review_args(reviews.context(*self.path, "sample-0001"), criteria)
            with self.assertRaises(ReviewError) as error:
                reviews.apply_ai(*self.path, "sample-0001", **args)
            self.assertEqual("CLUSTER_NOT_CONFIRMED", error.exception.code)
            self.confirm(store, draft)
            with self.assertRaises(ReviewError) as error:
                reviews.apply_ai(*self.path, "sample-0001", **args)
            self.assertEqual("REVISION_CONFLICT", error.exception.code)

    def test_rank_changes_do_not_remove_pinned_rationales_but_withdrawal_stales_them(self):
        with Database("oa") as db, db.transaction():
            store = self.store(db)
            source = self.confirm(store, self.apply(store))
            context = store.context(*self.path, "next-report")
            selection = CaseRetriever(db, store.ws).select(context, stage="CLUSTER")
            self.assertEqual([source["id"]], [case["id"] for case in selection["cases"]])
            self.assertFalse(selection["cases"][0]["reusable"])
            next_draft = self.apply(store, "next-report", cases=selection["cases"], retrieval=selection["metadata"])
            with patch.object(CaseRetriever, "select", return_value={"cases": [], "metadata": {"selected": []}}):
                self.assertFalse(store.stale(next_draft, context))
                confirmed = self.confirm(store, next_draft, "next-report")
            self.assertEqual(next_draft["initial_ai_event_id"], confirmed["initial_ai_event_id"])
            self.assertEqual(next_draft["model_metadata"], confirmed["model_metadata"])
            store.change(*self.path, "sample-0001", action="WITHDRAW", expected_revision=source["revision"],
                         request_id=str(uuid.uuid4()), reason="사람이 이전 분류 기준을 철회했습니다.")
            self.assertTrue(store.stale(confirmed, context))

    def test_selected_case_changed_while_model_runs_cannot_publish(self):
        with Database("oa") as db, db.transaction():
            store = self.store(db)
            source = self.confirm(store, self.apply(store))
            context = store.context(*self.path, "next-report")
            selection = CaseRetriever(db, store.ws).select(context, stage="CLUSTER")
            store.change(*self.path, "sample-0001", action="WITHDRAW", expected_revision=source["revision"],
                         request_id=str(uuid.uuid4()), reason="검토 중 철회")
            with self.assertRaises(ReviewError) as error:
                self.apply(store, "next-report", cases=selection["cases"], retrieval=selection["metadata"])
            self.assertEqual("PROPOSAL_STALE", error.exception.code)
            self.assertEqual((None, 0), store.current("next-report"))
