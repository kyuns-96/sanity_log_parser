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
    def test_include_unions_overlapping_modules_and_methods_without_repeating_tests(self):
        name = 'tests.test_ai_parallel_width.ParallelWidthTests'
        baseline = unittest.defaultTestLoader.loadTestsFromName(name + '.test_running_status_accepts_nine_sixteen_and_thirty_two_reservations')
        expected = {test.id() for test in runner.test_cases(unittest.defaultTestLoader.loadTestsFromName(name))}
        suite = runner.include_tests(baseline, [name, name, next(iter(expected))])
        ids = [test.id() for test in runner.test_cases(suite)]
        self.assertEqual(expected, set(ids))
        self.assertEqual(len(expected), len(ids))
        shards = [test.id() for i in range(3) for test in runner.test_cases(runner.shard_suite(suite, i, 3))]
        self.assertCountEqual(ids, shards)

    def test_include_manifest_records_the_added_scope_without_running_it(self):
        name = 'tests.test_ai_parallel_width.ParallelWidthTests'
        case = unittest.FunctionTestCase(lambda: self.fail('Manifest ran a baseline test'))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'manifest.json'
            with patch.object(runner, 'build_suite', return_value=unittest.TestSuite([case])), redirect_stdout(io.StringIO()):
                self.assertEqual(0, runner.main(['--include', name, name, '--manifest-json', str(path)]))
            manifest = json.loads(path.read_text())
            self.assertEqual([name], manifest['include'])
            self.assertEqual(('quick', False, 5), (manifest['profile'], manifest['full_suite'], manifest['tests_selected']))
            self.assertIn(case.id(), manifest['test_ids'])

    def test_missing_empty_or_nonpublic_include_cannot_silently_run_only_the_baseline(self):
        for name in ('tests.missing', 'oa.integration.company_sso.test_adapter', 'tests.test_missing_extra_module'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner.include_tests(unittest.TestSuite(), [name])
        with patch.object(unittest.TestLoader, 'loadTestsFromName', return_value=unittest.TestSuite()), self.assertRaises(ValueError):
            runner.include_tests(unittest.TestSuite(), ['tests.test_empty'])

    def test_expensive_flows_stay_in_regression_and_full_with_runtime_smoke_in_quick(self):
        quick = {test.id() for test in runner.test_cases(runner.build_suite('quick'))}
        regression = {test.id() for test in runner.test_cases(runner.build_suite('regression'))}
        full = {test.id() for test in runner.test_cases(runner.build_suite('full'))}
        for name in (
            'tests.test_ai_parallel_width.ParallelWidthDatabaseTests.test_global_32_jobs_16_displays_live_calls_and_drains_after_budget_reduction',
            'tests.test_ai_evidence_batches.EvidenceTextDatabaseTests.test_real_source_packet_reader_and_ledger_cover_text_for_classification_and_review',
        ):
            self.assertNotIn(name, quick)
            self.assertIn(name, regression)
            self.assertIn(name, full)
        self.assertIn('tests.test_ai_parallel_width.ParallelWidthTests.test_running_status_accepts_nine_sixteen_and_thirty_two_reservations', quick)
        self.assertIn('tests.test_ai_parallel_batches.ParallelVwpJobsTests.test_true_overlap_out_of_order_commit_and_merge_waits_for_all_siblings', quick)
        self.assertIn('tests.test_ai_rolling_batches.RollingJobsTests.test_eight_slots_refill_while_the_first_seven_are_blocked_and_merge_waits', quick)

    def test_shards_execute_the_exact_original_scope_once(self):
        class Sample(unittest.TestCase):
            def test_a(self): pass
            def test_b(self): pass
            def test_c(self): pass
            def test_d(self): pass
            def test_e(self): pass

        original = unittest.defaultTestLoader.loadTestsFromTestCase(Sample)
        ids = [test.id() for test in runner.test_cases(original)]
        shards = [[test.id() for test in runner.test_cases(runner.shard_suite(original, index, 3))]
                  for index in range(3)]
        self.assertEqual(sorted(ids), sorted(name for shard in shards for name in shard))
        self.assertTrue(all(set(shards[a]).isdisjoint(shards[b]) for a in range(3) for b in range(a)))
        self.assertEqual([2, 2, 1], [len(shard) for shard in shards])

    def test_duplicate_ids_and_invalid_shards_are_not_silently_discarded(self):
        case = unittest.FunctionTestCase(lambda: None)
        with self.assertRaises(ValueError):
            runner.shard_suite(unittest.TestSuite([case, case]), 0, 2)
        for value in ("0/2", "3/2", "1/0", "1/17", "1", "a/b"):
            with self.subTest(shard=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main(["--shard", value])

    def test_manifest_lists_scope_without_executing_or_claiming_a_full_run(self):
        case = unittest.FunctionTestCase(lambda: self.fail("Manifest ran a test"))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            with patch.object(runner, "build_suite", return_value=unittest.TestSuite([case])), redirect_stdout(io.StringIO()):
                self.assertEqual(0, runner.main(["--profile", "full", "--shard", "1/2", "--manifest-json", str(path)]))
            manifest = json.loads(path.read_text())
            self.assertEqual([case.id()], manifest["test_ids"])
            self.assertEqual(("full", 1, "1/2", False), (manifest["profile"], manifest["tests_total"], manifest["shard"], manifest["full_suite"]))

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
