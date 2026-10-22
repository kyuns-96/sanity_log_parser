"""Focused retrieval policy and PostgreSQL human-feedback checks."""
import copy
import unittest
import uuid
from types import SimpleNamespace

from common.sanity_ai_retrieval import (compatible, comparison, query_profile,
    rank_case, select_ranked)
from common.sanity_common import ReviewError, canonical_json, digest
from oa.sanity_ai_cases import CaseRetriever
from common.sanity_submission import source_digest
import common.sanity_workspace_rationale as rationale


def context():
    scope = {"project_id": "project", "block_id": "block", "item_id": "clock-check",
             "family": "spyglass", "parser": "parser-1", "rule_version": "rules-1", "layout": "POST", "eco": "eco-2"}
    return {"scope": scope, "report": {"id": "target", "version_id": "version-1", "records": [
        {"id": "new", "raw": "clock crossing synchronizer 누락 확인", "fields": {"object": "top/clock"}}]}}


def example(key="past", **changes):
    value = {"id": key, "report_id": key + "-report", "scope": {**context()["scope"], "eco": "eco-1"},
             "version_id": "version-1", "confirmed": True, "reusable": False, "compatible_versions": False,
             "revision": 2, "source_sha256": "a" * 64, "reason": "clock crossing synchronizer 누락을 확인한 사례",
             "conclusion": "PASS"}
    value.update(changes)
    return value


class RetrievalPolicyTests(unittest.TestCase):
    def test_other_eco_and_non_reusable_confirmation_are_reference_candidates(self):
        case = example()
        self.assertTrue(compatible(context(), case, stage="CLUSTER"))
        self.assertTrue(compatible(context(), case, stage="REVIEW"))
        self.assertEqual({"current": "eco-2", "case": "eco-1"}, comparison(context(), case)["eco"])
        self.assertFalse(comparison(context(), case)["automatic_rule_reuse_approved"])

    def test_each_security_or_format_scope_difference_is_excluded(self):
        for key in ("project_id", "block_id", "item_id", "family", "parser", "rule_version", "layout"):
            with self.subTest(key=key):
                case = example(scope={**context()["scope"], key: "other"})
                self.assertFalse(compatible(context(), case, stage="CLUSTER"))

    def test_other_versions_require_existing_explicit_compatibility_permission(self):
        self.assertFalse(compatible(context(), example(version_id="version-2"), stage="CLUSTER"))
        self.assertTrue(compatible(context(), example(version_id="version-2", compatible_versions=True), stage="CLUSTER"))

    def test_self_unconfirmed_and_deferred_results_are_excluded(self):
        self.assertFalse(compatible(context(), example(report_id="target"), stage="CLUSTER"))
        self.assertFalse(compatible(context(), example(confirmed=False), stage="CLUSTER"))
        self.assertFalse(compatible(context(), example(conclusion="DEFER"), stage="REVIEW"))
        self.assertFalse(compatible({**context(), "group_id": "g"}, example(), stage="REVIEW"))

    def test_lexical_and_korean_suffix_similarity_rank_relevant_human_reason(self):
        profile = query_profile(context())
        relevant = rank_case(profile, example(reason="누락된 synchronizer clock crossing"))
        unrelated = rank_case(profile, example(reason="thermal antenna geometry violation"))
        self.assertGreater(relevant, unrelated)
        self.assertGreater(rank_case(profile, example(reason="누락된 상태")), 0)

    def test_query_term_reduction_is_explicit_and_all_input_records_are_counted(self):
        value = context()
        value["report"]["records"] *= 20
        profile = query_profile(value, max_terms=2)
        self.assertEqual(20, profile["source_record_count"])
        self.assertEqual(2, len(profile["terms"]))
        self.assertGreater(profile["omitted_query_features"], 0)

    def test_relevant_fail_counterexample_survives_many_high_scoring_pass_examples(self):
        rows = [{"case": example(str(index)), "score": 1 - index / 100, "bytes": 10} for index in range(20)]
        rows.append({"case": example("counterexample", conclusion="FAIL"), "score": .3, "bytes": 10})
        selected = select_ranked(rows, limit=3, max_bytes=100)
        self.assertEqual(3, len(selected))
        self.assertIn("counterexample", [row["case"]["id"] for row in selected])

    def test_complete_case_budget_never_truncates_an_example(self):
        large = {"case": example("large", reason="x" * 1000), "score": 1}
        small = {"case": example("small"), "score": .8}
        budget = len(canonical_json(small["case"]))
        selected = select_ranked([large, small], limit=8, max_bytes=budget)
        self.assertEqual([small], selected)
        self.assertEqual(example("small"), selected[0]["case"])


