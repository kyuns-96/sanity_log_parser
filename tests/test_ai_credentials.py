"""Personal credentials: synthetic crypto, isolated DB, and SSO HTTP boundaries."""
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from fastapi import Request
from fastapi.testclient import TestClient

from common.sanity_common import ReviewError, canonical_json
from common.sanity_config import load_environment
from common.sanity_db import Database, json_value
from common.sanity_privacy import SecretGuard, MASK
from oa import ai_tokens_api
from oa.access_api import require_mutation_request
from oa.dashboard_api import authorized_actor, create_app
from oa.sanity_ai_tokens import UserTokenStore, expiry_hint, PREFIX
from oa.sanity_ai_model import OASettings, OAAdvisor
from tests.support import PgTestCase
from tests.test_ai_model import environment


def synthetic_jwt(*, days=365, subject="synthetic-person"):
    part = lambda value: base64.urlsafe_b64encode(canonical_json(value)).decode().rstrip("=")
    return part({"alg": "HS256", "typ": "JWT"}) + "." + part({"sub": subject,
        "exp": int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())}) + ".synthetic-signature"


def credential_environment():
    return {"SANITY_OA_AI_TOKEN_SOURCE": "user", "SANITY_OA_AI_TOKEN_STORE_SECRET": Fernet.generate_key().decode()}


class CredentialConfigurationTests(unittest.TestCase):
    def test_issuance_url_is_loaded_at_runtime_and_exported_settings_override_the_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            path.write_text("SANITY_OA_AI_TOKEN_ISSUE_URL=https://identity.example.test/tokens\n", encoding="utf-8")
            self.assertEqual("https://identity.example.test/tokens", load_environment(path)["SANITY_OA_AI_TOKEN_ISSUE_URL"])
            with patch.dict(os.environ, {"SANITY_OA_AI_TOKEN_ISSUE_URL": "https://identity.example.test/new-page"}):
                self.assertEqual("https://identity.example.test/new-page", load_environment(path)["SANITY_OA_AI_TOKEN_ISSUE_URL"])
            with patch.dict(os.environ, {"SANITY_OA_AI_TOKEN_ISSUE_URL": ""}):
                self.assertEqual("", load_environment(path)["SANITY_OA_AI_TOKEN_ISSUE_URL"])

    def test_user_mode_discards_the_server_token_and_requires_a_bound_personal_token(self):
        settings = OASettings.from_env(environment(SANITY_OA_AI_TOKEN_SOURCE="user"))
        self.assertEqual("", settings.token)
        advisor = OAAdvisor(settings)
        bound = advisor.with_user_token(synthetic_jwt())
        self.assertEqual(advisor.describe(), bound.describe())
        self.assertNotEqual("", bound.settings.token)
        self.assertEqual("", advisor.settings.token)
        with self.assertRaises(ReviewError):
            advisor.with_user_token("")

    def test_expiry_is_only_an_unverified_hint_and_bad_claims_are_not_authenticated(self):
        self.assertGreater(expiry_hint(synthetic_jwt()), datetime.now(timezone.utc))
        self.assertLess(expiry_hint(synthetic_jwt(days=-1)), datetime.now(timezone.utc))
        for value in ("opaque-synthetic-token", "a.invalid.c", "a.e30.c", "a.W10.c"):
            self.assertIsNone(expiry_hint(value))

    def test_store_key_is_required_and_never_echoed(self):
        for key in ("", "invalid-synthetic-secret", "한글", None):
            with self.subTest(key=key), self.assertRaises(ReviewError) as error:
                UserTokenStore(Mock(), {"SANITY_OA_AI_TOKEN_SOURCE": "user", "SANITY_OA_AI_TOKEN_STORE_SECRET": key})
            self.assertEqual("AI_TOKEN_STORE_NOT_CONFIGURED", error.exception.code)
            if key:
                self.assertNotIn(key, str(error.exception))
        settings = credential_environment()
        self.assertEqual(MASK, SecretGuard(settings).text(settings["SANITY_OA_AI_TOKEN_STORE_SECRET"]))


class CredentialDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.settings = credential_environment()

    def test_owner_bound_encryption_status_and_removal_keep_plaintext_out_of_db(self):
        token_a, token_b = synthetic_jwt(subject="person-a"), synthetic_jwt(subject="person-b")
        with Database("oa") as db, db.transaction():
            store = UserTokenStore(db, self.settings)
            self.assertFalse(store.status("person-a")["registered"])
            status = store.save("person-a", token_a, 0)
            self.assertTrue(status["registered"])
            self.assertEqual(1, status["revision"])
            self.assertEqual((token_a, 1), store.get_token("person-a"))
            self.assertFalse(store.status("person-b")["registered"])
            store.save("person-b", token_b, 0)
            self.assertEqual((token_b, 1), store.get_token("person-b"))
            self.assertNotIn(token_a, json.dumps(status))
            with self.assertRaises(ReviewError) as error:
                store.save("person-a", token_b, 0)
            self.assertEqual("REVISION_CONFLICT", error.exception.code)
            self.assertFalse(store.save("person-a", None, 1)["registered"])
            with self.assertRaises(ReviewError) as error:
                store.get_token("person-a")
            self.assertEqual("AI_USER_TOKEN_REQUIRED", error.exception.code)
            self.assertEqual((token_b, 1), store.get_token("person-b"))
        rows = self.admin_query("SELECT payload,state,version FROM {oa} WHERE record_type='WS_CONFIG'").fetchall()
        serialized = json.dumps(rows, default=str)
        self.assertNotIn(token_a, serialized)
        self.assertNotIn(token_b, serialized)
        self.assertNotIn(self.settings["SANITY_OA_AI_TOKEN_STORE_SECRET"], serialized)
        self.assertEqual(3, len(rows))
        self.assertTrue(all(row[1:] == ({}, 0) for row in rows))

    def test_ciphertext_cannot_be_transplanted_between_users(self):
        with Database("oa") as db, db.transaction():
            store = UserTokenStore(db, self.settings)
            store.save("person-a", synthetic_jwt(subject="a"), 0)
            store.save("person-b", synthetic_jwt(subject="b"), 0)
        encrypted = self.admin_query("SELECT payload->>'ciphertext' FROM {oa} WHERE payload->>'user_id'='person-a'").fetchone()[0]
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{ciphertext}}',%s) WHERE payload->>'user_id'='person-b'",
                         (json_value(encrypted),))
        with Database("oa") as db, db.transaction(), self.assertRaises(ReviewError) as error:
            UserTokenStore(db, self.settings).get_token("person-b")
        self.assertEqual("AI_TOKEN_UNAVAILABLE", error.exception.code)

    def test_wrong_key_never_falls_back_to_another_token_or_plaintext(self):
        with Database("oa") as db, db.transaction():
            UserTokenStore(db, self.settings).save("person-a", synthetic_jwt(), 0)
        with Database("oa") as db, db.transaction(), self.assertRaises(ReviewError) as error:
            UserTokenStore(db, credential_environment()).get_token("person-a")
        self.assertEqual("AI_TOKEN_UNAVAILABLE", error.exception.code)

    def test_expired_and_header_injection_tokens_are_rejected_without_writes(self):
        with Database("oa") as db, db.transaction():
            store = UserTokenStore(db, self.settings)
            for token, code in ((synthetic_jwt(days=-1), "AI_USER_TOKEN_EXPIRED"), ("bad\r\nheader", "INVALID_AI_TOKEN"),
                                ("Bearer value", "INVALID_AI_TOKEN"), ("x" * 8193, "INVALID_AI_TOKEN")):
                with self.subTest(code=code), self.assertRaises(ReviewError) as error:
                    store.save("person-a", token, 0)
                self.assertEqual(code, error.exception.code)
            self.assertEqual(0, store.status("person-a")["revision"])

    def test_expiry_status_changes_without_revalidating_the_signature_locally(self):
        with Database("oa") as db, db.transaction():
            store = UserTokenStore(db, self.settings)
            status = store.save("person-a", synthetic_jwt(days=10), 0)
            self.assertTrue(status["expires_soon"])
            self.assertFalse(status["expired"])
            with patch("oa.sanity_ai_tokens.expiry_hint", return_value=datetime.now(timezone.utc) - timedelta(days=1)):
                self.assertTrue(store.status("person-a")["expired"])
                with self.assertRaises(ReviewError) as error:
                    store.get_token("person-a")
                self.assertEqual("AI_USER_TOKEN_EXPIRED", error.exception.code)

    def test_token_replacement_waits_for_the_current_publication_transaction(self):
        with Database("oa") as db, db.transaction():
            UserTokenStore(db, self.settings).save("person-a", "synthetic-original-token", 0)
        with Database("oa") as publication, publication.transaction():
            self.assertEqual(("synthetic-original-token", 1), UserTokenStore(publication, self.settings).get_token("person-a"))
            with self.assertRaises(ReviewError) as error:
                with Database("oa") as writer, writer.transaction():
                    writer.execute("SET LOCAL lock_timeout='100ms'")
                    UserTokenStore(writer, self.settings).save("person-a", "synthetic-replacement-token", 1)
            self.assertTrue(error.exception.code.startswith("DATABASE_"))
        with Database("oa") as db, db.transaction():
            store = UserTokenStore(db, self.settings)
            self.assertEqual(2, store.save("person-a", "synthetic-replacement-token", 1)["revision"])
            self.assertEqual(("synthetic-replacement-token", 2), store.get_token("person-a"))


