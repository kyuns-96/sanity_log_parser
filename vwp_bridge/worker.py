"""VWP-side worker; never imports OA application code."""
from common.sanity_workers import poll
from vwp_bridge.sanity_cluster_worker import process_cluster
from vwp_bridge.sanity_exchange import respond_evidence


def step():
    result = process_cluster()
    response = respond_evidence()
    return response if result["status"] == "NO_MESSAGE" else result


def work(once=False, interval=2):
    return poll(step, once, interval)