class MemoryRetriever(CaseRetriever):
    def __init__(self, candidates, stale=()):
        self.candidates, self.invalid = candidates, set(stale)
        self.hydrated = []

    def _scan(self, context, stage, profile):
        yield from self.candidates

    def _hydrate(self, candidate, context, stage):
        key = candidate["case"]["id"]
        self.hydrated.append(key)
        return None if key in self.invalid else {**candidate["case"], "example_complete": True}


class BoundedSearchTests(unittest.TestCase):
    def test_all_history_is_scanned_without_hydrating_irrelevant_reports(self):
        rows = [{"case": example(str(index)), "score": 0, "bytes": 1000, "active": False} for index in range(1200)]
        rows.insert(0, {"case": example("old-relevant"), "score": 1, "bytes": 1000, "active": False})
        retriever = MemoryRetriever(rows)
        result = retriever.select(context(), stage="CLUSTER", limit=1)
        self.assertEqual(["old-relevant"], [case["id"] for case in result["cases"]])
        self.assertEqual(["old-relevant"], retriever.hydrated)
        self.assertEqual(1201, result["metadata"]["counts"]["scanned_heads"])

    def test_stale_top_window_does_not_hide_lower_ranked_valid_case(self):
        rows = [{"case": example(str(index)), "score": 1 - index / 100, "bytes": 1000, "active": False}
                for index in range(30)]
        result = MemoryRetriever(rows, [str(index) for index in range(29)]).select(context(), stage="CLUSTER", limit=1)
        self.assertEqual(["29"], [case["id"] for case in result["cases"]])
        self.assertEqual(2, result["metadata"]["scan_passes"])

    def test_oversized_and_active_cases_are_excluded_with_counts(self):
        rows = [{"case": example("oversized"), "score": 1, "bytes": 9999999, "active": False},
                {"case": example("active"), "score": 1, "bytes": 1000, "active": True},
                {"case": example("usable"), "score": .8, "bytes": 1000, "active": False}]
        result = MemoryRetriever(rows).select(context(), stage="CLUSTER", limit=1, max_bytes=5000)
        self.assertEqual(["usable"], [case["id"] for case in result["cases"]])
        self.assertEqual(1, result["metadata"]["counts"]["over_budget"])
        self.assertEqual(1, result["metadata"]["counts"]["ineligible"])


class GraphRetriever(CaseRetriever):
    """Exercise real DAG validation; only immutable DB row reads are replaced."""
    def __init__(self, stage, nodes=23):
        self.rows, self.events, self.read_count = {}, [], 0
        self.stage = stage
        self.reviews = SimpleNamespace(row=self.read)
        for index in range(nodes):
            report_id, event_id = f"report-{index}", f"event-{index}"
            scope = {**context()["scope"], "eco": "eco-1"}
            source = {**{key: value for key, value in scope.items() if key != "layout"},
                      "id": report_id, "version_id": "version-1", "record_count": 1,
                      "records": [{"id": f"log-{index}", "raw": "clock synchronizer 누락",
                                   "fields": {"object": "top/clock"}}], "rule": "CLOCK"}
            refs = [{key: self.events[previous][key] for key in ("id", "report_id", "revision", "source_sha256")}
                    for previous in range(max(0, index - 2), index)]
            event = example(event_id, report_id=report_id, scope=scope, source_sha256=source_digest(source), revision=1)
            self.rows[("WS_REPORT", report_id)] = {"payload": source}
            if stage == "CLUSTER":
                group = {"id": "group", "label": "clock 누락", "record_ids": [f"log-{index}"]}
                event.update(groups=[group], references=refs, rationales={})
                event["rationales"]["group"] = {"status": "CONFIRMED", "binding": rationale.binding(event, group),
                    "content": {"reason": "clock 누락 기준 확인", "evidence_ids": group["record_ids"],
                                "reference_groups": [{"case_id": ref["id"], "group_id": "group", "reason": "이전 근거 확인"}
                                                     for ref in refs]}}
                kind, column = "WS_CLUSTER", "workspace_pipeline_event_ref"
            else:
                event["proposal_id"] = f"proposal-{index}"
                self.rows[("WS_REVIEW_PROPOSAL", event["proposal_id"])] = {
                    "payload": {"report_id": report_id, "references": refs}}
                kind, column = "WS_REVIEW", "workspace_review_event_ref"
            self.rows[(kind, report_id)] = {"state": {"event_id": event_id}, column: event_id, "version": 1}
            self.rows[(kind + "_EVENT", event_id)] = {"record_id": event_id, "payload": event}
            self.events.append(event)

    def read(self, kind, key):
        self.read_count += 1
        return self.rows.get((kind, key))

    def _bounded_row(self, kind, key):
        self._charge_work(2)
        return self.read(kind, key)

    def _scan(self, context, stage, profile):
        yield {"case": self.events[-1], "score": 1, "bytes": 1000, "active": False}

    def add_cycle(self):
        last = self.events[-1]
        reference = {key: last[key] for key in ("id", "report_id", "revision", "source_sha256")}
        if self.stage == "CLUSTER":
            self.events[0]["references"].append(reference)
            self.events[0]["rationales"]["group"]["content"]["reference_groups"].append(
                {"case_id": last["id"], "group_id": "group", "reason": "합성 순환 참조"})
        else:
            self.rows[("WS_REVIEW_PROPOSAL", self.events[0]["proposal_id"])]["payload"]["references"].append(reference)


