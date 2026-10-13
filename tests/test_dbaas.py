"""HTTP access contract tests: synthetic secrets and mock transport, no internal service calls."""
from contextlib import redirect_stderr, redirect_stdout
import io
import logging
import os
from pathlib import Path
import ssl
import tempfile
import traceback
import unittest
from unittest.mock import patch

from sanity_common import ReviewError, atomic_text
import sanity_config
import sanity_dbaas
from sanity_db import Database

try:
    import httpx
    import dotenv
except ImportError:
    httpx = None


@unittest.skipUnless(httpx is not None, "Install requirements.txt for DBaaS HTTP tests")
class DbaasTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for manager in (patch.dict(os.environ, {}, clear=True),
                        patch.object(sanity_config, "ENV_FILE", self.root / ".env")):
            manager.start()
            self.addCleanup(manager.stop)
        self.settings = sanity_dbaas.DbaasSettings("https://synthetic.invalid", "synthetic-test-token")

    def client(self, handler):
        client = sanity_dbaas.DbaasClient(self.settings, transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_env_file_precedence_and_no_interpolation_or_environment_mutation(self):
        atomic_text(self.root / ".env", "DBAAS_BASE_URL=https://fixture.invalid\nDBAAS_TOKEN='${UNRELATED}'\nUNRELATED=secret\n")
        with patch.dict(os.environ, {"DBAAS_BASE_URL": "https://exported.invalid"}):
            values = sanity_config.load_environment()
        self.assertEqual("https://exported.invalid", values["DBAAS_BASE_URL"])
        self.assertEqual("${UNRELATED}", values["DBAAS_TOKEN"])
        self.assertNotIn("UNRELATED", values)
        self.assertNotIn("DBAAS_TOKEN", os.environ)

    def test_environment_empty_value_is_not_replaced_from_file(self):
        atomic_text(self.root / ".env", "DBAAS_TOKEN=fixture-token\n")
        with patch.dict(os.environ, {"DBAAS_TOKEN": ""}):
            self.assertEqual("", sanity_config.load_environment()["DBAAS_TOKEN"])

    def test_alias_and_conflicting_alias_fail_closed(self):
        config = sanity_dbaas.DbaasSettings.from_environment({"DBASS_BASE_URL": "https://synthetic.invalid/", "DBAAS_TOKEN": "test"})
        self.assertEqual("https://synthetic.invalid/", config.base_url)
        with self.assertRaises(ReviewError) as err:
            sanity_dbaas.DbaasSettings.from_environment({"DBAAS_BASE_URL": "https://one.invalid", "DBASS_BASE_URL": "https://two.invalid", "DBAAS_TOKEN": "test"})
        self.assertEqual("DBAAS_CONFIG", err.exception.code)

    def test_invalid_configuration_and_repr_never_reveal_values(self):
        self.assertEqual("DbaasSettings()", repr(self.settings))
        for base in ("http://synthetic.invalid", "https://user:secret@synthetic.invalid", "https://synthetic.invalid/?token=secret",
                     "https://synthetic.invalid/#secret", "https://synthetic.invalid/../other", "https://synthetic.invalid:bad"):
            with self.subTest(base=base), self.assertRaises(ReviewError):
                sanity_dbaas.DbaasSettings(base, "test")
        for token in ("", "contains space", "test\r\nAuthorization: injected", "한글"):
            with self.subTest(token=token), self.assertRaises(ReviewError):
                sanity_dbaas.DbaasSettings("https://synthetic.invalid", token)

    def test_system_ca_verification_stays_enabled(self):
        context = self.settings.tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)

    def test_pem_and_der_cer_are_supported(self):
        cert = ssl.create_default_context().get_ca_certs(binary_form=True)[0]
        for encoding in ("pem", "der"):
            target = self.root / (encoding + ".cer")
            target.write_bytes(ssl.DER_cert_to_PEM_cert(cert).encode() if encoding == "pem" else cert)
            context = sanity_dbaas.DbaasSettings("https://synthetic.invalid", "test", str(target)).tls_context()
            self.assertTrue(context.check_hostname)
            self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
            self.assertIn(cert, context.get_ca_certs(binary_form=True))

    def test_missing_invalid_or_relative_ca_never_falls_back(self):
        target = self.root / "invalid.cer"
        atomic_text(target, "not a certificate")
        for filename in (str(target), str(self.root / "missing.cer"), "relative.cer"):
            with self.subTest(filename=filename), self.assertRaises(ReviewError) as err:
                sanity_dbaas.DbaasSettings("https://synthetic.invalid", "test", filename).tls_context()
            self.assertEqual("DBAAS_CA_ERROR", err.exception.code)
            self.assertNotIn(filename, str(err.exception))

    def test_headers_paths_timeout_and_explicit_opaque_query_body(self):
        seen = []
        def handler(request):
            seen.append(request)
            self.assertEqual("Bearer synthetic-test-token", request.headers["Authorization"])
            self.assertEqual({"connect": 30, "read": 30, "write": 30, "pool": 30}, request.extensions["timeout"])
            return httpx.Response(200, json={"test": "ok"})
        client = self.client(handler)
        client.me()
        client.resources()
        client.query("resource-123", {"confirmed_api_field": "opaque test body"})
        self.assertEqual(["/api/v1/me", "/api/v1/resources", "/api/v1/resources/resource-123/query"], [r.url.path for r in seen])
        self.assertEqual(b'{"confirmed_api_field":"opaque test body"}', seen[-1].content)

    def test_api_prefix_not_duplicated(self):
        self.settings = sanity_dbaas.DbaasSettings("https://synthetic.invalid/service/api/v1/", "test")
        def handler(request):
            self.assertEqual("/service/api/v1/me", request.url.path)
            return httpx.Response(200, json={})
        self.client(handler).me()

    def test_client_disables_proxy_environment_and_redirects(self):
        original = httpx.Client
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:1"}), \
                patch.object(sanity_dbaas.httpx, "Client", wraps=original) as factory:
            client = self.client(lambda request: httpx.Response(200, json={}))
        self.assertIs(factory.call_args.kwargs["trust_env"], False)
        self.assertIs(factory.call_args.kwargs["follow_redirects"], False)
        self.assertEqual(30, factory.call_args.kwargs["timeout"])
        self.assertEqual({}, client._client._mounts)

    def test_redirect_never_forwards_credentials(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://other.invalid/leak"})
        with self.assertRaises(ReviewError) as err:
            self.client(handler).me()
        self.assertEqual("DBAAS_REDIRECT_REFUSED", err.exception.code)
        self.assertEqual(1, len(requests))

    def test_server_error_details_and_transport_values_are_not_printed(self):
        for status, code in ((401, "DBAAS_UNAUTHORIZED"), (403, "DBAAS_FORBIDDEN"), (404, "DBAAS_NOT_FOUND"), (500, "DBAAS_HTTP_ERROR")):
            with self.subTest(status=status), self.assertRaises(ReviewError) as err:
                self.client(lambda request: httpx.Response(status, json={"detail": "secret-response-body"})).me()
            self.assertEqual(code, err.exception.code)
            self.assertNotIn("secret-response-body", str(err.exception))
        def fail(request):
            raise httpx.ConnectError("secret-connection-values", request=request)
        try:
            self.client(fail).me()
        except ReviewError:
            self.assertNotIn("secret-connection-values", traceback.format_exc())

    def test_post_timeout_is_uncertain_and_never_retried(self):
        attempts = []
        def timeout(request):
            attempts.append(request)
            raise httpx.ReadTimeout("secret-connection-values", request=request)
        with self.assertRaises(ReviewError) as err:
            self.client(timeout).query("resource-123", {"opaque": "body"})
        self.assertEqual("DBAAS_QUERY_UNCERTAIN", err.exception.code)
        self.assertEqual(1, len(attempts))

    def test_post_bad_json_or_server_error_is_uncertain(self):
        for response in (httpx.Response(503, json={"detail": "secret"}),
                         httpx.Response(200, content=b"bad", headers={"content-type": "application/json"})):
            with self.subTest(status=response.status_code), self.assertRaises(ReviewError) as err:
                self.client(lambda request: response).query("resource-123", {"opaque": "body"})
            self.assertEqual("DBAAS_QUERY_UNCERTAIN", err.exception.code)

    def test_invalid_and_duplicate_json_are_rejected(self):
        for content in (b"not-json", b'{"key":1,"key":2}', b'{"key":NaN}', b"null"):
            with self.subTest(content=content), self.assertRaises(ReviewError) as err:
                self.client(lambda request: httpx.Response(200, content=content, headers={"content-type": "application/json"})).me()
            self.assertEqual("DBAAS_INVALID_RESPONSE", err.exception.code)

    def test_request_and_response_limits(self):
        client = self.client(lambda request: httpx.Response(200, json={"text": "x" * 100}))
        with patch.object(sanity_dbaas, "MAX_HTTP_BYTES", 16):
            with self.assertRaises(ReviewError) as err:
                client.me()
            self.assertEqual("DBAAS_RESPONSE_TOO_LARGE", err.exception.code)
            with self.assertRaises(ReviewError) as err:
                client.query("resource-123", {"text": "x" * 100})
            self.assertEqual("DBAAS_REQUEST_TOO_LARGE", err.exception.code)

    def test_resource_path_injection_is_rejected(self):
        client = self.client(lambda request: self.fail("No request should be sent"))
        for resource in ("../other", "a/b", "https://other.invalid", "a?token=x", "a%2Fb", "", True):
            with self.subTest(resource=resource), self.assertRaises(ReviewError):
                client.query(resource, {"opaque": "body"})

    def test_check_outputs_status_only_and_http_logging_is_quiet(self):
        output, errors, logs = io.StringIO(), io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        logger = logging.getLogger("httpx")
        prior = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, prior)
        client = self.client(lambda request: httpx.Response(200, json={"token": "secret-response-body"}))
        with patch.object(sanity_dbaas, "DbaasClient", return_value=client), redirect_stdout(output), redirect_stderr(errors):
            code = sanity_dbaas.main(["check", "--resources"])
        self.assertEqual(0, code)
        self.assertEqual("status=CONNECTED backend=DBAAS_HTTP checks=me,resources\n", output.getvalue())
        self.assertEqual("", errors.getvalue() + logs.getvalue())

    def test_cli_error_never_prints_server_body_or_configuration(self):
        output, errors = io.StringIO(), io.StringIO()
        client = self.client(lambda request: httpx.Response(401, json={"detail": "secret-response-body"}))
        with patch.object(sanity_dbaas, "DbaasClient", return_value=client), redirect_stdout(output), redirect_stderr(errors):
            code = sanity_dbaas.main(["check"])
        self.assertEqual(2, code)
        self.assertEqual("code=DBAAS_UNAUTHORIZED\n", errors.getvalue())
        self.assertEqual("", output.getvalue())

    def test_bridge_never_falls_back_to_direct_database_in_http_mode(self):
        with patch.dict(os.environ, {"SANITY_VWP_DSN": "unused-secret-dsn"}), self.assertRaises(ReviewError) as err:
            Database("vwp")
        self.assertEqual("DBAAS_SQL_CONTRACT_REQUIRED", err.exception.code)
        with patch.dict(os.environ, {"SANITY_DB_ACCESS": "postgresql"}), self.assertRaises(ReviewError) as err:
            Database("vwp")
        self.assertIn(err.exception.code, ("DATABASE_NOT_CONFIGURED", "DATABASE_DRIVER_MISSING"))


if __name__ == "__main__":
    unittest.main()
