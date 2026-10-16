"""SSO adapter contract tests. All identities and credentials are test doubles."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.sanity_config import load_environment
from oa.sanity_auth import Actor, AuthError, AuthRequest, AuthenticationService, LocalUser, SSOIdentityProvider, UserDirectory, VerifiedIdentity, require_business_access

LOCAL = {"SANITY_AUTH_MODE": "local_token", "SANITY_DASHBOARD_TOKEN": "test-only-local-token-at-least-32-characters",
         "SANITY_DASHBOARD_REVIEWER": "local reviewer"}


def request(token="test-only-company-credential"):
    return AuthRequest(method="GET", path="/api/session", headers={"Authorization": "Bearer " + token},
                       cookies={"company_session": "test-only-cookie"})


class AuthenticationContractTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock(spec=SSOIdentityProvider)
        self.directory = Mock(spec=UserDirectory)
        self.identity = VerifiedIdentity(issuer="test-issuer-a", subject="subject-1")
        self.provider.authenticate.return_value = self.identity
        self.directory.resolve.return_value = LocalUser("usr-101", "김철수")
        self.service = AuthenticationService(mode="sso", identity_provider=self.provider, user_directory=self.directory)

    def assertCode(self, code, action):
        with self.assertRaises(AuthError) as error:
            action()
        self.assertEqual(code, error.exception.code)

    def test_interfaces_are_abstract(self):
        for interface in (SSOIdentityProvider, UserDirectory):
            with self.assertRaises(TypeError):
                interface()

    def test_request_credentials_are_read_only_and_not_in_repr(self):
        value = request()
        self.assertIn("authorization", value.headers)
        self.assertNotIn("test-only-company-credential", repr(value))
        self.assertNotIn("test-only-cookie", repr(value))
        with self.assertRaises(TypeError):
            value.headers["authorization"] = "forged"
        with self.assertRaises(TypeError):
            value.cookies["company_session"] = "forged"

    def test_local_mode_compatibility_and_untrusted_user_headers(self):
        value = AuthRequest("GET", "/api/session", {"Authorization": "Bearer " + LOCAL["SANITY_DASHBOARD_TOKEN"],
                            "X-User-ID": "administrator", "X-Reviewer": "forged"}, {})
        actor = AuthenticationService().authenticate(value, LOCAL)
        self.assertEqual(Actor("local-reviewer", "local reviewer", "local_token"), actor)
        self.assertCode("UNAUTHORIZED", lambda: AuthenticationService().authenticate(request(), LOCAL))
        self.assertEqual(actor, require_business_access(actor))

    def test_explicit_sso_mode_and_missing_adapter_never_use_local_token(self):
        self.assertCode("AUTH_SSO_NOT_CONFIGURED", lambda: AuthenticationService().authenticate(
            request(LOCAL["SANITY_DASHBOARD_TOKEN"]), {**LOCAL, "SANITY_AUTH_MODE": "sso"}))
        self.assertCode("AUTH_DIRECTORY_NOT_CONFIGURED", lambda: AuthenticationService(
            mode="sso", identity_provider=self.provider).authenticate(request(), LOCAL))
        self.assertCode("AUTH_CONFIG", lambda: AuthenticationService().authenticate(request(), {"SANITY_AUTH_MODE": "typo"}))
        self.assertCode("AUTH_CONFIG", lambda: AuthenticationService(identity_provider=self.provider))
        self.assertCode("AUTH_CONFIG", lambda: AuthenticationService(mode="local_token", identity_provider=self.provider))

    def test_verified_identity_maps_to_internal_user_not_legacy_reviewer(self):
        actor = self.service.authenticate(request(), LOCAL)
        self.directory.resolve.assert_called_once_with(self.identity)
        self.assertEqual(Actor("usr-101", "김철수", "sso"), actor)
        self.assertCode("AUTHORIZATION_NOT_IMPLEMENTED", lambda: require_business_access(actor))

    def test_issuer_is_part_of_mapping_key_and_same_name_is_not_identity(self):
        users = {VerifiedIdentity("issuer-a", "same-subject"): LocalUser("usr-a", "동명이인"),
                 VerifiedIdentity("issuer-b", "same-subject"): LocalUser("usr-b", "동명이인")}
        self.directory.resolve.side_effect = users.get
        ids = []
        for identity in users:
            self.provider.authenticate.return_value = identity
            ids.append(self.service.authenticate(request(), LOCAL).user_id)
        self.assertEqual(["usr-a", "usr-b"], ids)
        self.provider.authenticate.return_value = VerifiedIdentity("issuer-c", "same-subject")
        self.assertCode("ACCOUNT_NOT_LINKED", lambda: self.service.authenticate(request(), LOCAL))

    def test_each_request_revalidates_expiry_and_does_not_call_directory_after_failure(self):
        self.service.authenticate(request(), LOCAL)
        self.directory.reset_mock()
        self.provider.authenticate.side_effect = AuthError("UNAUTHORIZED")
        self.assertCode("UNAUTHORIZED", lambda: self.service.authenticate(request(LOCAL["SANITY_DASHBOARD_TOKEN"]), LOCAL))
        self.directory.resolve.assert_not_called()
        self.assertEqual(2, self.provider.authenticate.call_count)

    def test_disabled_or_unlinked_account_is_rechecked_on_next_request(self):
        self.service.authenticate(request(), LOCAL)
        self.directory.resolve.return_value = LocalUser("usr-101", "김철수", active=False)
        self.assertCode("ACCOUNT_DISABLED", lambda: self.service.authenticate(request(), LOCAL))
        self.directory.resolve.return_value = None
        self.assertCode("ACCOUNT_NOT_LINKED", lambda: self.service.authenticate(request(), LOCAL))
        self.assertEqual(3, self.directory.resolve.call_count)

    def test_bad_adapter_results_fail_closed(self):
        self.provider.authenticate.return_value = {"issuer": "forged", "subject": "unverified"}
        self.assertCode("AUTH_INVALID_RESULT", lambda: self.service.authenticate(request(), LOCAL))
        self.directory.resolve.assert_not_called()
        self.provider.authenticate.return_value = self.identity
        self.directory.resolve.return_value = {"user_id": "admin", "active": True}
        self.assertCode("AUTH_INVALID_RESULT", lambda: self.service.authenticate(request(), LOCAL))

    def test_sdk_and_directory_failures_are_redacted_without_local_fallback(self):
        for component, code in ((self.provider.authenticate, "AUTH_PROVIDER_UNAVAILABLE"),
                                (self.directory.resolve, "AUTH_DIRECTORY_UNAVAILABLE")):
            with self.subTest(code=code):
                component.side_effect = RuntimeError("private-token-and-sdk-detail")
                with self.assertRaises(AuthError) as error:
                    self.service.authenticate(request(LOCAL["SANITY_DASHBOARD_TOKEN"]), LOCAL)
                self.assertEqual(code, error.exception.code)
                self.assertNotIn("private-token", str(error.exception))
                component.side_effect = None

    def test_auth_mode_loads_from_private_config_and_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / ".env"
            config.write_text("SANITY_AUTH_MODE=sso\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual("sso", load_environment(config)["SANITY_AUTH_MODE"])
            with patch.dict(os.environ, {"SANITY_AUTH_MODE": "local_token"}):
                self.assertEqual("local_token", load_environment(config)["SANITY_AUTH_MODE"])

    def test_identity_and_user_fields_reject_invalid_adapter_values(self):
        for make in (lambda: VerifiedIdentity("", "subject"), lambda: VerifiedIdentity("issuer", " subject "),
                     lambda: VerifiedIdentity("issuer", "subject\n"), lambda: LocalUser("", "name"),
                     lambda: LocalUser("usr-1", ""), lambda: LocalUser("usr-1", "name", active="yes")):
            self.assertCode("AUTH_INVALID_RESULT", make)
