"""Synthetic scale probe; uses only a dedicated sanity_test_* DB and a disposable schema."""
import argparse
import importlib.util
import json
from pathlib import Path
import resource
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.sanity_common import atomic_json, atomic_text, digest, sha256_file
from tests.support import PgTestCase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--groups", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.groups <= args.rows <= 100000:
        parser.error("Require 1 <= groups <= rows <= 100000")
    if getattr(PgTestCase, "__unittest_skip__", False):
        parser.error("Configure the PostgreSQL driver and all three test DSNs first")
    spec = importlib.util.spec_from_file_location("vwp_scale", ROOT / "vwp_bridge/main.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    fixture = PgTestCase()
    try:
        fixture.setUp()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "reports/numeric.csv"
            body = ",".join(bridge.HEADER) + "\n" + "\n".join(
                f"A{i % args.groups},P1,M1,C-1,WARNING,12,ns" for i in range(args.rows)) + f"\n# END rows={args.rows}\n"
            atomic_text(report, body)
            del body
            unit = {"review_unit_id": "u-1", "item_id": "numeric", "report": "reports/numeric.csv",
                    "parser": "numeric_csv_v1", "rule_version": "1", "condition": {"mode": "M1"},
                    "identity_fields": ["object", "check_point", "mode"],
                    "evaluation": {"operator": "le", "limit": 10, "unit": "ns"},
                    "execution_status": "COMPLETED", "applicable": True, "exclusion": None}
            inventory = {"confirmed": True, "source": "synthetic-scale", "version": "1", "units": [unit]}
            inventory["sha256"] = digest(inventory)
            manifest = {"schema_version": 1, "ready": True,
                        "run": {"run_id": "synthetic-scale", "project": "test", "design": "synthetic",
                                "stage": "sanity", "tool": "synthetic", "tool_version": "1"},
                        "inventory": inventory, "unexpected_files": [],
                        "files": [{"file_id": "f-1", "path": unit["report"], "size_bytes": report.stat().st_size,
                                   "sha256": sha256_file(report)}]}
            atomic_json(root / "run.json", manifest)
            started = time.monotonic()
            payload, receipt = bridge.analyze(root / "run.json")
            elapsed = time.monotonic() - started
            stored = fixture.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='EVIDENCE'").fetchone()[0]
            if stored != args.rows or len(payload["finding_groups"]) != args.groups:
                raise RuntimeError("Scale probe coverage mismatch")
            packed = fixture.admin_query("SELECT octet_length(payload_gzip) FROM {vwp_to_oa}").fetchone()[0]
            print(json.dumps({"rows": stored, "groups": args.groups, "seconds": round(elapsed, 3),
                "client_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "compressed_bytes": packed, "transport": "POSTGRESQL"}, sort_keys=True))
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    main()
