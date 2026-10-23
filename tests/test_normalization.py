"""Public normalization boundaries; every credential and report here is synthetic."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from common.sanity_common import ReviewError, digest
from common.sanity_privacy import MASK, SecretGuard
from common.sanity_submission import packet_base, scope_digest
from devtools.sanity_submission_fixture import fixture
from oa.submission_receiver import SourceReceiver, validate_record


class NormalizationTests(unittest.TestCase):
    def test_invalid_utf8_hex_is_masked_even_without_a_known_credential(self):
        secret = "SYNTHETIC_NORMALIZATION_ONLY_9876"
        for settings in ({}, {"SANITY_AI_TOKEN": secret}):
            guard = SecretGuard(settings)
            for original in (b"token=" + secret.encode() + b"\xff\n", b"\xff", b"unknown\x80"):
                with self.subTest(settings_known=bool(settings), original=original):
                    encoded = "hex:" + original.hex()
                    self.assertEqual(MASK, guard.text(encoded))
                    self.assertEqual({"raw": MASK}, guard.redact({"raw": encoded}))
                    with self.assertRaises(ReviewError) as error:
                        guard.ensure_public({"raw": encoded})
                    self.assertEqual("SENSITIVE_CONTENT", error.exception.code)
                    guard.ensure_public({"raw": MASK})

    def test_plaintext_and_valid_textual_hex_keep_their_existing_meaning(self):
        guard = SecretGuard({})
        for value in ("object=synthetic/A", "hex:", "hex:abc", "hex:zz", "hex:68656c6c6f",
                      "hex:c3a9", "hex:efbfbd"):
            with self.subTest(value=value):
                self.assertEqual(value, guard.text(value))
        self.assertEqual("token=" + MASK, guard.text("token=synthetic-credential"))
        self.assertEqual(MASK, guard.text("hex:FF"))

    def test_truncated_hex_examples_cannot_bypass_the_guard_with_a_valid_prefix(self):
        encoded = 'hex:' + b'token=SYNTHETIC_TRUNCATED_ONLY'.hex()
        example = {'raw':encoded,'raw_omitted_chars':2000,'line_start':1,'line_end':1}
        guard = SecretGuard({})
        self.assertEqual({**example,'raw':MASK},guard.redact(example))
        with self.assertRaises(ReviewError) as error:guard.ensure_public({'examples':[example]})
        self.assertEqual('SENSITIVE_CONTENT',error.exception.code)
        # Ordinary plaintext truncation and standalone textual hex keep their meaning.
        plain = {'raw':'synthetic report text','raw_omitted_chars':2000}
        self.assertEqual(plain,guard.redact(plain))
        self.assertEqual(encoded,guard.text(encoded))

    @staticmethod
    def record():
        raw = '{"object":"synthetic/A"}'
        return {"id": "r-" + "a" * 64, "evidence_id": "r-" + "a" * 64,
                "fields": {}, "check_status": "UNKNOWN", "raw": raw,
                "line_start": 1, "line_end": 1, "reasons": [], "redacted": False,
                "source_raw_sha256": hashlib.sha256(raw.encode()).hexdigest()}

    def test_receiver_accepts_optional_json_pointer_and_legacy_location(self):
        original = self.record()
        validate_record(original)
        self.assertNotIn("json_pointer", original)
        for pointer in ("/results/0", "/a~1b/~0key", "/", "/결과/0"):
            record = {**original, "json_pointer": pointer}
            before = copy.deepcopy(record)
            with self.subTest(pointer=pointer):
                validate_record(record)
                self.assertEqual(before, record)

    def test_receiver_rejects_malformed_json_pointers(self):
        for pointer in (None, 1, [], "", "results/0", "#/results/0", "/bad~", "/bad~2"):
            with self.subTest(pointer=pointer):
                with self.assertRaises(ReviewError) as error:
                    validate_record({**self.record(), "json_pointer": pointer})
                self.assertEqual("INVALID_SOURCE_RECORD", error.exception.code)

    def test_receiver_rejects_old_manifest_that_bypasses_numeric_evaluation(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = copy.deepcopy(fixture(Path(temp))["prepared"].plan)
        plan["inventory"]["units"][1]["parser"] = "summary_json_v1"
        plan["approval"]["scope_sha256"] = scope_digest(plan)
        output = {"plan": plan, "inventory_confirmed": True, "global_blockers": [],
                  "reports": [{"parser_contract": []}, {"parser_contract": []}], "producer": "postgresql-2"}
        submission = {"submission_id": "01800000-0000-4000-8000-000000000000",
                      "content_sha256": "b" * 64, "actor": plan["approval"]["actor"], "plan": plan}
        packet = {**packet_base(submission, "sc-" + digest([submission["content_sha256"], True,
                  output["producer"], [[], []]])), "body": output}
        receiver = SourceReceiver.__new__(SourceReceiver)
        receiver.header = Mock()
        with self.assertRaises(ReviewError) as error:
            receiver.validate_manifest(packet)
        self.assertEqual("PARSER_SCOPE_MISMATCH", error.exception.code)
