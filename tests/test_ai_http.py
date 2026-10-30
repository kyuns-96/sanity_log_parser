"""Explicit intranet HTTP model URLs; synthetic loopback and mock TLS tests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import ssl
import threading
import unittest
from unittest.mock import patch

import httpx

from common.sanity_common import ReviewError, canonical_json
from common.sanity_integration_examples import clustering_example
from common.sanity_vwp_ai_http import invoke
from common.sanity_vwp_ai_profiles import ProfileStore
from oa.sanity_ai_model import OAAdvisor
from tests.test_ai_model import chat, environment
from tests.test_vwp_inference import profile, body
from vwp_bridge.sanity_ai import AIAdapter

UNUSED_CA = "/nonexistent/synthetic-unused-model-ca.pem"


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

    def test_http_reaches_real_loopback_with_credentials_for_each_model_client(self):
        result = self.cluster(self.oa(self.url+"/oa", SANITY_OA_AI_CA_PATH=UNUSED_CA))
        self.assertTrue(result["metadata"]["model_called"])
        private = profile(endpoint=self.url+"/vwp", ca_path=UNUSED_CA)
        ProfileStore([private])
        raw, _ = invoke(canonical_json(body()), private, timeout=5, max_output_bytes=4096)
        self.assertEqual("synthetic-model", json.loads(raw)["model"])
        legacy = AIAdapter({"SANITY_AI_MODE":"api", "SANITY_AI_URL":self.url+"/legacy",
            "SANITY_AI_TOKEN":"synthetic-legacy-token", "SANITY_AI_MODEL":"synthetic-model", "SANITY_AI_CA_PATH":UNUSED_CA})
        self.assertTrue(legacy.propose({"schema_version":1})["needs_clarification"])
        self.assertEqual(["/oa","/vwp","/legacy"], [r[0] for r in self.requests])
        self.assertEqual(["Bearer "+environment()["SANITY_OA_AI_TOKEN"], "Bearer "+private["token"], "Bearer synthetic-legacy-token"],
                         [r[1]["Authorization"] for r in self.requests])

    def test_http_backup_receives_one_post_after_refused_primary_connection(self):
        with socket.socket() as blocked:
            blocked.bind(("127.0.0.1", 0))  # Reserved, non-listening port: no primary POST can arrive.
            primary = "http://127.0.0.1:"+str(blocked.getsockname()[1])+"/primary"
            result = self.cluster(self.oa(primary, self.url+"/oa-backup", SANITY_OA_AI_CA_PATH=UNUSED_CA))
            self.assertEqual("fallback", result["metadata"]["routing"]["endpoint"])
            private = profile(endpoint=primary, fallback_endpoint=self.url+"/vwp-backup", ca_path=UNUSED_CA)
            ProfileStore([private])
            _, route = invoke(canonical_json(body()), private, timeout=5, max_output_bytes=4096)
            self.assertEqual({"endpoint":"fallback", "attempted_endpoints":["primary","fallback"]}, route)
        self.assertEqual(["/oa-backup","/vwp-backup"], [r[0] for r in self.requests])

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

    def test_https_ca_is_required_for_primary_and_explicit_backup(self):
        for primary, fallback in (("https://primary.example.test/api", ""),
                                  (self.url+"/primary", "https://backup.example.test/api")):
            with self.subTest(primary=primary), self.assertRaises(ReviewError) as error:
                self.oa(primary, fallback, SANITY_OA_AI_CA_PATH=UNUSED_CA)
            self.assertEqual("AI_CONFIG", error.exception.code)
            private = profile(endpoint=primary, fallback_endpoint=fallback, ca_path=UNUSED_CA)
            with self.assertRaises(ReviewError) as error:
                invoke(canonical_json(body()), private, timeout=5, max_output_bytes=4096)
            self.assertEqual("AI_VWP_PROFILE_CONFIG", error.exception.code)
            self.assertIs(error.exception.model_called, False)
        with self.assertRaises(ReviewError) as error:
            AIAdapter({"SANITY_AI_MODE":"api", "SANITY_AI_URL":"https://primary.example.test/api",
                "SANITY_AI_TOKEN":"synthetic-legacy-token", "SANITY_AI_MODEL":"synthetic-model",
                "SANITY_AI_CA_PATH":UNUSED_CA}).propose({"schema_version":1})
        self.assertEqual("AI_CONFIG", error.exception.code)
        self.assertEqual([], self.requests)

    def test_http_server_errors_and_redirects_do_not_contact_backup(self):
        for path in ("/error", "/redirect"):
            for kind in ("oa", "vwp"):
                self.requests.clear()
                with self.subTest(path=path, client=kind), self.assertRaises(ReviewError) as error:
                    if kind == "oa":
                        self.cluster(self.oa(self.url+path, self.url+"/backup"))
                    else:
                        invoke(canonical_json(body()), profile(endpoint=self.url+path, fallback_endpoint=self.url+"/backup"),
                               timeout=5, max_output_bytes=4096)
                self.assertEqual("AI_CALL_FAILED", error.exception.code)
                self.assertEqual([path], [r[0] for r in self.requests])

    def test_http_read_timeout_remains_unknown_without_another_post(self):
        for kind in ("oa", "vwp"):
            seen = []
            def timeout(request):
                seen.append(request)
                raise httpx.ReadTimeout("synthetic response timeout", request=request)
            with self.subTest(client=kind), self.assertRaises(ReviewError) as error:
                if kind == "oa":
                    advisor = OAAdvisor(environment(SANITY_OA_AI_URL=self.url+"/primary", SANITY_OA_AI_FALLBACK_URL=self.url+"/backup"),
                                        transport=httpx.MockTransport(timeout))
                    self.cluster(advisor)
                else:
                    invoke(canonical_json(body()), profile(endpoint=self.url+"/primary", fallback_endpoint=self.url+"/backup"),
                           timeout=5, max_output_bytes=4096, transport=httpx.MockTransport(timeout))
            self.assertEqual("AI_OUTCOME_UNKNOWN", error.exception.code)
            self.assertEqual(1, len(seen))
