"""SSO self-service credential status/save/removal; never a token read API."""
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from common.sanity_clustering import check
from common.sanity_config import load_environment
from oa.sanity_ai_tokens import UserTokenStore, token_source


def _issuance_url(settings):
    """An optional operator-supplied browser link, never a model endpoint."""
    value = settings.get("SANITY_OA_AI_TOKEN_ISSUE_URL", "")
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch == "\\" for ch in value)):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port is not None and not 1 <= parsed.port <= 65535):
            return None
    except ValueError:
        return None
    return value


class TokenCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=8192)
    expected_revision: int = Field(ge=0, strict=True)


class RemoveTokenCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)


def create_ai_credentials_router(authorized_actor):
    router = APIRouter(prefix="/api/ai/credentials")

    def owner(actor=Depends(authorized_actor)):
        check(actor.auth_method == "sso" and getattr(actor, "access", None) is not None, "FORBIDDEN")
        return actor

    @router.get("")
    def status(actor=Depends(owner)):
        settings = load_environment()
        if token_source(settings) != "user":
            return {"enabled": False}
        return UserTokenStore(actor.access.db, settings).status(actor.user_id) | {"issuance_url": _issuance_url(settings)}

    @router.post("")
    def save(body: TokenCommand, actor=Depends(owner)):
        settings = load_environment()
        status = UserTokenStore(actor.access.db, settings).save(actor.user_id, body.token.get_secret_value(), body.expected_revision)
        return status | {"issuance_url": _issuance_url(settings)}

    @router.post("/remove")
    def remove(body: RemoveTokenCommand, actor=Depends(owner)):
        settings = load_environment()
        status = UserTokenStore(actor.access.db, settings).save(actor.user_id, None, body.expected_revision)
        return status | {"issuance_url": _issuance_url(settings)}

    return router
