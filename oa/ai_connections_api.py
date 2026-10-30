"""SSO administrator connection settings and owner-bound personal credentials."""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from common.sanity_clustering import check
from oa.ai_tokens_api import TokenCommand, RemoveTokenCommand
from oa.sanity_ai_connections import ConnectionStore


class ConnectionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    config: dict[str, Any]
    client_api_key: SecretStr | None = Field(default=None, max_length=8192)


class DeleteConnectionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, lt=9999999999, strict=True)


def create_ai_connections_router(authorized_actor):
    router = APIRouter()

    def owner(actor=Depends(authorized_actor)):
        check(actor.auth_method == "sso" and getattr(actor, "access", None) is not None, "FORBIDDEN")
        return actor

    @router.get("/api/admin/ai/connections")
    def admin_list(actor=Depends(owner)):
        actor.access._admin(actor)
        store = ConnectionStore(actor.access.db)
        ready = True
        try:
            store._cipher()
        except Exception as error:
            from common.sanity_common import ReviewError
            if not isinstance(error, ReviewError) or error.code != "AI_TOKEN_STORE_NOT_CONFIGURED":raise
            ready = False
        from oa.sanity_vwp_inference import Catalog
        return {"items":[store.admin_public(value) for value in store.rows()], "credentials_ready":ready,
                "vwp_ready":True, "vwp_profiles":Catalog(store.db).admin_public()}

    @router.post("/api/admin/ai/connections/{connection_id}")
    def save(connection_id: UUID, body: ConnectionCommand, actor=Depends(owner)):
        actor.access._admin(actor)
        return ConnectionStore(actor.access.db).save(actor, str(connection_id), body.config, body.expected_revision,
            body.client_api_key.get_secret_value() if body.client_api_key is not None else None)

    @router.post("/api/admin/ai/connections/{connection_id}/delete")
    def delete(connection_id: UUID, body: DeleteConnectionCommand, actor=Depends(owner)):
        actor.access._admin(actor)
        return ConnectionStore(actor.access.db).delete(actor,str(connection_id),body.expected_revision)

    @router.get("/api/ai/connections")
    def connections(actor=Depends(owner)):
        return {"items":ConnectionStore(actor.access.db).user_connections(actor.user_id, include_disabled=True)}

    def connection(actor, connection_id, *, enabled=True):
        store = ConnectionStore(actor.access.db)
        value = store.get(str(connection_id), enabled=enabled)
        return store, value

    @router.get("/api/ai/connections/{connection_id}/credentials")
    def status(connection_id: UUID, actor=Depends(owner)):
        store, value = connection(actor, connection_id, enabled=False)
        status, error = store.credential_status(value, actor.user_id)
        return {**status, "issuance_url":value["config"].get("issuance_url") or None,
                **({"credential_error":error} if error else {})}

    @router.post("/api/ai/connections/{connection_id}/credentials")
    def save_token(connection_id: UUID, body: TokenCommand, actor=Depends(owner)):
        store, value = connection(actor, connection_id)
        store.authorize_user(str(connection_id), actor.user_id)
        return {**store.tokens(value).save(actor.user_id, body.token.get_secret_value(), body.expected_revision),
                "issuance_url":value["config"].get("issuance_url") or None}

    @router.post("/api/ai/connections/{connection_id}/credentials/remove")
    def remove_token(connection_id: UUID, body: RemoveTokenCommand, actor=Depends(owner)):
        store, value = connection(actor, connection_id, enabled=False)
        return {**store.removal_tokens(value).save(actor.user_id, None, body.expected_revision),
                "issuance_url":value["config"].get("issuance_url") or None}

    return router
