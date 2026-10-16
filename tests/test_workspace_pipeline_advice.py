import unittest

from common.sanity_common import ReviewError
from oa.sanity_workspace_clustering import ConfirmedPartitionAdvisor, validate_clustering_advice


class PartitionAdviceTests(unittest.TestCase):
    def context(self):
        return {"scope": {"project_id": "synthetic-project"}, "report": {"rule": "R1", "record_count": 2,
            "records": [{"id": key, "fields": {"object": "same", "code": "C1", "severity": "ERROR"}} for key in ("a", "b")]}}

    def test_untrusted_proposals_cannot_drop_duplicate_or_invent_evidence(self):
        context = self.context()
        for ids in (["a"], ["a", "b", "b"], ["a", "unknown"]):
            with self.assertRaises(ReviewError):
                validate_clustering_advice({"groups": [{"id": "g1", "label": "group", "record_ids": ids}],
                                           "reason": "synthetic", "reference_ids": []}, context, [])

    def test_unknown_or_duplicate_case_references_and_extra_outputs_are_rejected(self):
        context = self.context()
        value = {"groups": [{"id": "g1", "label": "group", "record_ids": ["a", "b"]}], "reason": "synthetic", "reference_ids": []}
        for changes in ({"reference_ids": ["unknown"]}, {"reference_ids": ["known", "known"]}, {"reason": " "}, {"confirmed": True}):
            with self.assertRaises(ReviewError):
                validate_clustering_advice({**value, **changes}, context, [{"id": "known"}])

    def test_identical_records_split_by_a_person_are_not_generalized_from_position(self):
        context = self.context()
        case = {"id": "known", "reusable": True, "identity_fields": ["object"], "report_rule": "R1",
                "records": context["report"]["records"], "groups": [{"id": "old-a", "record_ids": ["a"]}, {"id": "old-b", "record_ids": ["b"]}]}
        result = ConfirmedPartitionAdvisor().propose(context, [case], ["object"])
        self.assertEqual(2, len(result["groups"]))
        self.assertIn("구분 불가", result["reason"])
