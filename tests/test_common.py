import copy
import gzip
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sanity_common import (ReviewError, atomic_json, canonical_json, coverage, read_bundle,
                           read_json, render_report, safe_path, validate_payload, write_bundle,
                           pack_json, unpack_json, unpack_bundle, digest)


def pass_payload():
    return {
        "schema_version": 1, "bundle_id": "b-pass",
        "run": dict(run_id="r-1", project="test", design="chip", stage="sanity", tool="synthetic", tool_version="1"),
        "inventory": dict(confirmed=True, source="plan", version="1", sha256="0" * 64, expected_unit_ids=["u-1"]),
        "review_units": [dict(review_unit_id="u-1", item_id="numeric", report="reports/a.csv",
                              applicable=True, exclusion=None, execution_status="COMPLETED",
                              parse_status="COMPLETE", check_status="PASS", blockers=[],
                              record_count=1, evaluated_count=1, unknown_count=0,
                              status_counts=dict(PASS=1, FAIL=0, UNKNOWN=0), rule_version="1")],
        "source_files": [dict(file_id="f-1", path="reports/a.csv", size_bytes=20, sha256="0" * 64)],
        "finding_groups": [], "global_blockers": [], "producer": {"version": "prototype-1"}}


def finding_payload():
    payload = pass_payload()
    unit = payload["review_units"][0]
    unit.update(check_status="FAIL", status_counts=dict(PASS=0, FAIL=1, UNKNOWN=0),
                identity_fields=["object", "check_point", "mode"])
    payload["finding_groups"] = [{
        "finding_group_id": "g-1", "review_unit_id": "u-1", "item_id": "numeric",
        "message_code": "CHECK-1", "severity": "WARNING", "rule_version": "1",
        "identity_status": "SAME_FINDING", "identity": {"object": "top/A", "check_point": "P1", "mode": "M1"},
        "occurrence_count": 1, "status_counts": dict(PASS=0, FAIL=1, UNKNOWN=0),
        "examples": [{"record_id": "r-1", "file_id": "f-1", "line_start": 2, "line_end": 2,
                      "raw": "top/A,P1,M1,CHECK-1,WARNING,12,ns"}], "examples_omitted": 0}]
    return payload


class CommonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def decisions(self, bundle="b-pass"):
        return {"u-1": dict(bundle_id=bundle, reviewer="tester", reason="all records checked", conclusion="PASS")}

    def test_complete_requires_current_human_review(self):
        p = pass_payload()
        self.assertEqual(coverage(p)["review_status"], "INCOMPLETE")
        self.assertEqual(coverage(p, self.decisions("b-older"))["reviewed"], 0)
        self.assertEqual(coverage(p, self.decisions())["review_status"], "COMPLETE")
        p["inventory"]["confirmed"] = False
        self.assertEqual(coverage(p, self.decisions())["review_status"], "INCOMPLETE")

    def test_database_bytes_are_deterministic_and_bounded(self):
        value = {"text": "한글", "nested": {"b": 2, "a": 1}}
        packed = pack_json(value)
        self.assertEqual(packed, pack_json(value))
        self.assertEqual(value, unpack_json(packed))
        for modified in ({**packed, "payload_sha256": "0" * 64},
                         {**packed, "uncompressed_bytes": packed["uncompressed_bytes"] + 1},
                         {**packed, "uncompressed_bytes": True}):
            with self.assertRaises(ReviewError):
                unpack_json(modified)
        raw = gzip.compress(b" " * 10000, mtime=0)
        with self.assertRaises(ReviewError) as err:
            unpack_json({"payload_gzip": raw, "payload_sha256": hashlib.sha256(raw).hexdigest(),
                         "uncompressed_bytes": 1}, max_bytes=1000)
        self.assertEqual("SIZE_LIMIT", err.exception.code)

    def test_database_bundle_verifies_content_id_and_manifest_scope(self):
        value = pass_payload()
        value.pop("bundle_id")
        value["bundle_id"] = "b-" + digest(value)
        message = {"bundle_id": value["bundle_id"], "project": value["run"]["project"],
                   "run_id": value["run"]["run_id"], "payload": {"schema_version": 1,
                   "inventory_sha256": value["inventory"]["sha256"], "producer_version": value["producer"]["version"]},
                   **pack_json(value)}
        self.assertEqual(value, unpack_bundle(message))
        for modified in ({**message, "project": "other"}, {**message, "payload": {"schema_version": True}}):
            with self.assertRaises(ReviewError) as err:
                unpack_bundle(modified)
            self.assertEqual("INTEGRITY_ERROR", err.exception.code)
        value["run"]["tool_version"] = "changed"
        with self.assertRaises(ReviewError) as err:
            unpack_bundle({**message, **pack_json(value)})
        self.assertEqual("INTEGRITY_ERROR", err.exception.code)

    def test_omitted_unit_cannot_shrink_denominator(self):
        p = pass_payload()
        p["review_units"] = []
        with self.assertRaises(ReviewError):
            validate_payload(p)

    def test_source_gap_and_unexpected_source_are_derived(self):
        p = pass_payload()
        p["source_files"] = []
        c = coverage(p, self.decisions())
        self.assertEqual(c["review_status"], "INCOMPLETE")
        self.assertIn("SOURCE_REPORT_MISSING", c["units"][0]["blockers"])
        p = pass_payload()
        p["source_files"].append(dict(file_id="f-2", path="reports/extra.csv", size_bytes=1, sha256="1" * 64))
        self.assertIn("UNEXPECTED_REPORTS", coverage(p)["global_blockers"])

    def test_failed_execution_cannot_be_excluded(self):
        p = pass_payload()
        unit = p["review_units"][0]
        unit.update(applicable=False, execution_status="FAILED", parse_status="NOT_APPLICABLE",
                    check_status="NOT_APPLICABLE", record_count=0, evaluated_count=0,
                    status_counts=dict(PASS=0, FAIL=0, UNKNOWN=0),
                    exclusion=dict(reason="skip", approved_by="tester", reference="SCOPE-1"))
        self.assertEqual(coverage(p)["approved_na"], 0)
        self.assertEqual(coverage(p)["review_status"], "INCOMPLETE")

    def test_deterministic_bundle_and_tamper_detection(self):
        p = pass_payload()
        path = write_bundle(p, self.root)
        before = {f.name: f.read_bytes() for f in self.root.iterdir()}
        write_bundle(p, self.root)
        self.assertEqual(before, {f.name: f.read_bytes() for f in self.root.iterdir()})
        self.assertEqual(read_bundle(path), p)
        payload_path = self.root / "b-pass.json.gz"
        payload_path.write_bytes(payload_path.read_bytes()[:-1] + b"x")
        with self.assertRaises(ReviewError) as error:
            read_bundle(path)
        self.assertEqual(error.exception.code, "INTEGRITY_ERROR")

    def test_gzip_expansion_is_bounded_even_if_manifest_lies(self):
        path = write_bundle(pass_payload(), self.root)
        manifest = read_json(path)
        raw = gzip.compress(b" " * 10000, mtime=0)
        (self.root / manifest["payload_file"]).write_bytes(raw)
        manifest.update(size_bytes=len(raw), uncompressed_bytes=1, sha256=hashlib.sha256(raw).hexdigest())
        atomic_json(path, manifest)
        with self.assertRaises(ReviewError) as error:
            read_bundle(path, max_bytes=1000)
        self.assertEqual(error.exception.code, "SIZE_LIMIT")

    def test_json_ambiguity_and_paths_rejected(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '[1,2]'):
            path = self.root / "invalid.json"
            path.write_text(text)
            with self.assertRaises(ReviewError):
                read_json(path)
        for path in ("../escape", "/tmp/escape", "a/../b", "a\\b"):
            with self.assertRaises(ReviewError):
                safe_path(self.root, path)

    def test_same_id_conflicting_output_not_overwritten(self):
        write_bundle(pass_payload(), self.root)
        changed = pass_payload()
        changed["run"]["tool_version"] = "2"
        with self.assertRaises(ReviewError) as error:
            write_bundle(changed, self.root)
        self.assertEqual(error.exception.code, "BUNDLE_CONFLICT")

    def unchecked_bundle(self, payload):
        """Simulate a buggy sender with valid framing/checksum but invalid semantics."""
        raw = canonical_json(payload)
        compressed = gzip.compress(raw, mtime=0)
        filename = payload["bundle_id"] + ".json.gz"
        (self.root / filename).write_bytes(compressed)
        manifest = self.root / (payload["bundle_id"] + ".manifest.json")
        atomic_json(manifest, dict(schema_version=1, bundle_id=payload["bundle_id"],
                    run_id=payload["run"]["run_id"], payload_file=filename,
                    size_bytes=len(compressed), sha256=hashlib.sha256(compressed).hexdigest(),
                    uncompressed_bytes=len(raw)))
        return manifest

    def test_evidence_from_other_report_is_rejected_at_receive_boundary(self):
        payload = finding_payload()
        other = copy.deepcopy(payload["review_units"][0])
        other.update(review_unit_id="u-2", item_id="summary", report="reports/b.json",
                     check_status="PASS", status_counts=dict(PASS=1, FAIL=0, UNKNOWN=0))
        payload["review_units"].append(other)
        payload["inventory"]["expected_unit_ids"].append("u-2")
        payload["source_files"].append(dict(file_id="f-2", path="reports/b.json", size_bytes=10, sha256="1" * 64))
        payload["finding_groups"][0]["examples"][0]["file_id"] = "f-2"
        with self.assertRaises(ReviewError) as error:
            read_bundle(self.unchecked_bundle(payload))
        self.assertEqual(error.exception.code, "INVALID_SCHEMA")
        self.assertIn("Evidence source", str(error.exception))

    def test_incomplete_or_undeclared_identity_cannot_claim_same_finding(self):
        variants = ("missing_key", "extra_key", "missing_rule", "duplicate_rule")
        for variant in variants:
            with self.subTest(variant=variant):
                payload = finding_payload()
                identity = payload["finding_groups"][0]["identity"]
                unit = payload["review_units"][0]
                if variant == "missing_key":
                    del identity["mode"]
                elif variant == "extra_key":
                    identity["other"] = "unexpected"
                elif variant == "missing_rule":
                    del unit["identity_fields"]
                else:
                    unit["identity_fields"].append("object")
                with self.assertRaises(ReviewError) as error:
                    read_bundle(self.unchecked_bundle(payload))
                self.assertEqual(error.exception.code, "INVALID_SCHEMA")

    def test_invalid_object_type_rejected_but_isolated_unknown_identity_allowed(self):
        for value in (123, False, [], {}, None, " "):
            with self.subTest(value=value):
                payload = finding_payload()
                group = payload["finding_groups"][0]
                group["identity"]["object"] = value
                with self.assertRaises(ReviewError):
                    validate_payload(payload)
                group["identity_status"] = "UNDECIDED"
                validate_payload(payload)

    def test_candidate_truncation_is_visible_even_when_group_has_no_returned_candidate(self):
        context = {"jira": {"status": "MATCHED", "candidates": [],
                   "limits": {"per_group": 5, "total": 1000}, "total_matches": 20,
                   "returned_count": 0, "omitted_count": 20, "truncated": True,
                   "groups": [{"finding_group_id": "g-1", "total_matches": 20,
                               "returned_count": 0, "omitted_count": 20, "truncated": True}]}}
        report = render_report(finding_payload(), context=context)
        self.assertIn("검색 일치: 20", report)
        self.assertIn("생략: 20", report)
        self.assertIn("| g-1 | 20 | 0 | 20 |", report)
        self.assertIn("후보 상한으로 일부 결과를 생략", report)


if __name__ == "__main__":
    unittest.main()