class CredentialApiTests(unittest.TestCase):
    def setUp(self):
        self.actor = SimpleNamespace(auth_method="sso", user_id="synthetic-self", access=SimpleNamespace(db=object()))
        self.store = Mock()
        self.store.status.return_value = {"enabled": True, "registered": False, "revision": 0}
        self.store.save.return_value = {"enabled": True, "registered": True, "revision": 1}
        factory = patch.object(ai_tokens_api, "UserTokenStore", return_value=self.store)
        self.factory = factory.start(); self.addCleanup(factory.stop)
        settings = patch.object(ai_tokens_api, "load_environment", return_value={"SANITY_OA_AI_TOKEN_SOURCE": "user"})
        settings.start(); self.addCleanup(settings.stop)
        app = create_app(auth_mode="sso")

        def verified_actor(request: Request):
            if request.method == "POST":
                require_mutation_request(request)
            return self.actor
        app.dependency_overrides[authorized_actor] = verified_actor
        self.client = TestClient(app, base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)
        self.headers = {"X-Sanity-Request": "1", "Origin": "http://127.0.0.1", "Sec-Fetch-Site": "same-origin"}

    def test_status_save_and_removal_are_always_bound_to_authenticated_self(self):
        response = self.client.get("/api/ai/credentials?user_id=someone-else")
        self.assertEqual(200, response.status_code)
        self.store.status.assert_called_once_with("synthetic-self")
        token = synthetic_jwt()
        response = self.client.post("/api/ai/credentials", headers=self.headers, json={"token": token, "expected_revision": 0})
        self.assertEqual(200, response.status_code)
        self.store.save.assert_called_once_with("synthetic-self", token, 0)
        self.assertNotIn(token, response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        self.client.post("/api/ai/credentials/remove", headers=self.headers, json={"expected_revision": 1})
        self.store.save.assert_called_with("synthetic-self", None, 1)

    def test_forged_owners_and_invalid_body_never_echo_tokens_or_save(self):
        token = synthetic_jwt()
        for body in ({"token": token, "expected_revision": 0, "user_id": "other"},
                     {"token": token, "expected_revision": True}, {"token": token, "expected_revision": "0"},
                     {"token": "x" * 8193, "expected_revision": 0}):
            response = self.client.post("/api/ai/credentials", headers=self.headers, json=body)
            self.assertEqual(400, response.status_code)
            self.assertEqual({"code": "INVALID_REQUEST"}, response.json())
            self.assertNotIn(token, response.text)
        self.store.save.assert_not_called()

    def test_origin_client_marker_and_local_identity_cannot_bypass_self_service(self):
        for headers in ({}, self.headers | {"Origin": "https://other.invalid"},
                        self.headers | {"Sec-Fetch-Site": "cross-site"}):
            response = self.client.post("/api/ai/credentials", headers=headers, json={"token": synthetic_jwt(), "expected_revision": 0})
            self.assertEqual(403, response.status_code)
        self.actor = SimpleNamespace(auth_method="local_token", user_id="synthetic-self", access=SimpleNamespace(db=object()))
        self.assertEqual(403, self.client.get("/api/ai/credentials").status_code)
        self.factory.assert_not_called()

    def test_personal_registration_is_explicitly_disabled_in_server_mode(self):
        with patch.object(ai_tokens_api, "load_environment", return_value={}):
            self.assertEqual({"enabled": False}, self.client.get("/api/ai/credentials").json())
        self.factory.assert_not_called()

    def test_issuance_link_is_runtime_metadata_retained_after_save_and_removal(self):
        url = "https://identity.example.test:8443/ai/tokens?app=sanity#issue"
        settings = {"SANITY_OA_AI_TOKEN_SOURCE": "user", "SANITY_OA_AI_TOKEN_ISSUE_URL": url}
        with patch.object(ai_tokens_api, "load_environment", return_value=settings):
            response = self.client.get("/api/ai/credentials")
            self.assertEqual(200, response.status_code)
            self.assertEqual(url, response.json()["issuance_url"])
            self.assertIn("no-store", response.headers["cache-control"])
            for path, body in (("", {"token": synthetic_jwt(), "expected_revision": 0}),
                               ("/remove", {"expected_revision": 1})):
                response = self.client.post("/api/ai/credentials" + path, headers=self.headers, json=body)
                self.assertEqual(200, response.status_code)
                self.assertEqual(url, response.json()["issuance_url"])
            settings["SANITY_OA_AI_TOKEN_ISSUE_URL"] = "https://identity.example.test/replacement"
            self.assertEqual(settings["SANITY_OA_AI_TOKEN_ISSUE_URL"], self.client.get("/api/ai/credentials").json()["issuance_url"])

    def test_missing_or_unsafe_issuance_urls_hide_only_the_link(self):
        for value in (None, "", "javascript:alert(1)", "data:text/html,hello", "http://identity.example.test/tokens",
                      "/tokens", "//identity.example.test/tokens", "https:///tokens", "https://",
                      "https://user:password@identity.example.test/tokens", "https://@identity.example.test/tokens",
                      "https://identity.example.test:invalid/tokens", "https://identity.example.test:0/tokens",
                      "https://identity.example.test:65536/tokens", "https://[broken/tokens",
                      "https://identity.example.test/with space", " https://identity.example.test/tokens",
                      "https://identity.example.test/\nother", "https://identity.example.test/\x00other",
                      "https://identity.example.test\\other", "https://identity.example.test/" + "a" * 2048):
            settings = {"SANITY_OA_AI_TOKEN_SOURCE": "user", "SANITY_OA_AI_TOKEN_ISSUE_URL": value}
            with self.subTest(url=value), patch.object(ai_tokens_api, "load_environment", return_value=settings):
                response = self.client.get("/api/ai/credentials")
                self.assertEqual(200, response.status_code)
                self.assertTrue(response.json()["enabled"])
                self.assertIsNone(response.json()["issuance_url"])
        self.store.save.assert_not_called()
