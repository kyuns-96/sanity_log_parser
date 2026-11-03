"""Injected SSO contracts at the real API boundary; no company network or DB required."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oa.sanity_auth import AuthError, LocalUser, SSOIdentityProvider, UserDirectory, VerifiedIdentity

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


    def test_unverified_headers_and_local_token_do_not_authenticate_sso(self):
        for headers in ({"X-User-ID": "admin", "X-Remote-User": "trusted-user"},
                        {"Authorization": "Bearer " + os.environ["SANITY_DASHBOARD_TOKEN"]}):
            self.assertEqual({"code": "UNAUTHORIZED"}, self.client.get("/api/session", headers=headers).json())
