"""Public Host preservation with the real OA request guards and optional nginx."""
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
import uvicorn

from oa.dashboard_api import create_app
from oa.docker import start
from oa.sanity_access import require_mutation_request


PUBLIC_HOST = "oa.example.test"
FORWARDED_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip")


def guarded_app():
    router = APIRouter()

    @router.post("/guard-check")
    def guard_check(request: Request):
        # Exercise the real guard without SDK, accounts, database or mutations.
        require_mutation_request(request)
        return {"host": request.headers["host"], "origin": request.headers.get("origin"),
                "forwarded": {name: request.headers.get(name) for name in FORWARDED_HEADERS}}

    return create_app(auth_mode="sso", auth_router=router, allowed_hosts=PUBLIC_HOST)


class OriginCases:
    def test_public_host_and_origin_including_nondefault_port_are_preserved(self):
        for host, origin in ((PUBLIC_HOST, "https://" + PUBLIC_HOST),
                             (PUBLIC_HOST + ":18443", "https://" + PUBLIC_HOST + ":18443")):
            with self.subTest(host=host):
                status, body = self.send("POST", "/auth/guard-check", {
                    "Host": host, "Origin": origin, "X-Sanity-Request": "1", "Sec-Fetch-Site": "same-origin"})
                self.assertEqual(200, status, body)
                self.assertEqual(host, body["host"])
                self.assertEqual(origin, body["origin"])

    def test_cross_origin_fetch_site_and_missing_client_header_are_rejected(self):
        cases = (
            {"Origin": "https://other.example.test", "X-Sanity-Request": "1"},
            {"Origin": "https://" + PUBLIC_HOST},
            {"Origin": "https://" + PUBLIC_HOST, "X-Sanity-Request": "1", "Sec-Fetch-Site": "cross-site"},
            {"X-Sanity-Request": "1", "Sec-Fetch-Site": "same-site"},
            {"Origin": "https://" + PUBLIC_HOST + ":18443", "X-Sanity-Request": "1"},
        )
        for headers in cases:
            with self.subTest(headers=headers):
                status, body = self.send("POST", "/auth/guard-check", {"Host": PUBLIC_HOST, **headers})
                self.assertEqual((403, {"code": "FORBIDDEN"}), (status, body))

    def test_null_malformed_and_credentialed_origins_are_rejected(self):
        for origin in ("null", "https://[", "https://user@" + PUBLIC_HOST, "https://" + PUBLIC_HOST + "/"):
            with self.subTest(origin=origin):
                status, body = self.send("POST", "/auth/guard-check", {
                    "Host": PUBLIC_HOST, "Origin": origin, "X-Sanity-Request": "1"})
                self.assertEqual((403, {"code": "FORBIDDEN"}), (status, body))

    def test_originless_client_remains_allowed_and_unknown_host_is_rejected(self):
        status, body = self.send("POST", "/auth/guard-check", {"Host": PUBLIC_HOST, "X-Sanity-Request": "1"})
        self.assertEqual(200, status, body)
        self.assertIsNone(body["origin"])
        status, _ = self.send("POST", "/auth/guard-check", {
            "Host": "other.example.test", "Origin": "https://other.example.test", "X-Sanity-Request": "1"})
        self.assertEqual(400, status)
        status, body = self.send("GET", "/api/health", {"Host": "127.0.0.1"})
        self.assertEqual(200, status, body)
        self.assertEqual("OK", body["status"])


class ApplicationOriginTests(OriginCases, unittest.TestCase):
    def setUp(self):
        self.client = TestClient(guarded_app())
        self.addCleanup(self.client.close)

    def send(self, method, path, headers):
        response = self.client.request(method, path, headers=headers, content=b"{}" if method == "POST" else None)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body


class NginxOriginTests(OriginCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = os.environ.get("SANITY_TEST_NGINX_BINARY") or shutil.which("nginx")
        if not binary:
            raise unittest.SkipTest("nginx unavailable; set SANITY_TEST_NGINX_BINARY for real proxy checks")
        cls.temp = tempfile.TemporaryDirectory(prefix="sanity-proxy-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        runtime = Path(cls.temp.name)
        backend = socket.socket()
        backend.bind(("127.0.0.1", 0))
        backend.listen(64)
        cls.addClassCleanup(backend.close)
        server = uvicorn.Server(uvicorn.Config(guarded_app(), access_log=False, log_level="critical", proxy_headers=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [backend]}, daemon=True)
        thread.start()

        def stop_backend():
            server.should_exit = True
            thread.join(timeout=5)
        cls.addClassCleanup(stop_backend)
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            cls.port = available.getsockname()[1]
        start.render_config(PUBLIC_HOST, runtime=runtime)
        # Only listening/upstream ports change; every proxy directive is production code.
        config = (runtime / "server.conf").read_text()
        config = config.replace("listen [::]:8080 default_server;", "").replace("listen [::]:8080;", "")
        config = config.replace("listen 8080", f"listen 127.0.0.1:{cls.port}")
        config = config.replace("http://127.0.0.1:8000", "http://127.0.0.1:" + str(backend.getsockname()[1]))
        (runtime / "server.conf").write_text(config)
        main = runtime / "nginx.conf"
        main.write_text(f"worker_processes 1;\npid {runtime}/nginx.pid;\nerror_log {runtime}/error.log crit;\n"
                        f"events {{ worker_connections 64; }}\nhttp {{ access_log off; "
                        f"client_body_temp_path {runtime}/client-body; proxy_temp_path {runtime}/proxy; "
                        f"fastcgi_temp_path {runtime}/fastcgi; uwsgi_temp_path {runtime}/uwsgi; "
                        f"scgi_temp_path {runtime}/scgi; "
                        f"include {runtime}/server.conf; }}\n")
        nginx = subprocess.Popen([binary, "-p", str(runtime), "-c", str(main), "-g", "daemon off;"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        def stop_nginx():
            if nginx.poll() is None:
                nginx.terminate()
                nginx.wait(timeout=5)
            nginx.stderr.close()
        cls.addClassCleanup(stop_nginx)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if nginx.poll() is not None:
                raise AssertionError("Test nginx failed to start: " + nginx.stderr.read().decode())
            try:
                if cls.send("GET", "/api/health", {"Host": PUBLIC_HOST})[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.02)
        else:
            raise AssertionError("Local proxy did not become ready")

    @classmethod
    def send(cls, method, path, headers):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=2)
        try:
            connection.request(method, path, body=b"{}" if method == "POST" else None, headers=headers)
            response = connection.getresponse()
            raw = response.read().decode()
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
            return response.status, body
        finally:
            connection.close()

    def test_forwarded_headers_do_not_reach_backend(self):
        status, body = self.send("POST", "/auth/guard-check", {
            "Host": PUBLIC_HOST, "Origin": "https://" + PUBLIC_HOST, "X-Sanity-Request": "1",
            **{name: "synthetic-untrusted" for name in FORWARDED_HEADERS}})
        self.assertEqual(200, status, body)
        self.assertEqual({name: None for name in FORWARDED_HEADERS}, body["forwarded"])


if __name__ == "__main__":
    unittest.main()
