#!/usr/bin/env python3
"""Run pure unit tests and configured PostgreSQL integration tests (never a local DB fallback)."""
import argparse
import json
import os
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent
PROFILES = ("quick", "regression", "full")
ROLES = ("vwp", "vwp-bridge", "oa")


# The public suite keeps one owner for each retained behavior. Full discovery
# also includes the complete portal replay and the 100,000-record model flow.
QUICK_TESTS = (
    "tests.test_auth", "tests.test_submission_contracts", "tests.test_submission_https",
    "tests.test_submission_files", "tests.test_submission_tokenless.InternalIntakeContracts",
    "tests.test_parsers", "tests.test_public_parsers", "tests.test_ai_model",
    "tests.test_ai_gateway", "tests.test_ai_http", "tests.test_payload_storage.PayloadCodecTests",
    "oa.test_sso", "tests.test_access_store", "tests.test_database", "tests.test_ai_jobs",
    "tests.test_ai_batches.BatchDatabaseTests", "tests.test_ai_parallel_batches.ParallelVwpJobsTests",
    "tests.test_ai_rolling_batches.RollingJobsTests", "tests.test_ai_unknown_recovery",
    "tests.test_ai_resume_cancellation", "tests.test_ai_iteration_completion", "tests.test_ai_scheduler",
    "tests.test_ai_job_deletion", "tests.test_workspace_removal", "tests.test_ai_connections",
    "tests.test_ai_evidence_choices", "tests.test_rule_groups", "tests.test_ai_input_projection",
    "tests.test_ai_cache_transport", "tests.test_oa_ai_feedback", "tests.test_mock_runner",
    "tests.test_test_runner", "tests.test_role_runner", "vwp_bridge.test_corporate_parsers",
)
REGRESSION_TESTS = (
    "tests.test_raw_sources", "tests.test_submission_tokenless.TokenlessSubmissionTests",
    "tests.test_evidence_pages.EvidencePageDatabaseTests", "tests.test_preserved_text.PreservedTextOwnerTests",
)

# Role commands add only that role's company adapters. Public file-security
# contracts keep their native filesystem checks in both role and global runs.
ROLE_TESTS = {
    "vwp": ("tests.test_submission_contracts", "tests.test_submission_files"),
    "vwp-bridge": (
        "tests.test_parsers", "tests.test_public_parsers", "tests.test_submission_https",
        "tests.test_submission_tokenless.InternalIntakeContracts", "tests.test_ai_gateway",
        "tests.test_ai_model", "tests.test_ai_http",
    ),
    "oa": ("tests.test_auth", "oa.test_sso", "tests.test_ai_model", "tests.test_ai_gateway"),
}
ROLE_DB_TESTS = {
    "vwp-bridge": (
        "vwp_bridge.test_corporate_parsers", "tests.test_ai_parallel_batches.ParallelVwpJobsTests",
        "tests.test_ai_rolling_batches.RollingJobsTests", "tests.test_ai_cache_transport",
        "tests.test_ai_input_projection", "tests.test_ai_scheduler", "tests.test_ai_job_deletion",
    ),
    "oa": (
        "tests.test_oa_ai_feedback", "tests.test_ai_connections", "tests.test_ai_evidence_choices",
        "tests.test_workspace_removal",
    ),
}
ROLE_ADAPTER_DIRS = {
    "vwp": ("vwp/integration/company_plan",),
    "vwp-bridge": tuple("vwp_bridge/integration/company_" + name
                        for name in ("parsers", "submission", "clustering", "review")),
    "oa": ("oa/integration/company_sso",),
}


class FailedCompanyImport(unittest.TestCase):
    def __init__(self, module, error):
        super().__init__("runTest")
        self.module, self.error = module, error

    def id(self):
        return self.module + ".__import__"

    def runTest(self):
        raise self.error


def build_role_suite(role, *, require_db=False):
    if role not in ROLES or (require_db and role not in ROLE_DB_TESTS):
        raise ValueError("Unknown role or a role without DB tests")
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(ROLE_TESTS[role] + (ROLE_DB_TESTS[role] if require_db else ()))
    company = []
    for directory in ROLE_ADAPTER_DIRS[role]:
        folder = ROOT / directory
        ids = []
        # Import by qualified name: different company directories may contain
        # identically named test files, including namespace packages without
        # __init__.py. Ordinary quick/regression selection is unchanged.
        files = sorted(folder.rglob("test_*.py"))
        for filename in files:
            parts = filename.relative_to(ROOT).with_suffix("").parts
            if not all(part.isidentifier() for part in parts):
                raise ValueError("Company test paths must be importable Python module names")
            module = ".".join(parts)
            try:
                tests = loader.loadTestsFromName(module)
            except Exception as error:
                tests = unittest.TestSuite([FailedCompanyImport(module, error)])
            ids.extend(test.id() for test in test_cases(tests))
            suite.addTests(tests)
        if files and not ids:
            raise ValueError("A company test directory contains no tests")
        company.append({"directory": directory, "test_ids": ids})
    return suite, company


def build_suite(profile):
    loader = unittest.TestLoader()
    if profile == "quick":
        return loader.loadTestsFromNames(QUICK_TESTS)
    if profile == "regression":
        suite = unittest.TestSuite()
        seen = set()
        for test in test_cases(loader.loadTestsFromNames((*QUICK_TESTS, *REGRESSION_TESTS))):
            if test.id() not in seen:
                suite.addTest(test)
                seen.add(test.id())
        return suite
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


