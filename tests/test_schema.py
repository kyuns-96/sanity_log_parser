"""Validate the GUI-only schema application layout."""
import hashlib
import unittest

from tests.support import ROOT


class SchemaTests(unittest.TestCase):
    def test_submitted_forms_and_original_setup_scripts_remain_unchanged(self):
        submitted = {
            "form/sanity_oa_data.columns.sql": "c62a2448cb55c1aa21dd757479032f52d2573b772bd8877a4393b1a647888c8c",
            "form/sanity_oa_to_vwp.columns.sql": "f3fc5267d7589d016e911b04adffbef3e55fb3817e6fff26cc924929a6f3caae",
            "form/sanity_vwp_data.columns.sql": "996c795f5224fde6f2a369df3ae97af7642f84726aeef5828ac110362ec21d99",
            "form/sanity_vwp_to_oa.columns.sql": "e43fdeff4d167c1428b6b846adfecdac4d8d38a9c9e659d0e1167703e31fe52e",
            "002_indexes.sql": "888f4eb71cb6c50c48aba793202fe829b60c99cec5c82a4493437bfeac0f93d2",
            "003_grants.sql": "b225cec979648c3a9f3dbb28941e1e017126f1833f15cfabd23283a83129c1d1",
        }
        for name, expected in submitted.items():
            with self.subTest(file=name):
                self.assertEqual(expected, hashlib.sha256((ROOT / "db" / name).read_bytes()).hexdigest(),
                                 "Already submitted SQL must stay immutable; use a new migration")

    def test_all_four_gui_forms_are_column_fragments(self):
        forms = {path.name.removesuffix(".columns.sql"): path
                 for path in (ROOT / "db/form").glob("*.columns.sql")}
        self.assertEqual({"sanity_vwp_data", "sanity_oa_data", "sanity_vwp_to_oa", "sanity_oa_to_vwp"}, set(forms))
        for name, path in forms.items():
            with self.subTest(table=name):
                body = path.read_text(encoding="utf-8").strip()
                self.assertTrue(body)
                self.assertIn("PRIMARY KEY", body)
                self.assertNotRegex(body, r"(?i)\bCREATE\s+TABLE\b|\b(?:BEGIN|COMMIT);|;")

    def test_only_required_post_creation_sql_is_shipped(self):
        self.assertEqual({"002_indexes.sql", "003_grants.sql", "004_clustering.sql", "005_workspace.sql", "006_workspace_reviews.sql", "007_workspace_pipeline.sql", "008_submissions.sql", "009_sso_identity.sql", "010_raw_sources.sql", "011_payload_storage.sql"},
                         {path.name for path in (ROOT / "db").glob("*.sql")})

    def test_new_oa_schema_contains_only_dashboard_records(self):
        form = (ROOT / "db/form/sanity_oa_data.columns.sql").read_text(encoding="utf-8")
        self.assertIn("'RECEIVED_BUNDLE', 'REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT'", form)
        self.assertIn("bundle_id VARCHAR(100) NOT NULL", form)
        for removed in ("JIRA", "part_number", "remote_id", "confirmed_at", "attempt_count", "lease_token"):
            self.assertNotIn(removed, form)
        self.assertNotIn("JIRA", (ROOT / "db/002_indexes.sql").read_text(encoding="utf-8"))
