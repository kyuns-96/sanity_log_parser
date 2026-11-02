"""Reuse one ASGI portal per test client, retaining the normal request contract.

Clients still own their cookies, app state and exception policy. Explicit `with`
contexts retain Starlette's lifespan behavior; ordinary clients do not add one.
Call close through the test's existing cleanup to join the owned portal thread.
"""
from contextlib import ExitStack, contextmanager
import threading

from anyio.from_thread import start_blocking_portal
from fastapi.testclient import TestClient as BaseTestClient


class TestClient(BaseTestClient):
    __test__ = False

    def __init__(self, *args, **kwargs):
        self._test_portal_stack = ExitStack()
        self._test_portal_lock = threading.Lock()
        self._test_portal = None
        super().__init__(*args, **kwargs)

    @contextmanager
    def _portal_factory(self):
        if self.portal is not None:
            yield self.portal
            return
        with self._test_portal_lock:
            if self._test_portal is None:
                self._test_portal = self._test_portal_stack.enter_context(
                    start_blocking_portal(**self.async_backend))
            portal = self._test_portal
        yield portal

    def close(self):
        try:
            super().close()
        finally:
            self._test_portal_stack.close()
