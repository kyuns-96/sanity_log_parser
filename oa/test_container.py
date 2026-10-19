"""Direct container HTTP boundary behind company ingress; no company DB/SDK."""
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from oa import dashboard_api as api
from oa.sanity_auth import AuthError, SSOIdentityProvider, UserDirectory
from oa.sanity_hosts import allowed_hosts


class ContainerHostTests(unittest.TestCase):
    def test_exact_hosts_are_normalized_and_local_health_stays_available(self):
        self.assertEqual(
            ["oa.example.invalid", "192.0.2.10", "[::1]", "127.0.0.1"],
            allowed_hosts(" OA.Example.Invalid,192.0.2.10,::1,oa.example.invalid "),
        )

    def test_bad_config_is_rejected_without_echoing_its_value(self):
        for value in ("", " ", "oa.example.invalid,", "*", "*.example.invalid", ".example.invalid",
                      "https://oa.example.invalid", "oa.example.invalid:8080", "good; return 200; #",
                      "good\nbad", "good bad", "~.*", "bad_name", "-bad.example.invalid",
                      "a" * 254, "[::1];", "fe80::1%eth0", "[fe80::1%injected;]", 123):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^CONTAINER_HOST_CONFIG$"):
                api.create_app(auth_mode="sso", allowed_hosts=value)

    def test_ingress_domain_and_health_host_work_without_an_internal_nginx(self):
        app = api.create_app(auth_mode="sso", allowed_hosts="oa.example.invalid")
        with TestClient(app, base_url="http://oa.example.invalid:8080", raise_server_exceptions=False) as client:
            self.assertEqual(200, client.get("/api/health").status_code)
            self.assertEqual(200, client.get("/api/health", headers={"Host": "127.0.0.1:8080"}).status_code)
            self.assertEqual(503, client.get("/api/session").status_code)

    def test_unconfigured_hosts_are_rejected_even_with_a_forged_forwarded_host(self):
        app = api.create_app(auth_mode="sso", allowed_hosts="oa.example.invalid")
        with TestClient(app, base_url="http://oa.example.invalid", raise_server_exceptions=False) as client:
            for host in ("unconfigured.example.invalid", "www.oa.example.invalid", "oa.example.invalid.evil"):
                with self.subTest(host=host):
                    response = client.get("/api/health", headers={
                        "Host": host, "X-Forwarded-Host": "oa.example.invalid",
                        "Forwarded": "host=oa.example.invalid;proto=https",
                    }, follow_redirects=False)
                    self.assertEqual(400, response.status_code)

    def test_legacy_factory_remains_local_even_when_container_hosts_are_configured(self):
        with patch.dict(os.environ, {"OA_ALLOWED_HOSTS": "oa.example.invalid"}):
            app = api.create_app(auth_mode="sso")
        with TestClient(app, base_url="http://oa.example.invalid", raise_server_exceptions=False) as client:
            self.assertEqual(400, client.get("/api/health").status_code)
            self.assertEqual(200, client.get("/api/health", headers={"Host": "localhost"}).status_code)

    def test_company_entrypoint_uses_hosts_but_never_falls_back_to_local_auth(self):
        class UnconfiguredIdentity(SSOIdentityProvider):
            def authenticate(self, request):
                raise AuthError("AUTH_SSO_NOT_CONFIGURED")

        class UnconfiguredDirectory(UserDirectory):
            def resolve(self, identity):
                raise AuthError("AUTH_DIRECTORY_NOT_CONFIGURED")

        company = ModuleType("oa.integration.corporate_sso")
        company.CorporateIdentityProvider = UnconfiguredIdentity
        company.CorporateUserDirectory = UnconfiguredDirectory
        spec = importlib.util.spec_from_file_location("container_sso_test", Path(__file__).with_name("sso_app.py"))
        entrypoint = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"OA_ALLOWED_HOSTS": "oa.example.invalid", "SANITY_AUTH_MODE": "local_token",
                                    "SANITY_DASHBOARD_TOKEN": "test-only-local-token-must-not-be-used"}), \
                patch.dict(sys.modules, {"oa.integration.corporate_sso": company}):
            spec.loader.exec_module(entrypoint)
            with TestClient(entrypoint.app, base_url="http://oa.example.invalid", raise_server_exceptions=False) as client:
                self.assertEqual(200, client.get("/api/health").status_code)
                response = client.get("/api/session", headers={
                    "Authorization": "Bearer test-only-local-token-must-not-be-used", "X-User-ID": "admin",
                })
                self.assertEqual(503, response.status_code)
                self.assertEqual({"code": "AUTH_SSO_NOT_CONFIGURED"}, response.json())
