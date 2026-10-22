"""Synthetic approved-criteria catalog boundary checks, without model or DB calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from oa.sanity_ai_criteria import load_criteria, REQUIRED_SCOPE, MAX_CATALOG_BYTES


class ApprovedCriteriaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "criteria.json"
        self.settings = {"SANITY_OA_AI_CRITERIA_FILE": str(self.path)}
        self.context = {"scope": {key: "synthetic-" + key for key in REQUIRED_SCOPE}}
        self.criterion = {"id": "synthetic-rule", "version": "1", "text": "합성 조건을 만족하는지 모든 로그를 확인한다.",
                          "scope": self.context["scope"].copy()}

    def write(self, criteria):
        self.path.write_text(json.dumps({"schema_version": 1, "criteria": criteria}, ensure_ascii=False), encoding="utf-8")

    def test_missing_catalog_has_no_invented_criterion(self):
        self.assertEqual([], load_criteria(self.context, {}))
        with self.assertRaises(ReviewError) as error:
            load_criteria(self.context, self.settings)
        self.assertEqual("INVALID_AI_CRITERIA", error.exception.code)
        self.assertNotIn(str(self.path), str(error.exception))

    def test_exact_registered_scope_and_optional_eco_are_respected(self):
        other = copy.deepcopy(self.criterion)
        other["scope"]["block_id"] = "different-synthetic-block"
        eco = copy.deepcopy(self.criterion)
        eco.update(id="eco-rule", scope={**eco["scope"], "eco": "E1"})
        self.write([self.criterion, other, eco])
        self.assertEqual([self.criterion], load_criteria(self.context, self.settings))
        scoped = {"scope": {**self.context["scope"], "eco": "E1"}}
        self.assertEqual([self.criterion, eco], load_criteria(scoped, self.settings))

    def test_unscoped_overlapping_and_malformed_catalogs_fail_closed(self):
        broad = {**self.criterion, "scope": {"item_id": "only-item"}}
        malformed_other = copy.deepcopy(self.criterion)
        malformed_other.update(text="", scope={**malformed_other["scope"], "block_id": "elsewhere"})
        for criteria in ([broad], [self.criterion, {**self.criterion, "version": "2"}], [malformed_other]):
            with self.subTest(criteria=criteria):
                self.write(criteria)
                with self.assertRaises(ReviewError):
                    load_criteria(self.context, self.settings)

    def test_duplicate_json_keys_constants_and_size_overflow_are_rejected(self):
        for raw in ('{"schema_version":1,"criteria":[],"criteria":[]}',
                    '{"schema_version":NaN,"criteria":[]}', ' ' * (MAX_CATALOG_BYTES + 1)):
            self.path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ReviewError) as error:
                load_criteria(self.context, self.settings)
            self.assertEqual("INVALID_AI_CRITERIA", error.exception.code)

    def test_all_oa_settings_are_loaded_only_from_the_explicit_environment_file(self):
        path = Path(self.temp.name) / "test.env"
        path.write_text("SANITY_OA_AI_ENABLED=false\nSANITY_OA_AI_URL=https://synthetic.invalid/v1/responses\n"
                        "SANITY_OA_AI_CRITERIA_FILE=/synthetic/criteria.json\nUNRELATED=ignored\n", encoding="utf-8")
        from unittest.mock import patch
        with patch.dict("os.environ", {}, clear=True):
            settings = load_environment(path)
        self.assertEqual("false", settings["SANITY_OA_AI_ENABLED"])
        self.assertEqual("/synthetic/criteria.json", settings["SANITY_OA_AI_CRITERIA_FILE"])
        self.assertNotIn("UNRELATED", settings)
