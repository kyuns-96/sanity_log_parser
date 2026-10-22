"""Welcome-page permission requests and administrator management for real SSO."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from common.sanity_config import load_environment
from common.sanity_common import ReviewError
from common.sanity_db import Database
from oa.sanity_auth import Actor
from oa.sanity_access import AccessStore, access_lock, administrator_id, require_mutation_request

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]


class AccessCommand(BaseModel):
    # Do not normalize opaque project, block or account IDs.
    model_config = ConfigDict(extra="forbid")


class ProjectCommand(AccessCommand):
    request_id: UUID
    project_id: Identifier
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)


class BlockCommand(AccessCommand):
    request_id: UUID
    block_id: Identifier
    name: str = Field(min_length=1, max_length=100)


class PermissionCommand(AccessCommand):
    request_id: UUID
    role: Literal["TOP", "OWNER"]
    project_id: Identifier
    block_id: Identifier | None = None
    reason: str = Field(min_length=1, max_length=2000)


class DecisionCommand(AccessCommand):
    command_id: UUID
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=0, strict=True)


class RevokeCommand(AccessCommand):
    command_id: UUID
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=0, strict=True)


@contextmanager
def _access(actor, *, write=False):
    configured = administrator_id(load_environment())
    if not isinstance(actor, Actor) or actor.auth_method != "sso":
        raise ReviewError("FORBIDDEN", "SSO is required for permission management")
    if configured is None:
        raise ReviewError("AUTHORIZATION_NOT_IMPLEMENTED", "Permission management is not configured")
    with Database("oa") as db, db.transaction():
        # REPEATABLE READ (including a server default) could retain a snapshot
        # taken before waiting for another transaction's revocation to finish.
        db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED " + ("READ WRITE" if write else "READ ONLY"))
        access_lock(db, exclusive=write)
        store = AccessStore(db, configured)
        store.require_actor(actor)
        yield store


def create_access_router(authenticated_dependency):
    router = APIRouter()

    @router.get("/api/access/catalog")
    def catalog(actor=Depends(authenticated_dependency)):
        with _access(actor) as store:
            return store.catalog()

    @router.get("/api/access/me")
    def me(actor=Depends(authenticated_dependency)):
        with _access(actor) as store:
            return store.snapshot(actor.user_id)

    @router.post("/api/access/requests")
    def request_access(body: PermissionCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        with _access(actor, write=True) as store:
            return store.request(actor, **body.model_dump())

    @router.get("/api/admin/access")
    def administrator_access(actor=Depends(authenticated_dependency)):
        with _access(actor) as store:
            return store.admin_snapshot(actor)

    @router.post("/api/admin/projects")
    def create_project(body: ProjectCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        with _access(actor, write=True) as store:
            return store.create_project(actor, **body.model_dump())

    @router.post("/api/admin/projects/{project_id}/blocks")
    def create_block(project_id: Identifier, body: BlockCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        with _access(actor, write=True) as store:
            return store.create_block(actor, project_id=project_id, **body.model_dump())

    @router.post("/api/admin/requests/{request_id}/decision")
    def decision(request_id: UUID, body: DecisionCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        with _access(actor, write=True) as store:
            return store.decide(actor, request_id=request_id, **body.model_dump())

    @router.post("/api/admin/grants/{grant_id}/revoke")
    def revoke(grant_id: UUID, body: RevokeCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        with _access(actor, write=True) as store:
            return store.revoke(actor, grant_id=grant_id, **body.model_dump())

    return router
