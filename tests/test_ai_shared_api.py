"""Old mutation replays must respect the shared-knowledge response boundary."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.sanity_ai_knowledge import EVIDENCE_MODE
from oa.ai_workbench_api import create_workbench_router
from oa.workspace_api import create_app


class SharedCaseResponseTests(unittest.TestCase):
    path = "/blocks/block/versions/version/reports/report"
    request_id = "00000000-0000-4000-8000-000000000001"

    def payload(self):
        return {"id": "saved-event", "revision": 1,
                "records": [{"id": "current-log", "raw": "current target evidence"}],
                "groups": [{"id": "group", "label": "Current group", "record_ids": ["current-log"]}],
                "group_snapshot": {"id": "group", "record_ids": ["current-log"]},
                "proposal": {"references": [{
                    "id": "historical-case", "reason": "Human-confirmed shared explanation",
                    "scope": {"project_id": "other-project", "block_id": "other-block"},
                    "records": [{"id": "private-log", "raw": "historical original"}],
                    "group_snapshot": {"id": "old-group", "record_ids": ["private-log"]},
                    "command": {"raw_report": "historical original"}},
                    {"id": "already-shared-case", "reason": "Shared group rationale",
                     "group_snapshot": {"id": "shared-group", "record_count": 2},
                     "evidence_mode": EVIDENCE_MODE, "example_complete": False}]}}

    def assert_shared(self, actual, original, *, compact=False):
        self.assertEqual(original["records"], actual["records"])
        if not compact:
            self.assertEqual(original["group_snapshot"], actual["group_snapshot"])
        case = actual["proposal"]["references"][0]
        self.assertEqual("Human-confirmed shared explanation", case["reason"])
        self.assertEqual("other-block", case["scope"]["block_id"])
        self.assertEqual(EVIDENCE_MODE, case["evidence_mode"])
        self.assertFalse(case["example_complete"])
        self.assertEqual(1, case["group_snapshot"]["record_count"])
        self.assertNotIn("record_ids", case["group_snapshot"])
        self.assertNotIn("records", case)
        self.assertNotIn("command", case)
        self.assertEqual(2, actual["proposal"]["references"][1]["group_snapshot"]["record_count"])

    def test_production_cluster_and_review_replays_project_historical_references(self):
        payload = self.payload()
        before = copy.deepcopy(payload)
        actor = SimpleNamespace(auth_method="sso", access=SimpleNamespace(db=object()), user_id="reviewer")
        workspace = SimpleNamespace(db=actor.access.db)
        app = FastAPI()
        app.include_router(create_workbench_router(lambda: actor))
        with patch("oa.ai_workbench_api.ProductionWorkspaceStore", return_value=workspace), \
                patch("oa.ai_workbench_api.WorkspaceClusteringStore") as clusters, \
                patch("oa.ai_workbench_api.WorkspaceGroupReviewStore") as reviews, TestClient(app) as client:
            clusters.return_value.change.return_value = payload
            reviews.return_value.save.return_value = payload
            requests = [("/clustering", {"action": "CONFIRM", "reason": "Confirmed groups"}),
                        ("/groups/group/review", {"conclusion": "FAIL", "reason": "Confirmed finding",
                                                   "expected_cluster_revision": 1})]
            for suffix, body in requests:
                for compact in (False, True):
                    with self.subTest(suffix=suffix, compact=compact):
                        response = client.post("/api/workbench" + self.path + suffix,
                                               params={"compact": str(compact).lower()},
                                               json={**body, "expected_revision": 0, "request_id": self.request_id})
                        self.assertEqual(200, response.status_code, response.text)
                        self.assert_shared(response.json(), before, compact=compact)
        self.assertEqual(before, payload, "Immutable replay snapshots must not be rewritten")

    def test_synthetic_mutation_replays_use_the_same_projection(self):
        payload = self.payload()
        before = copy.deepcopy(payload)
        app = create_app(origins=("https://testserver",))
        # Authentication and DB behavior have their own integration tests. Here
        # the route's historical response projection is the subject under test.
        route = next(route for route in app.routes if route.path.endswith("/review-proposals"))
        app.dependency_overrides[route.dependant.dependencies[0].call] = lambda: {"user": {"id": "reviewer"}}
        with patch("oa.workspace_api.Database", return_value=MagicMock()), \
                patch("oa.workspace_api.WorkspaceReviewStore") as reviews, \
                patch("oa.workspace_api.WorkspaceClusteringStore") as clusters, \
                patch("oa.workspace_api.WorkspaceGroupReviewStore") as groups, \
                TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver"}) as client:
            reviews.return_value.save.return_value = payload
            reviews.return_value.propose.return_value = payload
            clusters.return_value.change.return_value = payload
            groups.return_value.save.return_value = payload
            groups.return_value.propose.return_value = payload
            requests = [("/review", {"conclusion": "FAIL", "reason": "Confirmed finding"}),
                        ("/review-proposals", {}),
                        ("/clustering", {"action": "CONFIRM", "reason": "Confirmed groups"}),
                        ("/clustering/proposals", {}),
                        ("/groups/group/review", {"conclusion": "FAIL", "reason": "Confirmed finding",
                                                   "expected_cluster_revision": 1}),
                        ("/groups/group/review-proposals", {"expected_cluster_revision": 1})]
            for suffix, body in requests:
                variants = (False, True) if suffix in ("/clustering", "/groups/group/review", "/groups/group/review-proposals") else (False,)
                for compact in variants:
                    with self.subTest(suffix=suffix, compact=compact):
                        response = client.post("/workspace/api" + self.path + suffix,
                                               params={"compact": str(compact).lower()},
                                               json={**body, "expected_revision": 0, "request_id": self.request_id})
                        self.assertEqual(200, response.status_code, response.text)
                        self.assert_shared(response.json(), before, compact=compact)
        self.assertEqual(before, payload)
