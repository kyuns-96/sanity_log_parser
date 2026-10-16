"""Shared polling mechanics; role handlers own their database operations."""
import json
import threading
from common.sanity_common import ReviewError


def poll(handler, once=False, interval=2):
    if not 1 <= interval <= 60:
        raise ReviewError("INVALID_REQUEST", "Polling interval must be between 1 and 60 seconds")
    try:
        while True:
            try:
                result = handler()
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
