import unittest

from devtools.sanity_large_samples import large_report


class LargePublicSampleTests(unittest.TestCase):
    def test_each_single_report_parses_all_10000_records_with_original_locations(self):
        for family in ("GCA", "VCLP", "SPYGLASS"):
            with self.subTest(family=family):
                report = large_report(family)
                self.assertEqual((10000, "COMPLETE", "FAIL"),
                                 (len(report["records"]), report["parse_status"], report["check_status"]))
                self.assertEqual(10000, len({record["id"] for record in report["records"]}))
                lines = report["raw_report"].splitlines()
                for record in (report["records"][0], report["records"][5000], report["records"][-1]):
                    self.assertEqual(record["raw"].strip(), "\n".join(lines[record["line_start"]-1:record["line_end"]]).strip())
                self.assertIn("10000", report["records"][-1]["id"])

    def test_unique_group_fixture_does_not_normalize_distinct_objects_away(self):
        report = large_report("GCA", count=10000, groups=10000)
        self.assertEqual(10000, len({record["fields"]["object"] for record in report["records"]}))

    def test_each_single_100000_record_report_retains_every_id_and_original_location(self):
        for family in ("GCA", "VCLP", "SPYGLASS"):
            with self.subTest(family=family):
                report = large_report(family, count=100000)
                self.assertEqual((100000, "COMPLETE", "FAIL"),
                                 (report["record_count"], report["parse_status"], report["check_status"]))
                self.assertEqual(100000, len({record["id"] for record in report["records"]}))
                lines = report["raw_report"].splitlines()
                for record in (report["records"][0], report["records"][50000], report["records"][-1]):
                    self.assertEqual(record["raw"].strip(), "\n".join(lines[record["line_start"]-1:record["line_end"]]).strip())
                self.assertTrue(report["records"][-1]["id"].endswith("-e-100000"))
                del report, lines
