#!/usr/bin/env python3
"""Exercise all three stages locally; never approve findings or contact Jira/AI."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from sanity_common import ReviewError, atomic_json

ROOT = Path(__file__).resolve().parent


def stage(name, *args):
    result = subprocess.run([sys.executable, str(ROOT / name / "main.py"), *map(str, args)],
                            text=True, capture_output=True)
    if result.returncode:
        raise ReviewError("DEMO_STAGE_FAILED", f"{name}: {result.stderr.strip()}")
    return result.stdout


def demo(out, scenario="complete"):
    out = Path(out).absolute()
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise ReviewError("OUTPUT_EXISTS", "Use an absent or empty output directory")
    out.mkdir(parents=True, exist_ok=True)
    stage("vwp-dev", "demo", "--out", out / "vwp-dev", "--scenario", scenario)
    sent = json.loads(stage("vwp-bridge", "analyze", "--input", out / "vwp-dev/run.json", "--out", out / "vwp-bridge"))
    received = json.loads(stage("oa-bridge", "receive", "--message-id", sent["message_id"]))
    if received.get("status") not in ("RECEIVED", "ALREADY_RECEIVED"):
        raise ReviewError("DEMO_RECEIVE", "Shared DB message was not received; check endpoint, schema and lease")
    bundle_id = received["bundle_id"]
    stage("oa-bridge", "report", "--bundle-id", bundle_id,
          "--out", out / "report.md")
    receipt = {"scenario": scenario, "bundle_id": bundle_id, "coverage": received["coverage"],
               "interface": "OA_DASHBOARD", "jira_enabled": False,
               "report": str(out / "report.md"), "analysis_id": sent["analysis_id"], "transport": "POSTGRESQL"}
    atomic_json(out / "receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="demo-output")
    parser.add_argument("--scenario", choices=("complete", "missing", "malformed", "unsupported"),
                        default="complete")
    args = parser.parse_args()
    try:
        receipt = demo(args.out, args.scenario)
        c = receipt["coverage"]
        print(f"expected={c['expected']} reviewed={c['reviewed']} approved_na={c['approved_na']} "
              f"unresolved={c['unresolved']} missing={c['missing']} "
              f"review={c['review_status']} check={c['check_status']}")
        print(f"transport=POSTGRESQL interface=OA_DASHBOARD ai=NOT_CONFIGURED report={receipt['report']}")
        return 0
    except ReviewError as exc:
        print(f"code={exc.code} {exc}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"code=DEMO_ERROR {type(exc).__name__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
