"""OA receiver worker; never imports a VWP model client."""
from common.sanity_workers import poll
from oa.sanity_cluster_store import receive_cluster


def work(once=False, interval=2):
    return poll(receive_cluster, once, interval)
