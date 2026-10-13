"""Validate the GUI-only schema application layout."""
import unittest

from tests_support import ROOT


class SchemaTests(unittest.TestCase):
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
        self.assertEqual({"002_indexes.sql", "003_grants.sql"},
                         {path.name for path in (ROOT / "db").glob("*.sql")})

    def test_new_oa_schema_contains_only_dashboard_records(self):
        form = (ROOT / "db/form/sanity_oa_data.columns.sql").read_text(encoding="utf-8")
        self.assertIn("'RECEIVED_BUNDLE', 'REVIEW_UNIT', 'REVIEW_EVENT', 'REPORT'", form)
        self.assertIn("bundle_id VARCHAR(100) NOT NULL", form)
        for removed in ("JIRA", "part_number", "remote_id", "confirmed_at", "attempt_count", "lease_token"):
            self.assertNotIn(removed, form)
        self.assertNotIn("JIRA", (ROOT / "db/002_indexes.sql").read_text(encoding="utf-8"))
