"""Shared-case transport excludes source logs without changing the target."""
from copy import deepcopy
import unittest

from common.sanity_ai_knowledge import EVIDENCE_MODE, shared_case, shared_case_views
from common.sanity_common import canonical_json, digest


def historical():
    group = {"id": "historical-group", "label": "확정된 클록 분류", "record_ids": ["private-log-1", "private-log-2"]}
    rationale = {"status": "CONFIRMED", "source": "HUMAN", "content": {
        "reason": "사람이 확인한 공유 분류 근거", "evidence_ids": ["private-log-1"],
        "reference_groups": [{"case_id": "ancestor", "group_id": "ancestor-group", "reason": "공유된 과거 분류 의도",
                              "raw": "private-original"}]},
        "binding": {"record_ids": ["private-log-1"]},
        "proposal": {"content": {"reason": "private-original"}}, "history": [{"raw": "private-original"}]}
    return {"id": "case-identity", "report_id": "old-report", "version_id": "old-version", "revision": 3,
            "source_sha256": "a" * 64, "sha256": "b" * 64, "source_record_count": 100000,
            "confirmed": True, "conclusion": "PASS", "reason": "사람이 확정한 공유 판정 근거",
            "criteria": "기존에 명시한 조건", "conditions": {"rule": "CLK_0003", "mode": "FUNC",
                                                               "raw_report": "private-original", "raw_bag": {"raw": "private-original"}},
            "exception": {"rule": "CLK_0003", "reason": "검증된 예외", "conditions": "동일 연결 조건", "raw": "private-original"},
            "reusable": False, "compatible_versions": False,
            "scope": {"project_id": "project-a", "block_id": "block-a", "item_id": "clock-check", "family": "GCA",
                      "parser": "parser-1", "rule_version": "rule-1", "layout": "POST", "eco": "eco03", "raw": "private-original"},
            "source_version": {"id": "old-version", "project_id": "project-a", "block_id": "block-a",
                               "net_ver": "N1", "revision": "R2", "layout": "POST", "ecos": [None, "eco03"], "source_pre_id": None,
                               "records": [{"raw": "private-original"}]},
            "actor": {"id": "reviewer", "name": "합성 검토자", "username": "reviewer", "role": "USER", "session": "private-original"},
            "at": "2026-10-23T00:00:00+00:00", "groups": [group], "group_snapshot": deepcopy(group),
            "rationales": {group["id"]: rationale}, "clustering_rationale": deepcopy(rationale),
            "records": [{"id": "private-log-1", "raw": "private-original", "fields": {"object": "private-original"}}],
            "group_evidence": [{"raw": "private-original"}], "raw_report": "private-original",
            "command": {"raw": "private-original"}, "event": {"raw": "private-original"}, "proposal": {"raw": "private-original"},
            "references": [{"id": "ancestor", "report_id": "ancestor-report", "revision": 1,
                            "source_sha256": "c" * 64, "records": [{"raw": "private-original"}]}],
            "retrieval_comparison": {"attributes": {"block_id": {"current": "block-b", "case": "block-a", "raw": "private-original"},
                                                   "raw": {"current": "private-original", "case": "private-original"}},
                                     "different_attributes": ["block_id", "revision", "raw"],
                                     "eco": {"current": "eco04", "case": "eco03"}, "raw": "private-original"},
            "example_complete": True}


