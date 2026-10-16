"""Injected SSO contracts at the real API boundary; no company network or DB required."""
import importlib.util
import os
from pathlib import Path
import re
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from fastapi import APIRouter
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sanity_auth import AuthError, LocalUser, SSOIdentityProvider, UserDirectory, VerifiedIdentity

spec = importlib.util.spec_from_file_location("sso_dashboard_tests_target", Path(__file__).with_name("dashboard_api.py"))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class TestOnlyIdentityProvider(SSOIdentityProvider):
    def authenticate(self, request):
        if request.headers.get("authorization") != "Bearer test-only-sso" and request.cookies.get("test-session") != "test-only-session":
            raise AuthError("UNAUTHORIZED")
        return VerifiedIdentity("test-issuer", "test-subject")


class TestOnlyDirectory(UserDirectory):
    user = LocalUser("usr-101", "사내 예시 사용자")

    def resolve(self, identity):
        return self.user if identity == VerifiedIdentity("test-issuer", "test-subject") else None


class SSOApiTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"SANITY_DB_ACCESS": "postgresql", "SANITY_AUTH_MODE": "local_token",
                         "SANITY_DASHBOARD_TOKEN": "test-local-token-that-must-not-be-accepted-in-sso",
                         "SANITY_DASHBOARD_REVIEWER": "legacy name"})
        env.start()
        self.addCleanup(env.stop)
        self.provider = TestOnlyIdentityProvider()
        self.directory = TestOnlyDirectory()
        self.app = api.create_app(auth_mode="sso", identity_provider=self.provider, user_directory=self.directory)
        self.client = TestClient(self.app, base_url="http://127.0.0.1", raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_verified_sso_session_exposes_only_normalized_application_identity(self):
        response = self.client.get("/api/session", headers={"Authorization": "Bearer test-only-sso", "X-User-ID": "admin"})
        self.assertEqual(200, response.status_code)
        value = response.json()
        self.assertEqual("usr-101", value["user_id"])
        self.assertEqual("사내 예시 사용자", value["reviewer"])
        self.assertEqual("sso", value["auth_method"])
        self.assertFalse(value["writable"])
        self.assertFalse(value["authorization_ready"])
        self.assertNotIn("test-only-sso", response.text)
        self.assertNotIn("test-subject", response.text)
        self.assertNotIn("legacy name", response.text)
        self.assertEqual("no-store", response.headers["cache-control"])

    def test_cookie_credentials_reach_provider_without_assuming_a_cookie_name(self):
        self.client.cookies.set("test-session", "test-only-session")
        self.assertEqual(200, self.client.get("/api/session").status_code)
        self.client.cookies.clear()
        self.assertEqual(401, self.client.get("/api/session").status_code)

    def test_unverified_headers_and_local_token_do_not_authenticate_sso(self):
        for headers in ({"X-User-ID": "admin", "X-Remote-User": "trusted-user"},
                        {"Authorization": "Bearer " + os.environ["SANITY_DASHBOARD_TOKEN"]}):
            self.assertEqual({"code": "UNAUTHORIZED"}, self.client.get("/api/session", headers=headers).json())

    def test_disabled_unlinked_and_failed_directory_are_distinct(self):
        self.client.headers["Authorization"] = "Bearer test-only-sso"
        for user, code in ((None, "ACCOUNT_NOT_LINKED"), (LocalUser("usr-101", "name", active=False), "ACCOUNT_DISABLED")):
            self.directory.user = user
            result = self.client.get("/api/session")
            self.assertEqual(403, result.status_code)
            self.assertEqual({"code": code}, result.json())
        with patch.object(self.directory, "resolve", side_effect=RuntimeError("private database credentials")):
            result = self.client.get("/api/session")
        self.assertEqual(503, result.status_code)
        self.assertEqual({"code": "AUTH_DIRECTORY_UNAVAILABLE"}, result.json())

    def test_unconfigured_sso_and_provider_failure_return_fixed_503_codes(self):
        with TestClient(api.create_app(auth_mode="sso"), base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
            response = client.get("/api/session")
            self.assertEqual(503, response.status_code)
            self.assertEqual({"code": "AUTH_SSO_NOT_CONFIGURED"}, response.json())
        with patch.object(self.provider, "authenticate", side_effect=RuntimeError("private token and URL")):
            response = self.client.get("/api/session")
        self.assertEqual(503, response.status_code)
        self.assertEqual({"code": "AUTH_PROVIDER_UNAVAILABLE"}, response.json())

    def test_every_business_route_is_blocked_before_storage_until_scoped_authorization_exists(self):
        self.client.headers["Authorization"] = "Bearer test-only-sso"
        ids = {"bundle_id": "b-" + "a" * 64, "unit_id": "u-1", "jid": "job-1",
               "request_id": "00000000-0000-0000-0000-000000000001", "record_id": "r-1"}
        routes = [route for route in self.app.routes if getattr(route, "path", "").startswith("/api/")
                  and route.path not in {"/api/session", "/api/health", "/api/{path:path}"}]
        self.assertGreaterEqual(len(routes), 15)
        with patch.object(api, "Database") as db, patch.object(api.commands, "receive") as receive, \
                patch.object(api, "request_evidence") as evidence, patch.object(api, "receive_evidence") as receive_evidence:
            for route in routes:
                path = re.sub(r"\{([^}]+)\}", lambda match: ids[match[1]], route.path)
                for method in route.methods:
                    with self.subTest(method=method, path=path):
                        response = self.client.request(method, path, **({"json": {}} if method == "POST" else {}))
                        self.assertEqual(503, response.status_code, response.text)
                        self.assertEqual({"code": "AUTHORIZATION_NOT_IMPLEMENTED"}, response.json())
            for mock in (db, receive, evidence, receive_evidence):
                mock.assert_not_called()

    def test_company_auth_router_is_mounted_before_static_ui_under_auth_prefix(self):
        router = APIRouter()

        @router.get("/contract-probe")
        def probe():
            return {"status": "TEST_ROUTE_ONLY"}

        app = api.create_app(auth_mode="sso", auth_router=router)
        with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
            response = client.get("/auth/contract-probe")
            self.assertEqual(200, response.status_code)
            self.assertEqual({"status": "TEST_ROUTE_ONLY"}, response.json())
            self.assertEqual(503, client.get("/api/session").status_code)

    def test_unconfigured_company_entrypoint_never_uses_a_local_token(self):
        # This common contract must not import the company's real SDK or require
        # its adapter to stay unimplemented after an authorized integration.
        class UnconfiguredIdentity(SSOIdentityProvider):
            def authenticate(self, request):
                raise AuthError("AUTH_SSO_NOT_CONFIGURED")

        class UnconfiguredDirectory(UserDirectory):
            def resolve(self, identity):
                raise AuthError("AUTH_DIRECTORY_NOT_CONFIGURED")

        company = ModuleType("corporate_sso")
        company.CorporateIdentityProvider = UnconfiguredIdentity
        company.CorporateUserDirectory = UnconfiguredDirectory
        spec = importlib.util.spec_from_file_location("company_sso_entrypoint_test", Path(__file__).with_name("sso_app.py"))
        entrypoint = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"corporate_sso": company}):
            spec.loader.exec_module(entrypoint)
        with TestClient(entrypoint.app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
            response = client.get("/api/session", headers={"Authorization": "Bearer " + os.environ["SANITY_DASHBOARD_TOKEN"]})
        self.assertEqual(503, response.status_code)
        self.assertEqual({"code": "AUTH_SSO_NOT_CONFIGURED"}, response.json())
