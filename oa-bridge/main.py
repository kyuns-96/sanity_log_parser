#!/usr/bin/env python3
"""OA PostgreSQL receiver and review controls; Jira is disabled."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sanity_common import (ReviewError, atomic_text, coverage, unpack_bundle)
from sanity_db import Database, Queue
from sanity_oa_store import OAStore

def receive(args):
    if getattr(args, "jira_context", None) is not None:
        raise ReviewError("JIRA_DISABLED", "Jira is disabled; use the OA dashboard")
    message_id = getattr(args, "message_id", None)
    with Database("oa") as db:
        queue = Queue(db, "vwp_to_oa")
        if message_id:
            previous = queue.get(message_id)
            if previous and previous["status"] == "RECEIVED" and previous["message_type"] == "ANALYSIS_BUNDLE":
                incoming = unpack_bundle(previous)
                with db.transaction(readonly=True):
                    store = OAStore(db)
                    payload, decisions, context, revision = store.load(incoming["bundle_id"])
                    if payload != incoming:
                        raise ReviewError("BUNDLE_CONFLICT", "Received content differs from shared message")
                    store.report(payload["bundle_id"])
                    return {"status": "ALREADY_RECEIVED", "bundle_id": payload["bundle_id"],
                            "coverage": coverage(payload, decisions), "context": context, "review_revision": revision}
        message = queue.claim(("ANALYSIS_BUNDLE", "PING"), message_id, getattr(args, "lease_seconds", 300))
        if message is None:
            return {"status": "NO_MESSAGE"}
        try:
            with_ping = message["message_type"] == "PING"
            if not with_ping:
                payload = unpack_bundle(message)
                context = {"interface": "dashboard-v1", "ai": {"status": "NOT_CONFIGURED"}}
            with db.transaction():
                result = ({"status": "PING_RECEIVED", "message_id": message["message_id"]} if with_ping else
                          OAStore(db).receive(payload, context))
                queue.finish(message)
            return result
        except ReviewError as exc:
            if exc.code != "LEASE_LOST":
                with db.transaction():
                    queue.finish(message, "REJECTED", exc.code)
            raise


def status(args):
    with Database("oa") as db, db.transaction(readonly=True):
        store = OAStore(db)
        payload, decisions, context, revision = store.load(args.bundle_id)
        store.report(args.bundle_id)
        return {"bundle_id": args.bundle_id, "coverage": coverage(payload, decisions), "decisions": decisions,
                "context": {"ai": context.get("ai", {"status": "NOT_CONFIGURED"})},
                "review_revision": revision, "interface": "OA_DASHBOARD", "jira_enabled": False}



def report(args):
    with Database("oa") as db, db.transaction(readonly=True):
        body = OAStore(db).report(args.bundle_id)
    if args.out:
        atomic_text(args.out, body)
        return {"status": "REPORT_WRITTEN", "out": str(args.out)}
    return body


def review(args):
    with Database("oa") as db, db.transaction():
        return OAStore(db).review(args.bundle_id, args.unit_id, args.reviewer, args.conclusion,
                                 args.reason, args.request_id)


def publish(args):
    raise ReviewError("JIRA_DISABLED", "Jira publishing is disabled; use the OA dashboard")


def request_evidence(args):
    from sanity_exchange import request_evidence as request
    return request(args.bundle_id, args.unit_id, args.record_id, args.reason, args.request_id, args.ttl_seconds)


def receive_evidence(args):
    from sanity_exchange import receive_evidence as receive_response
    return receive_response(args.request_id, args.out)


def work(args):
    from sanity_workers import work as run_worker
    return run_worker("oa", args.once, args.interval)


def brief_result(result):
    """Bound the hand-relayed response independently of report or context size."""
    if not isinstance(result, dict):
        return "status=OK"
    # Values originate from validated IDs or fixed enum outputs; shorten their
    # representation too so this remains bounded if an adapter expands them.
    def token(value):
        return "_".join(str(value).split())[:100]
    fields = [(key, result[key]) for key in ("status", "bundle_id", "code") if key in result]
    if not any(key == "status" for key, _ in fields):
        fields.insert(0, ("status", "OK"))
    summary = result.get("coverage", {})
    names = (("expected", "expected"), ("reviewed", "reviewed"),
             ("unresolved", "unresolved"), ("missing", "missing"),
             ("review_status", "review"), ("check_status", "check"))
    fields.extend((label, summary[key]) for key, label in names if key in summary)
    return " ".join(f"{key}={token(value)}" for key, value in fields)



def main(argv=None):
    parser = argparse.ArgumentParser(description="OA PostgreSQL bridge and review controls; Jira disabled")
    subs = parser.add_subparsers(dest="command", required=True)
    worker = subs.add_parser("work", help="poll classification result parts through PostgreSQL")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval", type=int, default=2)
    worker.set_defaults(handler=work)
    for name in ("receive", "status", "report", "publish", "review", "request-evidence", "receive-evidence"):
        sub = subs.add_parser(name)
        if name != "report":
            sub.add_argument("--brief", action="store_true")
        if name not in ("receive", "receive-evidence"):
            sub.add_argument("--bundle-id", required=True)
        if name == "receive":
            sub.add_argument("--message-id", "--bundle-id", dest="message_id")
            sub.add_argument("--lease-seconds", type=int, default=300)
        elif name == "report":
            sub.add_argument("--out", type=Path)
        elif name == "publish":
            sub.add_argument("--dry-run", action="store_true")
            sub.add_argument("--simulate", action="store_true", help="retired option; publish always returns JIRA_DISABLED")
        elif name == "review":
            sub.add_argument("--unit-id", required=True)
            sub.add_argument("--reviewer", required=True)
            sub.add_argument("--conclusion", required=True, choices=("PASS", "FAIL"))
            sub.add_argument("--reason", required=True)
            sub.add_argument("--request-id", required=True, help="UUID reused for an exact command retry")
        elif name == "request-evidence":
            sub.add_argument("--request-id", required=True)
            sub.add_argument("--unit-id", required=True)
            sub.add_argument("--record-id", required=True, action="append")
            sub.add_argument("--reason", required=True)
            sub.add_argument("--ttl-seconds", type=int, default=3600)
        elif name == "receive-evidence":
            sub.add_argument("--request-id", required=True)
            sub.add_argument("--out", type=Path, help="optional OA-local evidence JSON output")
        sub.set_defaults(handler=globals()[name.replace("-", "_")])
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
        print(brief_result(result) if getattr(args, "brief", False) else
              result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ReviewError as exc:
        print(f"code={exc.code}" if getattr(args, "brief", False) else f"code={exc.code} {exc}", file=sys.stderr)
    except OSError as exc:
        print(f"code=STATE_IO_ERROR {type(exc).__name__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
