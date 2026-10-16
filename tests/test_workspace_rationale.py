import copy
import unittest

from sanity_common import ReviewError, digest
from sanity_codex_mock import (CodexMockClusteringAdvisor, LEGACY_PROMPT_VERSION, PROMPT_VERSION,
                               RATIONALE_PROMPT_VERSION, request_packet, validate_receipt)
from sanity_workspace_clustering import validate_clustering_advice
import sanity_workspace_rationale as rationale
from test_codex_mock import answer, context, events, FIELDS
from unittest.mock import patch


class RationaleTests(unittest.TestCase):
    def event(self):
        return {"id": "event-1", "revision": 1, "source_sha256": "source", "actor": {"id": "person"},
                "at": "now", "model_called": True, "groups": [{"id": "g", "label": "Clocks", "record_ids": ["a", "b"]}]}

    def content(self):
        return {"reason": "Undefined clocks share a missing definition; unrelated data width errors belong elsewhere. Cause unverified.",
                "evidence_ids": ["a"], "reference_groups": []}

    def test_legacy_missing_does_not_become_a_reviewed_reason(self):
        event = self.event()
        self.assertEqual({"MISSING": 1, "DRAFT": 0, "CONFIRMED": 0, "STALE": 0}, rationale.counts(event))
        self.assertFalse(rationale.complete(event))
        self.assertIsNone(rationale.examples(event)[0]["rationale"])

    def test_all_and_only_own_group_original_ids_may_be_representatives(self):
        for ids in ([], ["foreign"], ["a", "a"], ["a"]*21):
            with self.subTest(ids=ids), self.assertRaises(ReviewError):
                rationale.validate_content({**self.content(), "evidence_ids": ids}, self.event()["groups"][0], [])
        self.assertEqual(self.content(), rationale.validate_content(self.content(), self.event()["groups"][0], []))

    def test_unchanged_review_carries_lineage_and_moves_or_renames_become_stale(self):
        event = self.event(); event["rationales"] = rationale.carry(None, event, {"g": self.content()})
        rationale.adopt(event["rationales"]["g"], event, event["groups"][0])
        original = digest(event)
        for changed in (False, "label", "record_ids"):
            next_event = copy.deepcopy(event); next_event.update(id="event-2", revision=2)
            if changed == "label": next_event["groups"][0]["label"] = "Different name"
            if changed == "record_ids": next_event["groups"][0]["record_ids"].append("c")
            next_event["rationales"] = rationale.carry(event, next_event)
            self.assertEqual("STALE" if changed else "CONFIRMED", rationale.state(next_event, next_event["groups"][0]))
            self.assertEqual("event-1", next_event["rationales"]["g"]["previous_event_id"])
            self.assertEqual(self.content(), rationale.materialized(next_event["rationales"]["g"])["proposal"]["content"])
            self.assertEqual(original, digest(event))

    def test_ai_must_explain_every_group_and_cannot_cite_an_unavailable_group(self):
        ctx = context(); value = answer(ctx)
        bad = copy.deepcopy(value); bad["group_rationales"].pop()
        with self.assertRaises(ReviewError): validate_clustering_advice(bad, ctx, [], require_rationales=True)
        bad = copy.deepcopy(value); bad["group_rationales"][0]["evidence_ids"] = value["groups"][1]["record_ids"]
        with self.assertRaises(ReviewError): validate_clustering_advice(bad, ctx, [])

        bad = copy.deepcopy(value); bad["group_rationales"][0]["reference_groups"] = [{"case_id": "invented", "group_id": "g", "reason": "guess"}]
        with self.assertRaises(ReviewError): validate_clustering_advice(bad, ctx, [])

    def test_chunk_storage_preserves_all_content_and_pinned_group_lookup(self):
        event = self.event()
        event["rationales"] = rationale.carry(None, event, {"g": self.content()})
        with patch.object(rationale, "PACK_THRESHOLD", 1):
            packed = rationale.pack(event)
        self.assertEqual(event, rationale.unpack(packed))
        key = rationale.bucket("g")
        self.assertEqual(event["rationales"]["g"], rationale.unpack_group("g", key, packed["rationale_chunks"][key]))
        self.assertEqual(event["rationales"]["g"]["content"], self.content())
        broken = copy.deepcopy(packed); broken["rationales"]["g"] = "invalid"
        with self.assertRaises(ReviewError): rationale.unpack(broken)
        broken = copy.deepcopy(packed); broken["rationale_chunks"][key] += "invalid"
        with self.assertRaises(ReviewError): rationale.unpack(broken)
        with patch.object(rationale, "PACK_THRESHOLD", 1), patch.object(rationale, "MAX_RATIONALE_BYTES", 1), self.assertRaises(ReviewError):
            rationale.pack(event)

    def test_legacy_receipts_keep_their_frozen_prompt_but_new_model_calls_require_v2(self):
        ctx = context(); value = answer(ctx); old = copy.deepcopy(value); old.pop("group_rationales")
        advisor = CodexMockClusteringAdvisor(enabled=True, executable="/unused/codex", transport=lambda *_: events(value))
        advisor.propose(ctx, [], FIELDS)
        v2 = copy.deepcopy(advisor.receipt)
        v2["metadata"].update(prompt_version=RATIONALE_PROMPT_VERSION,
            input_sha256=digest(request_packet(ctx, [], FIELDS, version=RATIONALE_PROMPT_VERSION)))
        self.assertEqual(value, validate_receipt(v2, ctx, [], FIELDS))
        receipt = copy.deepcopy(advisor.receipt)
        receipt["advice"] = old
        receipt["metadata"].update(prompt_version=LEGACY_PROMPT_VERSION,
            input_sha256=digest(request_packet(ctx, [], FIELDS, version=LEGACY_PROMPT_VERSION)))
        self.assertEqual(old, validate_receipt(receipt, ctx, [], FIELDS))
        receipt["metadata"].update(prompt_version=PROMPT_VERSION, input_sha256=digest(request_packet(ctx, [], FIELDS)))
        with self.assertRaises(ReviewError): validate_receipt(receipt, ctx, [], FIELDS)

    def test_next_ai_input_includes_final_human_explanation_correction_and_original(self):
        ctx = context(); case = {**self.event(), "id": "case", "report_id": ctx["report"]["id"],
            "confirmed": True, "scope": ctx["scope"], "source_sha256": ctx["source_sha256"],
            "records": ctx["report"]["records"], "reason": "memo", "criteria": ""}
        case["groups"] = answer(ctx)["groups"]
        proposed = {r["group_id"]: {k:v for k,v in r.items() if k != "group_id"} for r in answer(ctx)["group_rationales"]}
        case["rationales"] = rationale.carry(None, case, proposed)
        row = case["rationales"]["clock"]
        row["content"] = {**row["content"], "reason": "Human final grouping explanation"}
        row["change_reason"] = "Corrected the initial model explanation"
        rationale.adopt(row, case, case["groups"][0])
        packet = request_packet(context("codex-mock-feedback"), [case], FIELDS)
        for expected in ("Human final grouping explanation", "Corrected the initial model explanation", "proposal"):
            self.assertIn(expected, packet)
        row.update(reviewed_at="different import clock", edited_at="another clock", edited_by={"id": "synthetic"})
        self.assertEqual(packet, request_packet(context("codex-mock-feedback"), [case], FIELDS))
        row["content"]["reason"] += " with a new human correction"
        self.assertNotEqual(packet, request_packet(context("codex-mock-feedback"), [case], FIELDS))


if __name__ == "__main__": unittest.main()
