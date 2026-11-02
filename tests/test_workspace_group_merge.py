"""A split can be merged through the authenticated, revision-pinned API."""
import unittest

from tests.support import PgTestCase


@unittest.skipIf(getattr(PgTestCase, "__unittest_skip__", False), "Disposable PostgreSQL fixture required")
class WorkspaceGroupMergeTests(unittest.TestCase):
    def setUp(self):
        from oa.test_workspace_pipeline import WorkspacePipelineTests, BASE
        self.fixture = WorkspacePipelineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base = BASE + "sample-0001"

    def current(self):
        return self.fixture.client.get(self.base + "/clustering").json()

    def test_split_then_merge_keeps_selected_destination_all_logs_and_immutable_history(self):
        f = self.fixture
        source = f.client.get(self.base).json()
        draft = f.propose()
        original = next(group for group in draft["groups"] if "e-1" in group["record_ids"])
        split = f.post("sample-0001/clustering/operations", f.command(
            expected_revision=draft["revision"], reason="같은 원인인지 확인하려고 임시 분리",
            operation={"kind": "MOVE", "record_ids": ["e-2"], "new_group_id": "human-split", "label": "임시 분리"}))
        command = f.command(expected_revision=split["revision"], reason="같은 원인임을 확인해 재병합",
            operation={"kind": "MERGE", "group_ids": [original["id"], "human-split"], "destination_id": "human-split"})
        merged = f.post("sample-0001/clustering/operations", command)
        self.assertEqual(merged, f.post("sample-0001/clustering/operations", command))
        current = self.current()["current"]
        target = next(group for group in current["groups"] if group["id"] == "human-split")
        self.assertEqual(["e-1", "e-2"], target["record_ids"])
        self.assertEqual("CLK_0003 · 임시 분리", target["label"])
        self.assertNotIn(original["id"], {group["id"] for group in current["groups"]})
        self.assertCountEqual(["e-1", "e-2", "e-3", "e-4"],
                              [key for group in current["groups"] for key in group["record_ids"]])
        self.assertFalse(current["confirmed"])
        self.assertEqual(split["revision"] + 1, current["revision"])
        self.assertEqual(source, f.client.get(self.base).json())
        old = f.client.get(self.base + "/clustering/records", params={"expected_revision": current["revision"],
            "event_id": split["id"], "group_id": "human-split"}).json()
        self.assertEqual(["e-2"], [record["id"] for record in old["items"]])
        history = f.client.get(self.base + "/clustering/history").json()["items"]
        self.assertEqual(command["reason"], next(event for event in history if event["id"] == merged["id"])["reason"])
        stale = f.post("sample-0001/clustering/operations", {**command, **f.command(
            expected_revision=split["revision"])}, 409)
        self.assertEqual("REVISION_CONFLICT", stale["code"])
        changed = {**command, "operation": {**command["operation"], "destination_id": original["id"]}}
        self.assertEqual("REQUEST_CONFLICT", f.post("sample-0001/clustering/operations", changed, 409)["code"])
        self.assertEqual(current, self.current()["current"])

    def test_merging_confirmed_groups_requires_new_reasons_and_preserves_both_judgments(self):
        f = self.fixture
        f.propose()
        confirmed = f.confirm()
        first, second = confirmed["groups"][:2]
        before = {group["id"]: f.group_save(confirmed, group["id"])["event"] for group in (first, second)}
        merged = f.post("sample-0001/clustering/operations", f.command(
            expected_revision=confirmed["revision"], reason="확정 후 같은 원인으로 확인해 병합",
            operation={"kind": "MERGE", "group_ids": [first["id"], second["id"]], "destination_id": second["id"]}))
        current = self.current()["current"]
        self.assertFalse(current["confirmed"])
        self.assertEqual("STALE", current["rationales"][second["id"]]["status"])
        blocked = f.post("sample-0001/clustering", f.command(action="CONFIRM", expected_revision=merged["revision"]), 409)
        self.assertEqual("RATIONALE_STALE", blocked["code"])
        for group in (first, second):
            view = f.client.get(self.base + "/groups/" + group["id"] + "/review").json()
            self.assertTrue(view["current_stale"])
            self.assertFalse(view["can_review"])
            self.assertEqual(before[group["id"]], view["history"][0])
            self.assertEqual("GROUP_REMOVED" if group == first else "CLUSTER_NOT_CONFIRMED", view["blocked_reason"])
        top = f.client_for("user01")
        forbidden = top.post(self.base + "/clustering/operations", json=f.command(
            expected_revision=current["revision"], reason="조회 전용 사용자의 병합 시도",
            operation={"kind": "MERGE", "group_ids": [g["id"] for g in current["groups"]], "destination_id": second["id"]}))
        self.assertEqual(403, forbidden.status_code)
        self.assertEqual(current, self.current()["current"])
