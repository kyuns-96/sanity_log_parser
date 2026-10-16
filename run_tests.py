#!/usr/bin/env python3
"""Run pure unit tests and configured PostgreSQL integration tests (never a local DB fallback)."""
import argparse
import json
import os
from pathlib import Path
import time
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-db", action="store_true", help="fail rather than skip PostgreSQL integration tests")
    parser.add_argument("--report-json", type=Path, help="write a new machine-readable result file (will not overwrite)")
    args = parser.parse_args()
    if args.require_db and not all(os.environ.get(key) for key in ("SANITY_TEST_ADMIN_DSN", "SANITY_VWP_DSN", "SANITY_OA_DSN")):
        parser.error("--require-db needs SANITY_TEST_ADMIN_DSN, SANITY_VWP_DSN and SANITY_OA_DSN")
    if args.require_db:
        try:
            import psycopg2
        except ImportError:
            parser.error("--require-db needs psycopg2; install requirements.txt")
    root = Path(__file__).resolve().parent
    suite = unittest.TestSuite()
    for directory in ("tests", "vwp", "vwp_bridge", "oa"):
        suite.addTests(unittest.TestLoader().discover(str(root / directory), pattern="test_*.py", top_level_dir=str(root)))
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.report_json:
        report = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                  "skipped": len(result.skipped), "successful": result.wasSuccessful(),
                  "seconds": round(time.monotonic() - started, 3),
                  "failed_tests": [test.id() for test, _ in result.failures + result.errors]}
        with args.report_json.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
