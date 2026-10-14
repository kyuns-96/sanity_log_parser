"""Explicit process entrypoints; no worker is started by opening a browser."""
import json
import threading

from sanity_common import ReviewError


def work(side, once=False, interval=2):
    if not 1 <= interval <= 60:
        raise ReviewError("INVALID_REQUEST", "Polling interval must be between 1 and 60 seconds")
    from sanity_cluster_worker import process_cluster
    from sanity_cluster_store import receive_cluster
    from sanity_exchange import respond_evidence
    try:
        while True:
            try:
                if side == "vwp":
                    result = process_cluster()
                    evidence = respond_evidence()
                    if result["status"] == "NO_MESSAGE":
                        result = evidence
                else:
                    result = receive_cluster()
                if once:
                    return result
                if result["status"] != "NO_MESSAGE":
                    print(json.dumps(result), flush=True)
                else:
                    threading.Event().wait(interval)
            except ReviewError as exc:
                if once:
                    raise
                print(json.dumps({"status": "ERROR", "code": exc.code}), flush=True)
                threading.Event().wait(interval)
    except KeyboardInterrupt:
        return {"status": "STOPPED"}
