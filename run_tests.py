#!/usr/bin/env python3
"""Run pure unit tests and configured PostgreSQL integration tests (never a local DB fallback)."""
import argparse
import os
from pathlib import Path
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-db", action="store_true", help="fail rather than skip PostgreSQL integration tests")
    args = parser.parse_args()
    if args.require_db and not all(os.environ.get(key) for key in ("SANITY_TEST_ADMIN_DSN", "SANITY_VWP_DSN", "SANITY_OA_DSN")):
        parser.error("--require-db needs SANITY_TEST_ADMIN_DSN, SANITY_VWP_DSN and SANITY_OA_DSN")
    if args.require_db:
        try:
            import psycopg
        except ImportError:
            parser.error("--require-db needs psycopg; install requirements.txt")
    root = Path(__file__).resolve().parent
    suite = unittest.TestSuite()
    for directory in ("tests", "vwp-dev", "vwp-bridge", "oa-bridge"):
        suite.addTests(unittest.TestLoader().discover(str(root / directory), pattern="test_*.py"))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
