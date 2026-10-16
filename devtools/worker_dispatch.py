"""Test-only convenience for exercising both server workers in one fixture."""


def work(side, once=False, interval=2):
    if side == "vwp":
        from vwp_bridge.worker import work as handler
    else:
        from oa.worker import work as handler
    return handler(once, interval)
