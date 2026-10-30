"""Portal reuse must preserve ASGI isolation, failures, concurrency and lifespan."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import threading
import unittest

from fastapi import FastAPI, Request
from tests.http_support import TestClient


class HttpFixtureTests(unittest.TestCase):
    def test_reuses_request_loop_but_keeps_clients_and_cookies_separate(self):
        app = FastAPI()

        @app.get("/")
        async def view(request: Request):
            return {"loop": id(asyncio.get_running_loop()), "cookie": request.cookies.get("synthetic")}

        first, second = TestClient(app), TestClient(app)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.cookies.set("synthetic", "first-client")
        a, b, c = first.get("/").json(), first.get("/").json(), second.get("/").json()
        self.assertEqual(a["loop"], b["loop"])
        self.assertNotEqual(a["loop"], c["loop"])
        self.assertEqual("first-client", b["cookie"])
        self.assertIsNone(c["cookie"])

    def test_concurrent_first_requests_share_a_portal_without_serializing_handlers(self):
        app = FastAPI()
        ready = threading.Barrier(2, timeout=5)

        @app.get("/")
        def view():
            ready.wait()
            return {"ok": True}

        client = TestClient(app)
        self.addCleanup(client.close)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.get("/"), range(2)))
        self.assertEqual([200, 200], [response.status_code for response in responses])

    def test_close_joins_the_owned_loop_and_remains_idempotent_after_a_handler_error(self):
        app = FastAPI()

        @app.get("/")
        async def view():
            raise RuntimeError("synthetic failure")

        client = TestClient(app)
        self.addCleanup(client.close)
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            client.get("/")
        portal = client._test_portal
        client.close()
        client.close()
        with self.assertRaises(RuntimeError):
            portal.call(lambda: None)

    def test_explicit_context_runs_lifespan_once_and_ordinary_requests_do_not_add_one(self):
        events = []

        @asynccontextmanager
        async def lifespan(app):
            events.append("start")
            yield
            events.append("stop")

        app = FastAPI(lifespan=lifespan)

        @app.get("/")
        async def view(): return {"ok": True}

        plain = TestClient(app)
        self.addCleanup(plain.close)
        self.assertEqual(200, plain.get("/").status_code)
        self.assertEqual([], events)
        with TestClient(app) as explicit:
            self.assertEqual(200, explicit.get("/").status_code)
            self.assertEqual(200, explicit.get("/").status_code)
        self.assertEqual(["start", "stop"], events)
