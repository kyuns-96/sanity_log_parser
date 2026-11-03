"""Explicit intranet HTTP model URLs; synthetic loopback and mock TLS tests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
import unittest
from unittest.mock import patch

import httpx

from common.sanity_common import canonical_json
from common.sanity_integration_examples import clustering_example
from common.sanity_vwp_ai_http import invoke as vwp_invoke
from common.sanity_vwp_ai_profiles import ProfileStore
from oa.sanity_ai_model import OAAdvisor
from tests.test_ai_model import chat, environment
from tests.test_vwp_inference import PERSONAL_TOKEN, profile, body

UNUSED_CA = "/nonexistent/synthetic-unused-model-ca.pem"


def invoke(raw, private, **options):
    # The worker injects an owner's key only into the transient HTTP profile.
    return vwp_invoke(raw, {**private, "token":PERSONAL_TOKEN}, **options)


class ModelHttpTests(unittest.TestCase):
    def setUp(self):
        self.packet = clustering_example()
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((self.path, dict(self.headers), raw))
                status = 503 if self.path == "/error" else 307 if self.path == "/redirect" else 200
                self.send_response(status)
                if status == 307:
                    self.send_header("Location", outer.url + "/canary")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                reply = {"schema_version":1, "rules":[], "explanation":"합성 응답", "needs_clarification":True} \
                    if self.path == "/legacy" else outer.packet["reply"]
                envelope = chat(reply)
                envelope["model"] = json.loads(raw)["model"]
                self.wfile.write(canonical_json(envelope))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval":0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def oa(self, endpoint, fallback="", **extra):
        return OAAdvisor(environment(SANITY_OA_AI_URL=endpoint, SANITY_OA_AI_FALLBACK_URL=fallback, **extra))

    def cluster(self, advisor):
        return advisor.cluster(self.packet["context"], self.packet["cases"], self.packet["identity_fields"])


    def test_explicit_mixed_scheme_backups_keep_tls_verification_and_identical_requests(self):
        actual_client = httpx.Client
        for first, second in (("http","http"), ("http","https"), ("https","http"), ("https","https")):
            primary, fallback = first+"://primary.example.test/api", second+"://backup.example.test/api"
            for kind in ("oa", "vwp"):
                with self.subTest(primary=first, fallback=second, client=kind):
                    seen = []
                    def respond(request):
                        seen.append(request)
                        if len(seen) == 1:
                            raise httpx.ConnectError("synthetic pre-send failure", request=request)
                        value = chat(self.packet["reply"])
                        value["model"] = json.loads(request.content)["model"]
                        return httpx.Response(200, json=value)
                    def client(**options):
                        if "https" in (first,second):
                            self.assertIsInstance(options["verify"], ssl.SSLContext)
                            self.assertTrue(options["verify"].check_hostname)
                            self.assertEqual(ssl.CERT_REQUIRED, options["verify"].verify_mode)
                        else:
                            self.assertIs(options["verify"], True)
                        self.assertFalse(options["trust_env"])
                        self.assertFalse(options["follow_redirects"])
                        return actual_client(**options)
                    with patch("httpx.Client", side_effect=client):
                        if kind == "oa":
                            advisor = OAAdvisor(environment(SANITY_OA_AI_URL=primary, SANITY_OA_AI_FALLBACK_URL=fallback),
                                                transport=httpx.MockTransport(respond))
                            self.cluster(advisor)
                        else:
                            private = profile(endpoint=primary, fallback_endpoint=fallback)
                            ProfileStore([private])
                            invoke(canonical_json(body()), private, timeout=5, max_output_bytes=4096,
                                   transport=httpx.MockTransport(respond))
                    self.assertEqual([primary,fallback], [str(r.url) for r in seen])
                    self.assertEqual(seen[0].content, seen[1].content)
                    self.assertEqual(seen[0].headers["authorization"], seen[1].headers["authorization"])
