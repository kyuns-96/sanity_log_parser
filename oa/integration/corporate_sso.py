"""Company-owned implementation points. Both deliberately fail until implemented.

Read SSO_INTEGRATION.md first. No demo users, shared-token bypass or unverified
header/JWT decoding belongs here. Keep secrets in company server configuration.
"""
from oa.sanity_auth import AuthError, AuthRequest, LocalUser, SSOIdentityProvider, UserDirectory, VerifiedIdentity


class CorporateIdentityProvider(SSOIdentityProvider):
    def authenticate(self, request: AuthRequest) -> VerifiedIdentity:
        # COMPANY TODO 1: use the approved SDK/middleware to validate the request's
        # credential or server session (signature, issuer, audience, expiry,
        # revocation as applicable). Return its immutable issuer + subject.
        # Invalid/missing credentials -> AuthError("UNAUTHORIZED").
        # Service outage -> AuthError("AUTH_PROVIDER_UNAVAILABLE").
        raise AuthError("AUTH_SSO_NOT_CONFIGURED")


class CorporateUserDirectory(UserDirectory):
    def resolve(self, identity: VerifiedIdentity) -> LocalUser | None:
        # COMPANY TODO 2: query the persistent (issuer, subject) -> user_id mapping.
        # Return LocalUser(user_id=..., display_name=..., active=...).
        # Unknown mapping -> None; disabled account -> active=False.
        # Storage outage -> AuthError("AUTH_DIRECTORY_UNAVAILABLE").
        # Project/block memberships belong to authorization, not SSO claims.
        raise AuthError("AUTH_DIRECTORY_NOT_CONFIGURED")
