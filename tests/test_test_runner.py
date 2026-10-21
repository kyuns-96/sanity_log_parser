"""Quick/full scope selection must never turn missing tests or failures green."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_tests as runner


class TestRunnerTests(unittest.TestCase):
    def test_quick_is_a_nonempty_subset_and_full_retains_large_and_portal_replays(self):
        quick = [test.id() for test in runner.test_cases(runner.build_suite("quick"))]
        full = [test.id() for test in runner.test_cases(runner.build_suite("full"))]
        self.assertTrue(quick)
        self.assertEqual(len(quick), len(set(quick)))
        self.assertLess(set(quick), set(full))
        self.assertFalse(any("100000" in name or "WorkspaceScaleTests" in name for name in quick))
        for name in (
            "oa.test_workspace_scale.WorkspaceScaleTests.test_100000_unique_groups_finish_all_records_resume_and_review_the_final_group",
            "tests.test_large_samples.LargePublicSampleTests.test_each_single_100000_record_report_retains_every_id_and_original_location",
            "tests.test_portal_runbook.PortalDatabaseTests.test_portal_autocommit_steps_match_original_sql_preserve_gui_names_fk_options_and_acls",
        ):
            self.assertIn(name, full)
            self.assertNotIn(name, quick)
        self.assertFalse(any("_FailedTest" in name for name in full + quick))

    def test_default_quick_records_success_with_its_actual_scope(self):
        case = unittest.FunctionTestCase(lambda: None)
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "report.json"
            with patch.object(runner, "build_suite", return_value=unittest.TestSuite([case])) as build, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(0, runner.main(["--report-json", str(report)]))
            build.assert_called_once_with("quick")
            data = json.loads(report.read_text())
            self.assertEqual(("quick", False, 1, 1, True),
                             (data["profile"], data["full_suite"], data["tests_selected"],
                              data["tests_run"], data["successful"]))

    def test_missing_quick_selector_is_an_error_instead_of_a_silent_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "report.json"
            with patch.object(runner, "QUICK_TESTS", ("tests.missing_quick_test_module",)), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(1, runner.main(["--report-json", str(report)]))
            data = json.loads(report.read_text())
            self.assertFalse(data["successful"])
            self.assertEqual(1, data["errors"])
            self.assertEqual(0, data["skipped"])

    def test_require_db_remains_required_for_the_quick_profile(self):
        missing = {name: "" for name in ("SANITY_TEST_ADMIN_DSN", "SANITY_VWP_DSN", "SANITY_OA_DSN")}
        with patch.dict(os.environ, missing), patch.object(runner, "build_suite") as build, \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                runner.main(["--require-db"])
        self.assertEqual(2, error.exception.code)
        build.assert_not_called()

    def test_list_only_never_executes_a_test(self):
        def must_not_run():
            self.fail("Listing ran a test")
        case = unittest.FunctionTestCase(must_not_run)
        output = io.StringIO()
        with patch.object(runner, "build_suite", return_value=unittest.TestSuite([case])), redirect_stdout(output):
            self.assertEqual(0, runner.main(["--list"]))
        self.assertIn("profile=quick tests_selected=1 full_suite=false", output.getvalue())
        self.assertIn(case.id(), output.getvalue())
