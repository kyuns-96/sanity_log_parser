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
    "tests.test_auth", "tests.test_db_config", "tests.test_common", "tests.test_parsers",
    "tests.test_schema", "tests.test_submission_contracts", "tests.test_integration_contracts",
    "tests.test_mock_runner", "tests.test_test_runner", "oa.test_auth_config", "oa.test_sso",
    "tests.test_sso_identity_migration.SSOIdentityMigrationTests.test_008_checkpoint_runs_one_portal_statement_with_the_exact_original_009_catalog",
    "tests.test_sso_identity_migration.SSOIdentityMigrationTests.test_conflicting_insert_preserves_original_issuer_disabled_state_and_role_permissions",
    "tests.test_database.DatabaseTests.test_missing_configuration_and_admin_login_fail_closed",
    "tests.test_database.DatabaseTests.test_excess_column_rights_are_detected",
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