class ReferenceGraphTests(unittest.TestCase):
    def test_shared_ancestors_validate_once_instead_of_exponential_row_reads(self):
        for stage, reads_per_node in (("CLUSTER", 3), ("REVIEW", 4)):
            with self.subTest(stage=stage):
                retriever = GraphRetriever(stage, nodes=23)
                result = retriever.select(context(), stage=stage, limit=1)
                self.assertEqual(["event-22"], [case["id"] for case in result["cases"]])
                self.assertLessEqual(retriever.read_count, 23 * reads_per_node + 1)
                self.assertEqual(23, retriever._validation_stats["unique_nodes"])
                self.assertEqual({}, retriever._validation_memo)
                # A new operation must not reuse even the successful DAG nodes.
                before = retriever.read_count
                self.assertTrue(retriever.revalidate(context(), result["cases"], stage=stage))
                self.assertGreater(retriever.read_count, before)

    def test_true_cycle_is_stale_but_shared_dag_ancestors_are_not_cycles(self):
        for stage in ("CLUSTER", "REVIEW"):
            with self.subTest(stage=stage):
                retriever = GraphRetriever(stage)
                retriever.add_cycle()
                self.assertEqual([], retriever.select(context(), stage=stage, limit=1)["cases"])
                self.assertLessEqual(retriever.read_count, 23 * 4)

    def test_new_operation_rechecks_a_changed_shared_ancestor_head(self):
        for stage in ("CLUSTER", "REVIEW"):
            with self.subTest(stage=stage):
                retriever = GraphRetriever(stage)
                cases = retriever.select(context(), stage=stage, limit=1)["cases"]
                del retriever.rows[("WS_CLUSTER" if stage == "CLUSTER" else "WS_REVIEW", "report-0")]
                self.assertFalse(retriever.revalidate(context(), cases, stage=stage))
                self.assertEqual([], retriever.select(context(), stage=stage, limit=1)["cases"])

    def test_unique_node_work_depth_and_byte_budgets_fail_explicitly(self):
        for bound in ("max_reference_nodes", "max_reference_work", "max_reference_depth", "max_reference_bytes"):
            with self.subTest(bound=bound):
                retriever = GraphRetriever("REVIEW")
                setattr(retriever, bound, 8)
                with self.assertRaises(ReviewError) as caught:
                    retriever.select(context(), stage="REVIEW", limit=1)
                self.assertEqual("AI_REFERENCE_LIMIT", caught.exception.code)
                self.assertLessEqual(retriever._validation_stats["unique_nodes"], 8)
                self.assertEqual({}, retriever._validation_memo)


from tests.support import PgTestCase


class RetrievalDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        from common.sanity_db import Database
        from oa.sanity_workspace import WorkspaceStore, seed_workspace
        from oa.test_workspace import fixture
        data = fixture()
        source = data["items"][0]
        source.update(rule_version="retrieval-v1", record_count=1)
        source["records"] = [{"id": "old-log", "raw": "clock synchronizer 누락 확인",
            "fields": {"object": "top/a", "mode": "M1", "check_point": "P1"}, "check_status": "FAIL"}]
        data["dataset_sha256"] = digest({key: value for key, value in data.items() if key != "dataset_sha256"})
        with Database("oa") as db, db.transaction():
            seed_workspace(db, data)
            target = copy.deepcopy(WorkspaceStore(db).report("user-04", "project-1-cpu", "project-1-cpu-post-1", "sample-0001"))
            target.update(id="retrieval-target", eco="eco01")
            target["records"][0]["id"] = "new-log"
            WorkspaceStore(db).put("WS_REPORT", target["id"], target,
                                   project=target["project_id"], block=target["block_id"])
        self.path = ("user-04", "project-1-cpu", "project-1-cpu-post-1")

    def test_latest_corrected_human_decision_is_retrieved_across_eco_without_reusable(self):
        from common.sanity_db import Database
        from oa.sanity_workspace_review import WorkspaceReviewStore
        with Database("oa") as db, db.transaction():
            store = WorkspaceReviewStore(db)
            initial = store.save(*self.path, "sample-0001", conclusion="PASS", reason="clock synchronizer 확인",
                criteria="", conditions={}, reusable=False, compatible_versions=False, expected_revision=0,
                request_id=str(uuid.uuid4()))["event"]
            corrected = store.save(*self.path, "sample-0001", conclusion="FAIL", reason="clock synchronizer 누락 정정",
                criteria="", conditions={}, reusable=False, compatible_versions=False, expected_revision=1,
                request_id=str(uuid.uuid4()))["event"]
            target = store.context(*self.path, "retrieval-target")
            result = CaseRetriever(db).select(target, stage="REVIEW")
            self.assertEqual([corrected["id"]], [case["id"] for case in result["cases"]])
            self.assertNotIn(initial["id"], [case["id"] for case in result["cases"]])
            self.assertFalse(result["cases"][0]["reusable"])
            self.assertEqual("FAIL", result["cases"][0]["conclusion"])
            self.assertEqual("old-log", result["cases"][0]["records"][0]["id"])
            self.assertEqual({"current": "eco01", "case": "chip_finish"}, result["cases"][0]["retrieval_comparison"]["eco"])

    def test_source_digest_mismatch_is_an_integrity_error(self):
        from common.sanity_db import Database, json_value
        from oa.sanity_workspace_review import WorkspaceReviewStore
        with Database("oa") as db, db.transaction():
            store = WorkspaceReviewStore(db)
            saved = store.save(*self.path, "sample-0001", conclusion="FAIL", reason="clock synchronizer 누락",
                criteria="", conditions={}, reusable=False, compatible_versions=False, expected_revision=0,
                request_id=str(uuid.uuid4()))["event"]
        # Synthetic integrity fault injected by the test DBA path only.
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,ARRAY['source_sha256'],%s) "
            "WHERE record_type='WS_REVIEW_EVENT' AND workspace_key=%s", (json_value("f" * 64), saved["id"]))
        with Database("oa") as db, db.transaction():
            store = WorkspaceReviewStore(db)
            target = store.context(*self.path, "retrieval-target")
            with self.assertRaises(ReviewError) as caught:
                CaseRetriever(db).select(target, stage="REVIEW")
            self.assertEqual("STATE_CORRUPT", caught.exception.code)

    def test_confirmed_partition_and_feedback_are_retrieved_until_withdrawn(self):
        from common.sanity_db import Database
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        with Database("oa") as db, db.transaction():
            store = WorkspaceClusteringStore(db)
            draft = store.change(*self.path, "sample-0001", action="PROPOSE", expected_revision=0,
                                 request_id=str(uuid.uuid4()))
            confirmed = store.change(*self.path, "sample-0001", action="CONFIRM", expected_revision=draft["revision"],
                request_id=str(uuid.uuid4()), reason="clock synchronizer 누락을 함께 검토하는 분류")
            target = store.context(*self.path, "retrieval-target")
            result = CaseRetriever(db).select(target, stage="CLUSTER")
            self.assertEqual([confirmed["id"]], [case["id"] for case in result["cases"]])
            case = result["cases"][0]
            self.assertFalse(case["reusable"])
            self.assertEqual(confirmed["rationales"], case["rationales"])
            self.assertEqual(confirmed["groups"], case["groups"])
            self.assertTrue(case["example_complete"])
            store.change(*self.path, "sample-0001", action="WITHDRAW", expected_revision=confirmed["revision"],
                         request_id=str(uuid.uuid4()), reason="분류 기준을 다시 검토")
            self.assertEqual([], CaseRetriever(db).select(target, stage="CLUSTER")["cases"])

    def test_pinned_revalidation_ignores_new_cases_and_detects_corrections(self):
        from common.sanity_db import Database
        from oa.sanity_workspace import WorkspaceStore
        from oa.sanity_workspace_review import WorkspaceReviewStore
        with Database("oa") as db, db.transaction():
            store = WorkspaceReviewStore(db)
            store.save(*self.path, "sample-0001", conclusion="FAIL", reason="clock synchronizer 누락",
                criteria="", conditions={}, reusable=False, compatible_versions=False, expected_revision=0,
                request_id=str(uuid.uuid4()))
            target = store.context(*self.path, "retrieval-target")
            retriever = CaseRetriever(db)
            cases = retriever.select(target, stage="REVIEW")["cases"]
            pins = [{**{key: case[key] for key in ("id", "report_id", "revision", "source_sha256")},
                     "sha256": digest(case)} for case in cases]
            extra = {**target["report"], "id": "another-report"}
            WorkspaceStore(db).put("WS_REPORT", extra["id"], extra, project=extra["project_id"], block=extra["block_id"])
            store.save(*self.path, "another-report", conclusion="PASS", reason="clock synchronizer 연결 확인",
                criteria="", conditions={}, reusable=False, compatible_versions=False, expected_revision=0,
                request_id=str(uuid.uuid4()))
            self.assertTrue(retriever.revalidate(target, cases, stage="REVIEW"))
            self.assertEqual({"cases": cases, "stale_ids": [], "over_budget_ids": []},
                             retriever.resolve_references(target, pins, stage="REVIEW"))
            store.save(*self.path, "sample-0001", conclusion="PASS", reason="clock synchronizer 확인 후 정정",
                criteria="", conditions={}, reusable=False, compatible_versions=False, expected_revision=1,
                request_id=str(uuid.uuid4()))
            self.assertFalse(retriever.revalidate(target, cases, stage="REVIEW"))
            self.assertEqual({"cases": [], "stale_ids": [cases[0]["id"]], "over_budget_ids": []},
                             retriever.resolve_references(target, pins, stage="REVIEW"))

    def test_group_case_requires_current_confirmed_partition_and_keeps_full_members(self):
        from common.sanity_db import Database
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
        with Database("oa") as db, db.transaction():
            clusters = WorkspaceClusteringStore(db)
            confirmed = {}
            for report_id in ("sample-0001", "retrieval-target"):
                draft = clusters.change(*self.path, report_id, action="PROPOSE", expected_revision=0,
                                        request_id=str(uuid.uuid4()))
                confirmed[report_id] = clusters.change(*self.path, report_id, action="CONFIRM", expected_revision=draft["revision"],
                    request_id=str(uuid.uuid4()), reason="clock synchronizer 누락 분류 확인")
            source = confirmed["sample-0001"]
            store = WorkspaceGroupReviewStore(db, source["groups"][0]["id"], source["revision"])
            event = store.save(*self.path, "sample-0001", conclusion="FAIL", reason="clock synchronizer 누락 판정",
                criteria="", conditions={}, reusable=False, compatible_versions=False, expected_revision=0,
                request_id=str(uuid.uuid4()))["event"]
            target_store = WorkspaceGroupReviewStore(db, confirmed["retrieval-target"]["groups"][0]["id"])
            target = target_store.context(*self.path, "retrieval-target")
            result = CaseRetriever(db).select(target, stage="REVIEW")
            self.assertEqual([event["id"]], [case["id"] for case in result["cases"]])
            self.assertEqual(source["groups"][0]["record_ids"], [record["id"] for record in result["cases"][0]["records"]])
            clusters.change(*self.path, "sample-0001", action="WITHDRAW", expected_revision=source["revision"],
                            request_id=str(uuid.uuid4()), reason="기존 분류 재검토")
            self.assertFalse(CaseRetriever(db).revalidate(target, result["cases"], stage="REVIEW"))
            self.assertEqual([], CaseRetriever(db).select(target, stage="REVIEW")["cases"])


if __name__ == "__main__":
    unittest.main()
