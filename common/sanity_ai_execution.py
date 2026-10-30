"""Explicit process placement, independent of DB identity or model provider."""
from contextlib import contextmanager
from contextvars import ContextVar

from common.sanity_common import ReviewError

_site = ContextVar("sanity_ai_worker_site", default="oa")


def worker_site():
    return _site.get()


@contextmanager
def executing_on(site):
    if site not in ("oa", "vwp"):
        raise ReviewError("AI_WORKER_SITE", "Choose the actual AI worker placement")
    token = _site.set(site)
    try:
        yield
    finally:
        _site.reset(token)
