"""Logical OA jobs, physically deployed on VWP; no additional HTTP server."""
from __future__ import annotations

import argparse
import sys
import time
import threading
from uuid import uuid4

from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_db import Database
from oa.sanity_access import AccessStore, administrator_id
from oa.sanity_ai_jobs import run_once
from oa.sanity_ai_model import OAAdvisor, OASettings
from oa.sanity_production_workspace import ProductionWorkspaceStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run persisted OA AI jobs with complete bounded inputs")
    parser.add_argument("--once", action="store_true", help="Process at most one preparation/call/batch step and exit")
    parser.add_argument("--interval", type=int, default=2, help="Idle polling seconds (1..300, default 2)")
    parser.add_argument("--site", choices=("oa", "vwp"), default="oa", help="Actual worker placement; production relocation uses vwp")
    args = parser.parse_args(argv)
    if not 1 <= args.interval <= 300:
        parser.error("--interval must be between 1 and 300")
    try:
        settings = load_environment()
        admin = administrator_id(settings)
        if admin is None:
            raise ReviewError("AUTHORIZATION_NOT_IMPLEMENTED", "Production authorization is required")
        legacy = OASettings.from_env(settings)
        advisor = OAAdvisor(legacy) if legacy.enabled else None

        def workspace(db):
            return ProductionWorkspaceStore(db, AccessStore(db, admin))

        from common.sanity_ai_execution import executing_on
        from oa.sanity_ai_monitor import heartbeat
        from oa.sanity_ai_scheduler import Scheduler,executing_job,work_loop
        factory=lambda:Database('oa')
        scheduler=Scheduler(factory,str(uuid4()),args.site)
        stop=threading.Event()
        def health_loop():
            with executing_on(args.site):
                while not stop.wait(30):
                    try:
                        heartbeat();scheduler.renew()
                    except Exception:pass
        published = None
        with executing_on(args.site):
            heartbeat()
            if not args.once:threading.Thread(target=health_loop,daemon=True,name="ai-worker-heartbeat").start()
            def tick():
                nonlocal published
                if args.site == "vwp" and (published is None or time.monotonic() - published >= 300):
                    from oa.sanity_vwp_inference import publish_catalog
                    publish_catalog()
                    published = time.monotonic()
            def process(identity):
                with executing_on(args.site),executing_job(identity,scheduler.owner):
                    return run_once(factory,workspace,advisor,job_id=identity)
            def report(result):
                if result:
                    # IDs, input text, credentials and driver/model exceptions are
                    # not printed. Full provenance remains in the authorized DB.
                    print("status=" + result["status"] + (" code=" + result["error"] if result.get("error") else ""), flush=True)
            work_loop(scheduler,process,stop,args.interval,once=args.once,on_tick=tick,on_result=report,
                      on_error=lambda code:print('code='+code,file=sys.stderr,flush=True))
            return 0
    except ReviewError as error:
        print("code=" + error.code, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        print("code=AI_WORKER_FAILED", file=sys.stderr)
        return 2
    finally:
        if "stop" in locals():stop.set()


if __name__ == "__main__":
    raise SystemExit(main())