def include_tests(suite, names):
    """Add public change-specific tests once, retaining the selected baseline."""
    cases = list(test_cases(suite))
    seen = {test.id() for test in cases}
    if len(seen) != len(cases):
        raise ValueError("Duplicate test IDs in the selected profile")
    loader = unittest.TestLoader()
    for name in dict.fromkeys(names):
        parts = name.split('.')
        if (len(parts) < 2 or parts[0] not in ('tests', 'oa', 'vwp', 'vwp_bridge')
                or not parts[1].startswith('test_') or not all(part.isidentifier() for part in parts)):
            raise ValueError("--include requires public dotted test module/class/method names")
        extra = list(test_cases(loader.loadTestsFromName(name)))
        if loader.errors or not extra:
            raise ValueError("--include did not load tests: " + name)
        for test in extra:
            if test.id() not in seen:
                seen.add(test.id())
                cases.append(test)
    return unittest.TestSuite(cases)


def shard_suite(suite, index, count):
    """Disjoint, deterministic shards; each original test ID belongs to one worker."""
    cases = list(test_cases(suite))
    if len({test.id() for test in cases}) != len(cases):
        raise ValueError("Duplicate test IDs in the selected profile")
    if count == 1:
        return suite
    return unittest.TestSuite(sorted(cases, key=lambda test: test.id())[index::count])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="quick",
                        help="quick: three-minute core checks (default); regression: broader checks; full: all tests including 100,000-record workloads")
    parser.add_argument("--role", choices=ROLES,
                        help="only this owner's quick contracts and company tests; DB tests require --require-db")
    parser.add_argument("--include", action="extend", nargs="+", default=[],
                        help="add public test modules/classes/methods to the profile, without duplicate execution")
    parser.add_argument("--list", action="store_true", help="list selected test IDs without executing them")
    parser.add_argument("--require-db", action="store_true", help="fail rather than skip PostgreSQL integration tests")
    parser.add_argument("--report-json", type=Path, help="write a new machine-readable result file (will not overwrite)")
    parser.add_argument("--shard", default="1/1", help="run disjoint shard INDEX/COUNT (one-based)")
    parser.add_argument("--manifest-json", type=Path, help="write selected test IDs without running tests")
    args = parser.parse_args(argv)
    if args.role and args.profile != "quick":
        parser.error("--role selects quick role checks; run regression/full without --role for integrated validation")
    if args.role and args.include:
        parser.error("--include cannot extend an owner's --role scope")
    args.include = list(dict.fromkeys(args.include))
    if args.role == "vwp" and args.require_db:
        parser.error("VWP collection has no DB tests; run --role vwp without --require-db")
    try:
        shard_index, shard_count = map(int, args.shard.split("/"))
        if not 1 <= shard_index <= shard_count <= 16:
            raise ValueError
    except ValueError:
        parser.error("--shard requires 1 <= INDEX <= COUNT <= 16")
    if args.require_db and not all(os.environ.get(key) for key in ("SANITY_TEST_ADMIN_DSN", "SANITY_VWP_DSN", "SANITY_OA_DSN")):
        parser.error("--require-db needs SANITY_TEST_ADMIN_DSN, SANITY_VWP_DSN and SANITY_OA_DSN")
    if args.require_db:
        try:
            import psycopg2
        except ImportError:
            parser.error("--require-db needs psycopg2; install requirements.txt")
    company = []
    if args.role:
        try:
            suite, company = build_role_suite(args.role, require_db=args.require_db)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        suite = build_suite(args.profile)
        if args.include:
            try:
                suite = include_tests(suite, args.include)
            except ValueError as exc:
                parser.error(str(exc))
    total = suite.countTestCases()
    suite = shard_suite(suite, shard_index - 1, shard_count)
    selected = suite.countTestCases()
    if not selected:
        parser.error("No tests selected")
    full_suite = args.profile == "full" and shard_count == 1 and not args.role
    selected_ids = [test.id() for test in test_cases(suite)]
    company_ids = {name for group in company for name in group["test_ids"]}
    scope = {"role": args.role, "database_tests": args.require_db, "include": args.include,
             "company_test_directories": company,
             "company_tests_selected": sum(name in company_ids for name in selected_ids)}
    print(f"profile={args.profile} tests_selected={selected} full_suite={str(full_suite).lower()} shard={args.shard} role={args.role or 'all'} database_tests={str(args.require_db).lower()}", flush=True)
    if args.include:
        print("additional_tests=" + ",".join(args.include), flush=True)
    if args.role:
        print(f"company_tests_selected={scope['company_tests_selected']} (zero means company adapters are not verified)", flush=True)
    if args.manifest_json:
        with args.manifest_json.open("x", encoding="utf-8") as stream:
            json.dump({"profile": args.profile, "test_ids": selected_ids, "tests_selected": selected,
                       "tests_total": total, "shard": args.shard, "full_suite": full_suite, **scope}, stream, indent=2)
            stream.write("\n")
    if args.list or args.manifest_json:
        for test in test_cases(suite):
            print(test.id())
        return 0
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    successful = result.wasSuccessful() and (not args.role or not result.skipped)
    print(f"profile={args.profile} tests_run={result.testsRun}", flush=True)
    if args.role:
        print(f"role_tests_status={'PASSED' if successful else 'FAILED'} role={args.role}", flush=True)
    if args.report_json:
        report = {"profile": args.profile, "full_suite": full_suite, "tests_selected": selected,
                  "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                  "skipped": len(result.skipped), "successful": successful, **scope,
                  "tests_total": total, "test_ids": selected_ids, "shard": args.shard,
                  "failed_tests": [test.id() for test, _ in result.failures + result.errors]}
        with args.report_json.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
