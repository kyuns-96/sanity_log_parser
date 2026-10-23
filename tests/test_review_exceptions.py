"""Exception metadata is conditional AI evidence, never implicit rule approval."""
import copy
import unittest
import uuid

from pydantic import ValidationError

from common.sanity_ai_retrieval import compatible, comparison, query_profile, rank_case
from common.sanity_common import ReviewError
from common.sanity_review_advice import (
    ApprovedCriteriaAdvisor,
    normalize_review_exception,
    validate_stored_review_exception,
)
from oa.workspace_api import GroupReviewChange, ReviewChange


EXCEPTION = {"rule": "CLOCK_CHECK", "reason": "검증한 대체 회로가 있습니다.",
             "conditions": "해당 모드에서 대체 회로가 활성화된 경우에만 허용합니다."}


def command(**changes):
    return {"conclusion": "PASS", "reason": "현재 원문과 실행 조건을 확인했습니다.",
            "expected_revision": 0, "request_id": str(uuid.uuid4()), **changes}


class ReviewExceptionContractTests(unittest.TestCase):
    def test_both_api_models_accept_omission_null_and_trimmed_exception(self):
        for model, extra in ((ReviewChange, {}), (GroupReviewChange, {"expected_cluster_revision": 2})):
            with self.subTest(model=model.__name__):
                self.assertIsNone(model.model_validate(command(**extra)).exception)
                self.assertIsNone(model.model_validate(command(exception=None, **extra)).exception)
                supplied = {key: "  " + value + " \n" for key, value in EXCEPTION.items()}
                value = model.model_validate(command(exception=supplied, **extra)).model_dump()
                self.assertEqual(EXCEPTION, value["exception"])
                self.assertFalse(value["reusable"])

    def test_exception_api_schema_rejects_shape_coercion_unknown_fields_and_blank_values(self):
        invalid = [False, [], "CLOCK_CHECK", {}, {"rule": "CLOCK_CHECK"},
                   {**EXCEPTION, "approved": True}]
        for field in EXCEPTION:
            invalid.extend(({**EXCEPTION, field: value} for value in (None, True, 12, [], " \n ")))
        for model, extra in ((ReviewChange, {}), (GroupReviewChange, {"expected_cluster_revision": 2})):
            for value in invalid:
                with self.subTest(model=model.__name__, value=value):
                    with self.assertRaises(ValidationError):
                        model.model_validate(command(exception=value, **extra))

    def test_exception_limits_apply_to_trimmed_strict_strings(self):
        limits = {"rule": 500, "reason": 2000, "conditions": 2000}
        for field, limit in limits.items():
            with self.subTest(field=field):
                boundary = {**EXCEPTION, field: "x" * limit}
                padded = {**boundary, field: "  " + boundary[field] + " \n"}
                self.assertEqual(boundary, ReviewChange.model_validate(command(exception=padded)).model_dump()["exception"])
                self.assertEqual(boundary, normalize_review_exception(padded))
                with self.assertRaises(ValidationError):
                    ReviewChange.model_validate(command(exception={**boundary, field: "x" * (limit + 1)}))
                with self.assertRaises(ReviewError) as caught:
                    normalize_review_exception({**boundary, field: "x" * (limit + 1)})
                self.assertEqual("INVALID_EXCEPTION", caught.exception.code)

    def test_stored_exception_validation_preserves_history_and_never_infers_an_override(self):
        historical = {"conclusion": "PASS", "tool_status": "FAIL", "reason": "원문과 조건 확인"}
        original = copy.deepcopy(historical)
        self.assertIsNone(validate_stored_review_exception(historical))
        self.assertEqual(original, historical)
        explicit = {**historical, "exception": None}
        self.assertIsNone(validate_stored_review_exception(explicit))
        self.assertIn("exception", explicit)
        confirmed = {**historical, "reusable": False, "exception": copy.deepcopy(EXCEPTION)}
        before = copy.deepcopy(confirmed)
        self.assertIsNone(validate_stored_review_exception(confirmed))
        self.assertEqual(before, confirmed)
        for value in ({**EXCEPTION, "reason": " "}, {**EXCEPTION, "rule": " CLOCK_CHECK"}):
            with self.subTest(value=value):
                with self.assertRaises(ReviewError) as caught:
                    validate_stored_review_exception({**historical, "exception": value})
                self.assertEqual("STATE_CORRUPT", caught.exception.code)
        for changes in ({"conclusion": "DEFER"}, {"reusable": True}):
            with self.subTest(changes=changes):
                with self.assertRaises(ReviewError) as caught:
                    validate_stored_review_exception({**confirmed, **changes})
                self.assertEqual("STATE_CORRUPT", caught.exception.code)

    def test_reference_engine_excludes_exception_even_if_legacy_rule_approval_is_present(self):
        facts = {"rule": "CLOCK_CHECK", "mode": "M1"}
        exception_case = {"id": "exception", "reusable": True, "conditions": facts,
                          "conclusion": "PASS", "exception": EXCEPTION}
        advisor = ApprovedCriteriaAdvisor()
        result = advisor.propose({"facts": facts}, [exception_case])
        self.assertEqual(("DEFER", []), (result["conclusion"], result["reference_ids"]))
        ordinary = {"id": "ordinary", "reusable": True, "conditions": facts, "conclusion": "FAIL"}
        result = advisor.propose({"facts": facts}, [exception_case, ordinary])
        self.assertEqual(("FAIL", ["ordinary"]), (result["conclusion"], result["reference_ids"]))

    def test_each_exception_field_can_make_a_case_relevant_to_ai_search(self):
        profile = query_profile({"report": {"records": [{"raw": "synchronizer"}]}})
        base = {"reason": "unrelated", "criteria": "", "conditions": {}}
        self.assertEqual(0, rank_case(profile, base))
        for field in EXCEPTION:
            with self.subTest(field=field):
                case = {**base, "exception": {**EXCEPTION, field: "synchronizer"}}
                self.assertGreater(rank_case(profile, case), 0)

    @staticmethod
    def retrieval_example():
        scope = {"project_id": "p", "block_id": "b", "item_id": "clock", "family": "GCA",
                 "parser": "parser-1", "rule_version": "rules-1", "layout": "POST", "eco": "ECO-2"}
        context = {"scope": scope, "report": {"id": "new", "version_id": "v2"},
                   "version": {"net_ver": "NET-2", "revision": "REV-2"}}
        case = {"scope": {**scope, "eco": "ECO-1"}, "report_id": "old", "version_id": "v1",
                "source_version": {"net_ver": "NET-1", "revision": "REV-1"}, "conclusion": "PASS",
                "reusable": False, "compatible_versions": False, "exception": EXCEPTION}
        return context, case

    def test_exception_cases_are_globally_searchable_without_legacy_rule_approval(self):
        context, original = self.retrieval_example()
        sources = ({}, {"block_id": "another-block"},
                   {"project_id": "another-project", "block_id": "another-block"},
                   {"project_id": "another-project", "block_id": "another-block", "item_id": "another-check",
                    "family": "VCLP", "parser": "parser-2", "rule_version": "rules-2", "layout": "PRE", "eco": None})
        for source in sources:
            for approved in (False, True):
                for conclusion in ("PASS", "FAIL"):
                    case = {**original, "scope": {**original["scope"], **source},
                            "compatible_versions": approved, "conclusion": conclusion}
                    with self.subTest(source=source, approved=approved, conclusion=conclusion):
                        self.assertTrue(compatible(context, case, stage="REVIEW"))
                        self.assertTrue(compatible(context, {**case, "exception": None}, stage="REVIEW"))
                        details = comparison(context, case)
                        self.assertEqual({"current": "v2", "case": "v1"}, details["version"])
                        self.assertEqual({"current": "ECO-2", "case": case["scope"]["eco"]}, details["eco"])
                        self.assertEqual({"current": "REV-2", "case": "REV-1"}, details["attributes"]["revision"])
                        self.assertEqual(approved, details["compatible_versions_explicit"])
                        self.assertFalse(details["automatic_rule_reuse_approved"])

    def test_global_exception_search_keeps_self_deferred_and_review_granularity_guards(self):
        context, case = self.retrieval_example()
        for change in ({"report_id": "new"}, {"conclusion": "DEFER"}, {"conclusion": None},
                       {"group_id": "past-group"}):
            with self.subTest(change=change):
                self.assertFalse(compatible(context, {**case, **change}, stage="REVIEW"))
        group_context = {**context, "group_id": "current-group"}
        self.assertFalse(compatible(group_context, case, stage="REVIEW"))
        self.assertTrue(compatible(group_context, {**case, "group_id": "past-group"}, stage="REVIEW"))

    def test_global_exception_search_still_requires_source_identity(self):
        context, case = self.retrieval_example()
        for side in ("context", "case"):
            for key in ("project_id", "block_id", "item_id", "family", "parser", "rule_version", "layout"):
                current, candidate = copy.deepcopy(context), copy.deepcopy(case)
                (current if side == "context" else candidate)["scope"].pop(key)
                with self.subTest(side=side, missing=key):
                    self.assertFalse(compatible(current, candidate, stage="REVIEW"))
            for key in ("project_id", "block_id", "item_id"):
                for value in (None, ""):
                    current, candidate = copy.deepcopy(context), copy.deepcopy(case)
                    (current if side == "context" else candidate)["scope"][key] = value
                    with self.subTest(side=side, blank=key, value=value):
                        self.assertFalse(compatible(current, candidate, stage="REVIEW"))
