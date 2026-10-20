"""Unauthenticated login discovery must not authenticate or touch business data."""
import unittest
from unittest.mock import Mock, patch

from fastapi import APIRouter
from fastapi.testclient import TestClient

from oa import dashboard_api as api


def synthetic_router(*, logout_method="POST"):
    router = APIRouter()

    @router.get("/login")
    def login():
        return {"status": "SYNTHETIC_ONLY"}

    router.add_api_route("/logout", login, methods=[logout_method])
    return router


class AuthConfigTests(unittest.TestCase):
    def get(self, app):
        with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
            return client.get("/api/auth-config")

    def test_local_metadata_needs_no_token_and_exposes_no_credentials(self):
        settings = {"SANITY_AUTH_MODE": "local_token", "SANITY_DASHBOARD_TOKEN": "private-synthetic-secret",
                    "SANITY_DASHBOARD_REVIEWER": "private-synthetic-name"}
        with patch.object(api, "load_environment", return_value=settings):
            response = self.get(api.create_app())
        self.assertEqual(200, response.status_code)
        self.assertEqual({"mode": "local_token", "login_url": None, "logout_url": None, "logout_method": None}, response.json())
        self.assertNotIn("private-synthetic", response.text)
        self.assertEqual("no-store", response.headers["cache-control"])

    def test_explicit_sso_overrides_environment_without_calling_sdk_directory_or_db(self):
        provider, directory = Mock(), Mock()
        with patch.object(api, "load_environment", return_value={"SANITY_AUTH_MODE": "local_token"}), \
                patch.object(api, "Database") as db:
            app = api.create_app(auth_mode="sso", identity_provider=provider, user_directory=directory,
                                 auth_router=synthetic_router())
            response = self.get(app)
        self.assertEqual({"mode": "sso", "login_url": "/auth/login", "logout_url": "/auth/logout", "logout_method": "POST"}, response.json())
        provider.authenticate.assert_not_called()
        directory.resolve.assert_not_called()
        db.assert_not_called()

    def test_unconnected_sso_stays_sso_with_no_advertised_login_route(self):
        response = self.get(api.create_app(auth_mode="sso"))
        self.assertEqual(200, response.status_code)
        self.assertEqual({"mode": "sso", "login_url": None, "logout_url": None, "logout_method": None}, response.json())

    def test_get_logout_is_not_advertised_as_the_post_contract(self):
        response = self.get(api.create_app(auth_mode="sso", auth_router=synthetic_router(logout_method="GET")))
        self.assertEqual("/auth/login", response.json()["login_url"])
        self.assertIsNone(response.json()["logout_url"])
        self.assertIsNone(response.json()["logout_method"])

    def test_local_mode_does_not_advertise_an_injected_sso_router(self):
        response = self.get(api.create_app(auth_mode="local_token", auth_router=synthetic_router()))
        self.assertEqual("local_token", response.json()["mode"])
        self.assertIsNone(response.json()["login_url"])

    def test_environment_mode_is_resolved_on_each_request_like_authentication(self):
        app = api.create_app()
        with patch.object(api, "load_environment", return_value={"SANITY_AUTH_MODE": "sso"}):
            self.assertEqual("sso", self.get(app).json()["mode"])
        with patch.object(api, "load_environment", return_value={"SANITY_AUTH_MODE": "local_token"}):
            self.assertEqual("local_token", self.get(app).json()["mode"])

    def test_bad_environment_fails_closed_without_echoing_its_value(self):
        with patch.object(api, "load_environment", return_value={"SANITY_AUTH_MODE": "private-invalid-mode"}):
            response = self.get(api.create_app())
        self.assertEqual(503, response.status_code)
        self.assertEqual({"code": "AUTH_CONFIG"}, response.json())

    def test_public_metadata_still_rejects_an_unconfigured_host(self):
        with TestClient(api.create_app(), base_url="http://unconfigured.example.invalid", raise_server_exceptions=False) as client:
            response = client.get("/api/auth-config", headers={"X-Forwarded-Host": "127.0.0.1"})
        self.assertEqual(400, response.status_code)