class SharedKnowledgeTests(unittest.TestCase):
    def test_allowlist_keeps_human_knowledge_and_counts_without_original_evidence(self):
        source = historical()
        result = shared_case(source)
        self.assertEqual(EVIDENCE_MODE, result["evidence_mode"])
        self.assertIs(result["example_complete"], False)
        self.assertEqual(100000, result["source_record_count"])
        self.assertEqual({"id": "historical-group", "label": "확정된 클록 분류", "record_count": 2}, result["groups"][0])
        self.assertEqual(result["groups"][0], result["group_snapshot"])
        self.assertEqual("사람이 확인한 공유 분류 근거", result["rationales"]["historical-group"]["content"]["reason"])
        self.assertEqual({"rule": "CLK_0003", "mode": "FUNC"}, result["conditions"])
        self.assertEqual({"rule": "CLK_0003", "reason": "검증된 예외", "conditions": "동일 연결 조건"}, result["exception"])
        self.assertNotIn("private-original", canonical_json(result).decode())
        self.assertNotIn("private-log-1", canonical_json(result).decode())
        self.assertNotIn("private-log-2", canonical_json(result).decode())

    def test_identity_digests_source_version_and_scope_are_preserved(self):
        source = historical()
        result = shared_case(source)
        for key in ("id", "report_id", "version_id", "revision", "source_sha256", "sha256", "actor", "at"):
            if key == "actor":
                self.assertEqual({key: source["actor"][key] for key in ("id", "name", "username", "role")}, result[key])
            else:
                self.assertEqual(source[key], result[key])
        self.assertEqual({key: source["source_version"][key] for key in
                          ("id", "project_id", "block_id", "net_ver", "revision", "layout", "ecos", "source_pre_id")}, result["source_version"])
        self.assertEqual("eco03", result["scope"]["eco"])
        self.assertEqual(["block_id", "revision"], result["retrieval_comparison"]["different_attributes"])
        self.assertEqual("c" * 64, result["references"][0]["source_sha256"])

    def test_projection_is_idempotent_and_does_not_mutate_input(self):
        source = historical()
        before = deepcopy(source)
        result = shared_case(source)
        self.assertEqual(before, source)
        self.assertEqual(result, shared_case(result))
        self.assertEqual(digest(result), digest(shared_case(result)))
        result["source_version"]["ecos"].append("extra")
        self.assertEqual(before, source)

    def test_count_can_be_derived_but_missing_identity_and_evidence_are_not_invented(self):
        result = shared_case({"id": "minimal", "records": [{"raw": "hidden"}]})
        self.assertEqual(1, result["source_record_count"])
        self.assertNotIn("source_sha256", result)
        self.assertNotIn("revision", result)
        self.assertNotIn("groups", result)
        self.assertEqual(result, shared_case(result))

    def test_browser_saved_references_are_projected_without_changing_current_target(self):
        target = {"id": "current", "report": {"records": [{"id": "current-log", "raw": "current-original"}]},
                  "group_snapshot": {"id": "current-group", "record_ids": ["current-log"]},
                  "rationale": {"content": {"reason": "current-reason", "evidence_ids": ["current-log"]}},
                  "proposal": {"reason": "current-proposal", "references": [historical()]},
                  "history": [{"id": "current-old-revision", "references": [historical()]}],
                  "cases": [historical()], "input_cases": [{"id": "case-identity", "revision": 3,
                                                              "source_sha256": "a" * 64, "raw": "private-original"}]}
        before = deepcopy(target)
        result = shared_case_views(target)
        self.assertEqual(before, target)
        for key in ("report", "group_snapshot", "rationale"):
            self.assertEqual(target[key], result[key])
        self.assertEqual("current-proposal", result["proposal"]["reason"])
        self.assertNotIn("private-original", canonical_json(result).decode())
        self.assertEqual(EVIDENCE_MODE, result["history"][0]["references"][0]["evidence_mode"])
        self.assertEqual(result, shared_case_views(result))

    def test_partial_rationale_reference_keeps_shared_group_reason_only(self):
        source = historical()
        result = shared_case_views({"references": [{"id": source["id"], "report_id": source["report_id"],
                                                    "group": source["groups"][0], "rationale": source["clustering_rationale"]}]})
        reference = result["references"][0]
        self.assertEqual(2, reference["group"]["record_count"])
        self.assertEqual("사람이 확인한 공유 분류 근거", reference["rationale"]["content"]["reason"])
        self.assertNotIn("private-original", canonical_json(reference).decode())

    def test_human_correction_keeps_only_safe_ai_identity_and_verdict(self):
        source = historical()
        source.update(proposal_conclusion="FAIL", proposal_agreed=False, ai_proposal={
            "id": "original-ai-proposal", "conclusion": "FAIL", "reason": "unconfirmed-private-ai-reason",
            "records": [{"raw": "private-original"}], "evidence_ids": ["private-log-1"],
            "references": [{"raw_report": "private-original"}]})
        before = deepcopy(source)
        result = shared_case(source)
        self.assertEqual("PASS", result["conclusion"])
        self.assertEqual("사람이 확정한 공유 판정 근거", result["reason"])
        self.assertEqual("FAIL", result["proposal_conclusion"])
        self.assertIs(result["proposal_agreed"], False)
        self.assertEqual({"id": "original-ai-proposal", "conclusion": "FAIL"}, result["ai_proposal"])
        self.assertNotIn("unconfirmed-private-ai-reason", canonical_json(result).decode())
        self.assertNotIn("private-original", canonical_json(result).decode())
        self.assertEqual(result, shared_case(result))
        self.assertEqual(before, source)
        own_proposal = {key: deepcopy(value) for key, value in source["ai_proposal"].items() if key != "references"}
        current = {"proposal": own_proposal, "ai_proposal": deepcopy(own_proposal), "cases": [source]}
        views = shared_case_views(current)
        self.assertEqual(current["proposal"], views["proposal"])
        self.assertEqual(current["ai_proposal"]["reason"], views["ai_proposal"]["reason"])
        self.assertEqual(result, views["cases"][0])

    def test_optional_correction_audit_accepts_null_but_never_coerces_values(self):
        result = shared_case({"id": "normal", "proposal_conclusion": None, "proposal_agreed": None})
        self.assertIsNone(result["proposal_conclusion"])
        self.assertIsNone(result["proposal_agreed"])
        invalid = shared_case({"id": "invalid", "proposal_conclusion": "OTHER", "proposal_agreed": 1,
                               "ai_proposal": {"id": "proposal", "conclusion": "OTHER", "reason": "hidden"}})
        self.assertNotIn("proposal_conclusion", invalid)
        self.assertNotIn("proposal_agreed", invalid)
        self.assertEqual({"id": "proposal"}, invalid["ai_proposal"])


if __name__ == "__main__":
    unittest.main()
