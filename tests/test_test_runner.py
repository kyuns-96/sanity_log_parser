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
    def test_regression_preserves_previous_scope_and_includes_quick_without_duplicates(self):
        quick = {test.id() for test in runner.test_cases(runner.build_suite("quick"))}
        previous = {test.id() for test in runner.test_cases(unittest.TestLoader().loadTestsFromNames(runner.REGRESSION_TESTS))}
        regression = [test.id() for test in runner.test_cases(runner.build_suite("regression"))]
        full = {test.id() for test in runner.test_cases(runner.build_suite("full"))}
        self.assertEqual(quick | previous, set(regression))
        self.assertEqual(len(regression), len(set(regression)))
        self.assertLess(quick, set(regression))
        self.assertLess(set(regression), full)
        self.assertFalse(any("_FailedTest" in name for name in regression))

    def test_quick_keeps_known_failures_and_synthetic_file_security_checks_visible(self):
        quick = {test.id() for test in runner.test_cases(runner.build_suite("quick"))}
        for name in (
            "tests.test_submission_contracts.SubmissionContractTests.test_hardlinks_symlinks_and_changed_snapshot_are_rejected",
            "tests.test_submission_https.HttpsSubmissionTests.test_transport_config_is_explicit_https_and_receipts_are_bound_to_the_owner",
            "tests.test_submission_files.SyntheticSubmissionMetadataTests.test_synthetic_single_link_file_keeps_exact_bytes",
            "tests.test_submission_files.SyntheticSubmissionMetadataTests.test_synthetic_root_owned_safe_configuration_loads",
            "tests.test_sso_identity_migration.SSOIdentityMigrationTests.test_008_checkpoint_runs_one_portal_statement_with_the_exact_original_009_catalog",
            "tests.test_raw_sources_migration.RawSourcesMigrationTests.test_009_checkpoint_executes_four_portal_statements_with_the_original_010_catalog",
            "tests.test_payload_storage_migration.PayloadStorageMigrationTests.test_checkpoint_59_prepares_and_verifies_the_exact_original_011_catalog",
            "tests.test_evidence_pages_migration.EvidencePagesMigrationTests.test_checkpoint_60_applies_only_012_with_exact_original_catalog",
            "tests.test_shared_source_migration.SharedPacketMigrationTests.test_checkpoint_62_runs_exact_original_013_catalog",
        ):
            self.assertIn(name, quick)

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
        for name in (
            "tests.test_submission_tokenless.TokenlessSubmissionTests.test_real_tls_without_token_reaches_pending_then_sso_owner_publishes",
            "tests.test_shared_source_packets.SharedSourceSubmissionFlows.test_tokenless_sso_owner_confirmation_keeps_full_shared_evidence",
            "tests.test_shared_source_packets.SharedSourceSubmissionFlows.test_collection_only_owner_intake_and_human_review_with_shared_bodies",
            "tests.test_shared_source_packets.SharedSourceSubmissionFlows.test_collection_only_marker_tampering_is_rejected_with_shared_bodies",
        ):
            self.assertIn(name, quick)
        for name in (
            "tests.test_payload_storage.CompactSubmissionFlows.test_tokenless_tls_and_sso_owner_confirmation_with_compressed_storage",
            "tests.test_evidence_pages.PagedSubmissionFlows.test_tokenless_tls_and_sso_owner_confirmation_with_pages",
            "tests.test_raw_sources.RawSourcesDatabaseTests.test_real_tls_db_originals_reach_oa_and_receipts_survive_without_server_files",
            "tests.test_submission_https_database.HttpsSubmissionDatabaseTests.test_real_tls_upload_reaches_oa_and_returns_its_verified_receipt_without_shared_storage",
            "tests.test_submission_https_database.HttpsSubmissionDatabaseTests.test_worker_resumes_partial_analysis_from_the_private_http_archive_after_restart",
            "tests.test_submission_tokenless.TokenlessSubmissionTests.test_collection_only_runs_owner_intake_ai_and_human_review_without_changing_source",
            "tests.test_submission_tokenless.TokenlessSubmissionTests.test_collection_only_receiver_rejects_scope_producer_and_count_tampering",
        ):
            self.assertIn(name, full)
            self.assertNotIn(name, quick)

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
            self.assertGreaterEqual(data["seconds"], 0)
            self.assertEqual([case.id()], [row["test"] for row in data["timings"]])
            self.assertGreaterEqual(data["timings"][0]["seconds"], 0)

    def test_negative_duration_count_is_rejected_before_loading_tests(self):
        with patch.object(runner, "build_suite") as build, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                runner.main(["--durations", "-1"])
        self.assertEqual(2, error.exception.code)
        build.assert_not_called()

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
