"""Executable handoff examples use real validators and no company SDK or DB."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from common.sanity_common import ReviewError, digest
from common.sanity_integration_contracts import SanityPlanProvider
from common.sanity_integration_examples import collection_plan_example, clustering_example, group_review_example
from common.sanity_review_advice import ReviewAdvisor, report_facts, validate_advice
from oa.sanity_workspace_clustering import ClusteringAdvisor, validate_clustering_advice

spec = importlib.util.spec_from_file_location("handoff_collector", Path(__file__).resolve().parents[1] / "vwp/main.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class IntegrationContractTests(unittest.TestCase):
    def test_plan_and_model_interfaces_cannot_be_used_without_implementations(self):
        for interface in (SanityPlanProvider, ClusteringAdvisor, ReviewAdvisor):
            with self.subTest(interface=interface.__name__), self.assertRaises(TypeError):
                interface()

    def test_plan_example_uses_existing_validation_without_inventing_approval(self):
        plan = collection_plan_example()
        self.assertEqual({"reports/check.txt"}, collector.validate_plan(plan))
        self.assertIs(plan["inventory"]["confirmed"], False)

    def test_inventory_retains_failed_and_unexecuted_expected_units(self):
        plan = collection_plan_example()
        original = plan["inventory"]["units"][0]
        for index, status in enumerate(("FAILED", "UNKNOWN", "NOT_RUN"), 2):
            plan["inventory"]["units"].append({**original, "review_unit_id": f"unit-{index}",
                "report": f"reports/check-{index}.txt", "execution_status": status})
        self.assertEqual(4, len(collector.validate_plan(plan)))

    def test_invalid_plan_paths_and_duplicate_units_are_not_accepted(self):
        for change in ("duplicate", "unsafe_path"):
            plan = collection_plan_example()
            if change == "duplicate":
                plan["inventory"]["units"].append(deepcopy(plan["inventory"]["units"][0]))
            else:
                plan["inventory"]["units"][0]["report"] = "reports/../outside.txt"
            with self.subTest(change=change), self.assertRaises(ReviewError):
                collector.validate_plan(plan)

    def test_first_report_gets_complete_v2_groups_without_historical_cases(self):
        packet = clustering_example()
        result = validate_clustering_advice(packet["reply"], packet["context"], packet["cases"], require_rationales=True)
        self.assertEqual(2, len(result["groups"]))
        self.assertEqual([], result["reference_ids"])
        self.assertEqual({r["id"] for r in packet["context"]["report"]["records"]},
                         {key for group in result["groups"] for key in group["record_ids"]})

    def test_historical_group_references_and_human_corrections_survive_validation(self):
        packet = clustering_example(with_history=True)
        before = deepcopy(packet)
        result = validate_clustering_advice(packet["reply"], packet["context"], packet["cases"], require_rationales=True)
        self.assertEqual(packet, before)
        self.assertEqual([packet["cases"][0]["id"]], result["reference_ids"])
        row = packet["cases"][0]["rationales"]["prior-clock"]
        self.assertNotEqual(row["content"]["reason"], row["proposal"]["content"]["reason"])

    def test_new_company_output_cannot_fall_back_to_legacy_global_rationale(self):
        packet = clustering_example()
        packet["reply"].pop("group_rationales")
        with self.assertRaises(ReviewError) as error:
            validate_clustering_advice(packet["reply"], packet["context"], [], require_rationales=True)
        self.assertEqual("INVALID_RATIONALE", error.exception.code)

    def test_a_representative_from_another_group_is_rejected(self):
        packet = clustering_example()
        packet["reply"]["group_rationales"][0]["evidence_ids"] = ["current-record-3"]
        with self.assertRaises(ReviewError):
            validate_clustering_advice(packet["reply"], packet["context"], [], require_rationales=True)

    def test_unknown_historical_group_and_reference_union_mismatch_are_rejected(self):
        for change in ("unknown_group", "union"):
            packet = clustering_example(with_history=True)
            if change == "unknown_group":
                packet["reply"]["group_rationales"][0]["reference_groups"][0]["group_id"] = "absent"
            else:
                packet["reply"]["reference_ids"] = []
            with self.subTest(change=change), self.assertRaises(ReviewError):
                validate_clustering_advice(packet["reply"], packet["context"], packet["cases"], require_rationales=True)

    def test_sdk_output_cannot_record_a_human_approval(self):
        packet = clustering_example()
        packet["reply"]["confirmed"] = True
        with self.assertRaises(ReviewError):
            validate_clustering_advice(packet["reply"], packet["context"], [], require_rationales=True)
        review = group_review_example()
        review["reply"]["actor"] = {"user_id": "invented"}
        with self.assertRaises(ReviewError):
            validate_advice(review["reply"], [])

    def test_group_review_packet_keeps_report_hash_and_only_targets_its_members(self):
        context = group_review_example()["context"]
        ids = {record["id"] for record in context["group_evidence"]}
        self.assertEqual(set(context["group_snapshot"]["record_ids"]), ids)
        self.assertEqual(2, len(ids))
        self.assertEqual(3, len(context["report"]["records"]))
        self.assertEqual(digest(context["report"]), context["source_sha256"])
        self.assertEqual(report_facts({**context["report"], "records": context["group_evidence"]}), context["facts"])
        self.assertEqual("SYNTHETIC-CLOCK", context["facts"]["code"])

    def test_no_reference_review_is_deferred_under_the_current_contract(self):
        packet = group_review_example()
        self.assertEqual("DEFER", validate_advice(packet["reply"], packet["cases"])["conclusion"])
        for result in ("PASS", "FAIL"):
            with self.subTest(result=result), self.assertRaises(ReviewError):
                validate_advice({**packet["reply"], "conclusion": result}, [])

    def test_referenced_suggestion_is_separate_from_the_immutable_tool_fail(self):
        packet = group_review_example(with_history=True)
        before = deepcopy(packet)
        self.assertEqual("PASS", validate_advice(packet["reply"], packet["cases"])["conclusion"])
        self.assertEqual("FAIL", packet["context"]["report"]["check_status"])
        self.assertEqual(packet, before)

    def test_example_packets_do_not_share_mutable_inputs(self):
        first = clustering_example(with_history=True)
        first["context"]["report"]["records"].clear()
        first["cases"][0]["groups"].clear()
        second = clustering_example(with_history=True)
        self.assertEqual(3, len(second["context"]["report"]["records"]))
        self.assertEqual(2, len(second["cases"][0]["groups"]))


if __name__ == "__main__":
    unittest.main()
