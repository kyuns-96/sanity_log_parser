#!/usr/bin/env python3
"""Run pure unit tests and configured PostgreSQL integration tests (never a local DB fallback)."""
import argparse
import json
import os
from pathlib import Path
import time
import unittest


ROOT = Path(__file__).resolve().parent
PROFILES = ("quick", "full")
# A short regression set for everyday edits. Keep expensive datasets and the
# complete portal migration replay in full, with their original assertions.
QUICK_TESTS = (
    "tests.test_auth", "tests.test_db_config", "tests.test_db_single_account", "tests.test_common", "tests.test_parsers",
    "tests.test_scope_contract", "tests.test_access_store.AccessConfigurationTests", "tests.test_access_api.AccessApiBoundaryTests",
    "tests.test_access_store.AccessStoreTests.test_top_and_owner_have_sql_scoped_lists_and_exact_bundle_permissions",
    "tests.test_scoped_dashboard.ScopedDashboardTests.test_admin_starts_without_business_grants_and_can_approve_own_top_request",
    "tests.test_scoped_dashboard.ScopedDashboardTests.test_response_id_alias_cannot_expose_another_blocks_evidence",
    "tests.test_schema", "tests.test_submission_contracts", "tests.test_normalization", "tests.test_integration_contracts",
    "tests.test_review_exceptions",
    "tests.test_submission_database.SubmissionDatabaseTests.test_summary_numeric_plan_is_rejected_without_publication",
    "tests.test_submission_database.SubmissionDatabaseTests.test_invalid_utf8_is_private_and_public_unknown_evidence_is_masked",
    "tests.test_submission_database.SubmissionDatabaseTests.test_json_pointers_survive_the_full_roundtrip_and_records_api",
    "tests.test_submission_database.SubmissionDatabaseTests.test_old_sensitive_outbox_is_blocked_without_rewriting_or_publication",
    "tests.test_submission_database.SubmissionDatabaseTests.test_old_numeric_summary_outbox_is_rejected_without_blocking_later_submissions",
    "tests.test_submission_database.SubmissionDatabaseTests.test_partial_outbox_from_another_revision_is_rejected_and_preserved",
    "tests.test_submission_database.SubmissionDatabaseTests.test_normalization_revision_preserves_previous_sources_and_human_decisions",
    "tests.test_submission_database.SubmissionDatabaseTests.test_receiver_rechecks_staged_numeric_manifest_before_commit",
    "tests.test_submission_database.SubmissionDatabaseTests.test_receiver_rechecks_staged_sensitive_parts_before_commit",
    "tests.test_ai_model.ReviewV2Tests.test_first_judgment_uses_direct_evidence_and_an_approved_criterion",
    "tests.test_oa_ai_feedback.OAFeedbackCycleTests.test_human_corrections_and_counterexamples_reach_the_next_eco_model_calls",
    "tests.test_mock_runner", "tests.test_test_runner", "oa.test_auth_config", "oa.test_sso",
    "tests.test_sso_identity_migration.SSOIdentityMigrationTests.test_008_checkpoint_runs_one_portal_statement_with_the_exact_original_009_catalog",
    "tests.test_sso_identity_migration.SSOIdentityMigrationTests.test_conflicting_insert_preserves_original_issuer_disabled_state_and_role_permissions",
    "tests.test_database.DatabaseTests.test_missing_configuration_and_admin_login_fail_closed",
    "tests.test_database.DatabaseTests.test_excess_column_rights_are_detected",
    "tests.test_database.DatabaseTests.test_destructive_and_ddl_related_rights_are_rejected_in_restricted_mode",
    "tests.test_database.DatabaseTests.test_private_payload_and_sender_ack_cannot_be_modified",
    "tests.test_database.DatabaseTests.test_message_idempotence_and_conflict",
    "tests.test_database.DatabaseTests.test_psycopg2_outer_rollback_and_readonly_do_not_leak",
    "oa.test_workspace_pipeline.WorkspacePipelineTests.test_only_a_confirmed_group_accepts_a_judgment_and_it_does_not_become_a_report_judgment",
    "oa.test_workspace_pipeline.WorkspacePipelineTests.test_top_scope_csrf_forged_actor_and_unconfigured_models_remain_guarded",
    "tests.test_portal_runbook.PortalRunbookTests.test_offline_cli_needs_only_standard_library_and_never_loads_a_db_driver",
    "tests.test_portal_runbook.PortalDatabaseTests.test_optional_pg15_nulls_flag_is_read_on_new_servers_and_false_on_pg12",
)


def build_suite(profile):
    loader = unittest.TestLoader()
    if profile == "quick":
        return loader.loadTestsFromNames(QUICK_TESTS)
    if profile != "full":
        raise ValueError("Unknown test profile")
    suite = unittest.TestSuite()
    for directory in ("tests", "vwp", "vwp_bridge", "oa"):
        suite.addTests(loader.discover(str(ROOT / directory), pattern="test_*.py", top_level_dir=str(ROOT)))
    return suite


def test_cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_cases(test)
        else:
            yield test


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="quick",
                        help="quick: core regressions (default); full: all tests including 100,000-record workloads")
    parser.add_argument("--list", action="store_true", help="list selected test IDs without executing them")
    parser.add_argument("--require-db", action="store_true", help="fail rather than skip PostgreSQL integration tests")
    parser.add_argument("--report-json", type=Path, help="write a new machine-readable result file (will not overwrite)")
    args = parser.parse_args(argv)
    if args.require_db and not all(os.environ.get(key) for key in ("SANITY_TEST_ADMIN_DSN", "SANITY_VWP_DSN", "SANITY_OA_DSN")):
        parser.error("--require-db needs SANITY_TEST_ADMIN_DSN, SANITY_VWP_DSN and SANITY_OA_DSN")
    if args.require_db:
        try:
            import psycopg2
        except ImportError:
            parser.error("--require-db needs psycopg2; install requirements.txt")
    suite = build_suite(args.profile)
    selected = suite.countTestCases()
    if not selected:
        parser.error("No tests selected")
    print(f"profile={args.profile} tests_selected={selected} full_suite={str(args.profile == 'full').lower()}", flush=True)
    if args.list:
        for test in test_cases(suite):
            print(test.id())
        return 0
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.report_json:
        report = {"profile": args.profile, "full_suite": args.profile == "full", "tests_selected": selected,
                  "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                  "skipped": len(result.skipped), "successful": result.wasSuccessful(),
                  "seconds": round(time.monotonic() - started, 3),
                  "failed_tests": [test.id() for test, _ in result.failures + result.errors]}
        with args.report_json.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
