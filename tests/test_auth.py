"""SSO adapter contract tests. All identities and credentials are test doubles."""
import unittest
from unittest.mock import Mock

from oa.sanity_auth import AuthError, AuthRequest, AuthenticationService, LocalUser, SSOIdentityProvider, UserDirectory, VerifiedIdentity

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


    def test_sso_revalidates_identity_and_redacts_provider_and_directory_failures(self):
        self.service.authenticate(request(), LOCAL)
        self.directory.reset_mock()
        self.provider.authenticate.side_effect = AuthError("UNAUTHORIZED")
        self.assertCode("UNAUTHORIZED", lambda: self.service.authenticate(request(LOCAL["SANITY_DASHBOARD_TOKEN"]), LOCAL))
        self.directory.resolve.assert_not_called()
        self.assertEqual(2, self.provider.authenticate.call_count)

        self.provider.authenticate.side_effect = None
        for component, code in ((self.provider.authenticate, "AUTH_PROVIDER_UNAVAILABLE"),
                                (self.directory.resolve, "AUTH_DIRECTORY_UNAVAILABLE")):
            with self.subTest(code=code):
                component.side_effect = RuntimeError("private-token-and-sdk-detail")
                with self.assertRaises(AuthError) as error:
                    self.service.authenticate(request(LOCAL["SANITY_DASHBOARD_TOKEN"]), LOCAL)
                self.assertEqual(code, error.exception.code)
                self.assertNotIn("private-token", str(error.exception))
                component.side_effect = None
