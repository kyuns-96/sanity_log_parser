import unittest

from common.sanity_common import ReviewError
from common.sanity_review_advice import ApprovedCriteriaAdvisor, report_facts, validate_advice


class ReviewAdviceTests(unittest.TestCase):
    def test_only_common_facts_are_eligible_and_variable_objects_are_not_collapsed(self):
        report = {"rule": "R1", "records": [{"fields": {"mode": "M1", "object": "a"}}, {"fields": {"mode": "M1", "object": "b"}}]}
        self.assertEqual({"rule": "R1", "mode": "M1"}, report_facts(report))

    def test_an_advisor_cannot_invent_a_human_reference_or_a_final_unreferenced_pass(self):
        for refs in (["unknown"], [], ["known", "known"]):
            with self.assertRaises(ReviewError):
                validate_advice({"conclusion": "PASS", "reason": "explanation", "reference_ids": refs}, [{"id": "known"}])

    def test_no_confirmed_cases_never_produce_an_unfounded_pass(self):
        result = ApprovedCriteriaAdvisor().propose({"facts": {"rule": "R1"}}, [])
        self.assertEqual("DEFER", result["conclusion"])
        self.assertEqual([], result["reference_ids"])
