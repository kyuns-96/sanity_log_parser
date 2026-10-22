"""Existing OA deployment's background model worker; no additional HTTP server."""
from __future__ import annotations

import argparse
import sys
import time

from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_db import Database
from oa.sanity_access import AccessStore, administrator_id
from oa.sanity_ai_jobs import run_once
from oa.sanity_ai_model import OAAdvisor, OASettings
from oa.sanity_production_workspace import ProductionWorkspaceStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run persisted OA AI jobs with complete bounded inputs")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit")
    parser.add_argument("--interval", type=int, default=2, help="Idle polling seconds (1..300, default 2)")
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 300:
        parser.error("--interval must be between 1 and 300")
    try:
        settings = load_environment()
        admin = administrator_id(settings)
        if admin is None:
            raise ReviewError("AUTHORIZATION_NOT_IMPLEMENTED", "Production authorization is required")
        advisor = OAAdvisor(OASettings.from_env(settings))

        def workspace(db):
            return ProductionWorkspaceStore(db, AccessStore(db, admin))

        while True:
            result = run_once(lambda: Database("oa"), workspace, advisor)
            if result:
                # IDs, input text, credentials and driver/model exceptions are
                # not printed. Full provenance remains in the authorized DB.
                print("status=" + result["status"] + (" code=" + result["error"] if result.get("error") else ""), flush=True)
            if args.once:
                return 0
            if result is None or result["status"] == "RUNNING":
                time.sleep(args.interval)
    except ReviewError as error:
        print("code=" + error.code, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        print("code=AI_WORKER_FAILED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
