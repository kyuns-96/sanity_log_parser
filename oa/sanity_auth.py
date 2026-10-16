"""Dashboard authentication contracts; no company SDK, database schema or SSO protocol.

Implement SSOIdentityProvider and UserDirectory, then inject them into create_app.
See oa/SSO_INTEGRATION.md. Authentication does not grant business access.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re
import secrets
from types import MappingProxyType
from typing import Literal, Mapping

from common.sanity_common import ReviewError

AuthMode = Literal["local_token", "sso"]
AUTH_CODES = frozenset({
    "UNAUTHORIZED", "ACCOUNT_NOT_LINKED", "ACCOUNT_DISABLED",
    "AUTH_CONFIG", "AUTH_SSO_NOT_CONFIGURED", "AUTH_DIRECTORY_NOT_CONFIGURED",
    "AUTH_PROVIDER_UNAVAILABLE", "AUTH_DIRECTORY_UNAVAILABLE", "AUTH_INVALID_RESULT",
    "AUTHORIZATION_NOT_IMPLEMENTED",
})


class AuthError(ReviewError):
    """A fixed public code only. Never include a token, claim or SDK error message."""
    def __init__(self, code: str):
        if code not in AUTH_CODES:
            raise ValueError("Unknown authentication error code")
        super().__init__(code, "Authentication or identity mapping failed")


def _valid_text(value: object, limit: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit and value == value.strip()
            and re.search(r"[\x00-\x1f\x7f]", value) is None)


@dataclass(frozen=True)
class AuthRequest:
    """UNTRUSTED input. Headers are lower-case. No query/body or forwarded-user trust.

    Adapters may validate a Bearer credential or a server-managed session cookie.
    The mappings are immutable and excluded from repr to avoid accidental logging.
    """
    method: str
    path: str
    headers: Mapping[str, str] = field(repr=False)
    cookies: Mapping[str, str] = field(repr=False)
    client_host: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "headers", MappingProxyType({key.lower(): value for key, value in self.headers.items()}))
        object.__setattr__(self, "cookies", MappingProxyType(dict(self.cookies)))


@dataclass(frozen=True)
class VerifiedIdentity:
    """Return ONLY after authenticating the credential/session with the company IdP.

    issuer + subject is the external identity key, not a name/email/role claim.
    Keep both opaque and unchanged; do not decode a JWT without verification.
    """
    issuer: str
    subject: str

    def __post_init__(self):
        if not _valid_text(self.issuer, 512) or not _valid_text(self.subject, 512):
            raise AuthError("AUTH_INVALID_RESULT")


@dataclass(frozen=True)
class LocalUser:
    """Persisted internal account. Resolve by (issuer, subject), never by display name."""
    user_id: str
    display_name: str
    active: bool = True

    def __post_init__(self):
        if (not isinstance(self.user_id, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", self.user_id) is None
                or not _valid_text(self.display_name, 100) or type(self.active) is not bool):
            raise AuthError("AUTH_INVALID_RESULT")


@dataclass(frozen=True)
class Actor:
    """Application identity; deliberately contains no SSO secrets or authorization roles."""
    user_id: str
    reviewer: str
    auth_method: AuthMode


class SSOIdentityProvider(ABC):
    @abstractmethod
    def authenticate(self, request: AuthRequest) -> VerifiedIdentity:
        """Validate every request/session; reject invalid/expired credentials with UNAUTHORIZED.

        Raise AuthError(AUTH_PROVIDER_UNAVAILABLE) on IdP/network failure. Do not
        return roles, assume X-User headers are trusted, or fall back to local auth.
        Synchronous contract: FastAPI runs the dependency in its worker threadpool.
        """
        raise NotImplementedError


class UserDirectory(ABC):
    @abstractmethod
    def resolve(self, identity: VerifiedIdentity) -> LocalUser | None:
        """Look up a durable internal account by issuer AND subject on every request.

        None means not linked; active=False means disabled. No automatic privileged
        provisioning. Raise AuthError(AUTH_DIRECTORY_UNAVAILABLE) on storage failure.
        """
        raise NotImplementedError


class AuthenticationService:
    """Select one explicit mode; an SSO failure never retries the local token path."""
    def __init__(self, *, mode: AuthMode | None = None,
                 identity_provider: SSOIdentityProvider | None = None,
                 user_directory: UserDirectory | None = None):
        if mode is not None and mode not in ("local_token", "sso"):
            raise AuthError("AUTH_CONFIG")
        # Supplying company adapters must not silently leave local-token auth enabled.
        if (identity_provider is not None or user_directory is not None) and mode != "sso":
            raise AuthError("AUTH_CONFIG")
        self.mode = mode
        self.identity_provider = identity_provider
        self.user_directory = user_directory

    def authenticate(self, request: AuthRequest, settings: Mapping[str, str | None]) -> Actor:
        mode = self.mode if self.mode is not None else settings.get("SANITY_AUTH_MODE", "local_token")
        if mode == "local_token":
            return self._local(request, settings)
        if mode != "sso":
            raise AuthError("AUTH_CONFIG")
        if self.identity_provider is None:
            raise AuthError("AUTH_SSO_NOT_CONFIGURED")
        if self.user_directory is None:
            raise AuthError("AUTH_DIRECTORY_NOT_CONFIGURED")
        try:
            identity = self.identity_provider.authenticate(request)
        except AuthError:
            raise
        except Exception:
            raise AuthError("AUTH_PROVIDER_UNAVAILABLE") from None
        if not isinstance(identity, VerifiedIdentity):
            raise AuthError("AUTH_INVALID_RESULT")
        try:
            user = self.user_directory.resolve(identity)
        except AuthError:
            raise
        except Exception:
            raise AuthError("AUTH_DIRECTORY_UNAVAILABLE") from None
        if user is None:
            raise AuthError("ACCOUNT_NOT_LINKED")
        if not isinstance(user, LocalUser):
            raise AuthError("AUTH_INVALID_RESULT")
        if not user.active:
            raise AuthError("ACCOUNT_DISABLED")
        return Actor(user_id=user.user_id, reviewer=user.display_name, auth_method="sso")

    @staticmethod
    def _local(request: AuthRequest, settings: Mapping[str, str | None]) -> Actor:
        token = settings.get("SANITY_DASHBOARD_TOKEN") or ""
        reviewer = (settings.get("SANITY_DASHBOARD_REVIEWER") or "").strip()
        if len(token) < 32 or not _valid_text(reviewer, 100):
            raise ReviewError("DASHBOARD_NOT_CONFIGURED", "Configure a token and a fixed reviewer")
        authorization = request.headers.get("authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            raise AuthError("UNAUTHORIZED")
        return Actor(user_id="local-reviewer", reviewer=reviewer, auth_method="local_token")


def require_business_access(actor: Actor) -> Actor:
    """Legacy routes have no project/block filtering yet. Keep SSO identity-only.

    Replace this boundary with resource-level RBAC AND scoped store queries as
    described in SSO_INTEGRATION.md; merely allowing SSO actors here leaks data.
    """
    if actor.auth_method != "local_token":
        raise AuthError("AUTHORIZATION_NOT_IMPLEMENTED")
    return actor
