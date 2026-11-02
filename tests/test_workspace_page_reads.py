"""Paged production reads validate their dependencies without AI case searches."""
import unittest
from unittest.mock import patch
from uuid import uuid4

from common.sanity_common import ReviewError
from common.sanity_db import json_value
from oa.sanity_ai_cases import CaseRetriever
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_pipeline import WorkspacePipelineStore
from tests.support import PgTestCase


@unittest.skipIf(getattr(PgTestCase, "__unittest_skip__", False), "Disposable PostgreSQL fixture required")
class WorkspacePageReadTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.fixture = AIJobDatabaseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        with self.fixture.service() as service:
            self.event = WorkspaceClusteringStore(service.db, service.ws).change(*self.fixture.path,
                action="PROPOSE", expected_revision=0, request_id=uuid4(), prepared={
                    "groups": [{"id": "g1", "label": "같은 클록 조건", "record_ids": ["a", "b"]}],
                    "reason": "합성 원본의 클록 조건을 확인했습니다.", "reference_ids": [],
                    "group_rationales": [{"group_id": "g1", "reason": "같은 클록 조건이며 원인은 미확인입니다.",
                        "evidence_ids": ["a"], "reference_groups": []}]})

    def test_workbench_groups_reasons_and_history_do_not_rank_entire_source_again(self):
        with self.fixture.service() as service, patch.object(CaseRetriever, "select",
                side_effect=AssertionError("A display read must not repeat model case retrieval")):
            store = WorkspacePipelineStore(service.db, service.ws)
            view = store.view_paged(*self.fixture.path)
            self.assertEqual("REFERENCED", view["case_count_scope"])
            self.assertEqual((2, 1, 0), (view["record_count"], view["summary"]["total"], view["case_count"]))
            page = store.groups_page(*self.fixture.path, expected_revision=self.event["revision"])
            self.assertEqual("DRAFT", page["items"][0]["rationale"]["status"])
            detail = store.rationale_detail(*self.fixture.path, "g1", expected_revision=self.event["revision"])
            self.assertEqual(["a"], [row["id"] for row in detail["samples"]])
            self.assertEqual(1, store.history_page(*self.fixture.path)["total"])
            for method in (store.view_paged, store.records_page):
                with patch.object(service.ws, "report", wraps=service.ws.report) as report:
                    result = method(*self.fixture.path, **({"expected_revision": self.event["revision"]}
                                    if method == store.records_page else {}))
                    self.assertEqual(1, report.call_count)
                    self.assertTrue(result)

    def test_display_optimization_keeps_revision_and_source_integrity_checks(self):
        with self.fixture.service() as service:
            store = WorkspacePipelineStore(service.db, service.ws)
            with self.assertRaises(ReviewError) as caught:
                store.groups_page(*self.fixture.path, expected_revision=0)
            self.assertEqual("REVISION_CONFLICT", caught.exception.code)
            original = service.ws.get("WS_REPORT", "report")["payload"]
            changed = {**original, "records": [{**row, "raw": "합성 원문 변경"} for row in original["records"]]}
            # Simulate damaged stored input inside this disposable fixture.
            self.fixture.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key=%s",
                                     (json_value(changed), "report"))
            for read in (lambda: store.view_paged(*self.fixture.path),
                         lambda: store.groups_page(*self.fixture.path, expected_revision=self.event["revision"]),
                         lambda: store.rationale_detail(*self.fixture.path, "g1", expected_revision=self.event["revision"]),
                         lambda: store.records_page(*self.fixture.path, expected_revision=self.event["revision"])):
                with self.subTest(read=read), self.assertRaises(ReviewError) as caught:
                    read()
                self.assertEqual("STATE_CORRUPT", caught.exception.code)

    def test_pinned_case_validation_is_still_used_after_reference_content_changes(self):
        from tests.test_ai_evidence_choices import EvidenceChoiceDatabaseTests
        f = EvidenceChoiceDatabaseTests()
        try:
            f.setUp()
            f.toggle(True)
            path = ("owner", "block", "version", "target")
            with f.c.service() as service:
                store = WorkspaceClusteringStore(service.db, service.ws)
                referenced = store.change(*path, action="EDIT", expected_revision=f.target_cluster["revision"],
                    request_id=uuid4(), reason="검토한 과거 그룹을 참고했습니다.", rationale_operation={
                        "kind": "SAVE", "group_id": "g1", "reviewed": True, "content": {
                            "reason": "과거 클록 조건을 참고해 이번 차이를 검토합니다.", "evidence_ids": ["a"],
                            "reference_groups": [{"case_id": f.cluster["id"], "group_id": "g1", "reason": "같은 클록 조건"}]}})
            with patch.object(CaseRetriever, "select", side_effect=AssertionError("Unexpected relevance search")):
                with f.c.service() as service:
                    store = WorkspacePipelineStore(service.db, service.ws)
                    page = store.groups_page(*path, expected_revision=referenced["revision"])
                    self.assertEqual("CONFIRMED", page["items"][0]["rationale"]["status"])
            with f.c.service() as service:
                WorkspaceClusteringStore(service.db, service.ws).change(*f.c.path, action="EDIT",
                    expected_revision=f.cluster["revision"], request_id=uuid4(), reason="참고 원본 분류 수정",
                    operation={"kind": "RENAME", "destination_id": "g1", "label": "달라진 클록 조건"})
            with patch.object(CaseRetriever, "select", side_effect=AssertionError("Unexpected relevance search")):
                with f.c.service() as service:
                    store = WorkspacePipelineStore(service.db, service.ws)
                    page = store.groups_page(*path, expected_revision=referenced["revision"])
                    self.assertEqual("STALE", page["items"][0]["rationale"]["status"])
                    detail = store.rationale_detail(*path, "g1", expected_revision=referenced["revision"])
                    self.assertEqual("STALE", detail["status"])
                    self.assertEqual([], detail["references"])
        finally:
            f.doCleanups()
