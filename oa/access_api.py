"""Welcome-page permission requests and administrator management for real SSO."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from common.sanity_config import load_environment
from common.sanity_common import ReviewError
from common.sanity_db import Database
from oa.atlas_area_sync import resolve_project_sync_metadata, try_auto_prepare_area_scope
from oa.sanity_auth import Actor
from oa.sanity_workspace import WorkspaceStore
from oa.sanity_access import AccessStore, access_lock, administrator_id, require_mutation_request

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")]


class AccessCommand(BaseModel):
    # Do not normalize opaque project, block or account IDs.
    model_config = ConfigDict(extra="forbid")


class ProjectCommand(AccessCommand):
    request_id: UUID
    name: Name
    process: Name | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""


class ProjectUpdateCommand(AccessCommand):
    name: Name
    process: Name | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""


class ProjectVisibilityCommand(AccessCommand):
    hidden: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""


class ProjectDeleteCommand(AccessCommand):
    force: bool = False
    confirm_name: Name
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""


class BlockCommand(AccessCommand):
    request_id: UUID
    name: Name


class PermissionCommand(AccessCommand):
    request_id: UUID
    role: Literal["MEMBER", "TOP", "OWNER"]
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
    def catalog(include_hidden: bool = False, actor=Depends(authenticated_dependency)):
        with _access(actor) as store:
            show_hidden = bool(include_hidden and store.is_admin(actor.user_id))
            ws = WorkspaceStore(store.db)
            data = store.catalog(include_hidden=show_hidden)
            for p in data["projects"]:
                if "process" not in p:
                    p["process"] = ws.initial_process(p["id"])
            return {**data, "processes": ws.processes()}

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
            result = store.create_project(actor, **body.model_dump())
            _, process = resolve_project_sync_metadata(WorkspaceStore(store.db), result["project_id"], explicit_process=body.process)
        return try_auto_prepare_area_scope(
            request,
            result,
            actor_id=actor.user_id,
            project_id=result["project_id"],
            project_name=body.name.strip(),
            process=process,
        )

    @router.post("/api/admin/projects/{project_id}/update")
    def update_project_admin(project_id: Identifier, body: ProjectUpdateCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        from oa.project_lifecycle import update_project
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with _access(actor, write=True) as store:
            store.require_admin(actor)
            return update_project(
                store.db, actor.user_id, project_id,
                name=body.name, description=body.description, process=body.process, area_enabled=area_on,
            )

    @router.post("/api/admin/projects/{project_id}/visibility")
    def project_visibility_admin(project_id: Identifier, body: ProjectVisibilityCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        from oa.project_lifecycle import set_project_hidden
        with _access(actor, write=True) as store:
            store.require_admin(actor)
            return set_project_hidden(store.db, actor.user_id, project_id, hidden=body.hidden, reason=body.reason)

    @router.get("/api/admin/projects/{project_id}/delete-preview")
    def project_delete_preview_admin(project_id: Identifier, request: Request, actor=Depends(authenticated_dependency)):
        from oa.project_lifecycle import project_delete_preview
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with _access(actor) as store:
            store.require_admin(actor)
            return project_delete_preview(store.db, project_id, area_enabled=area_on)

    @router.post("/api/admin/projects/{project_id}/delete")
    def delete_project_admin(project_id: Identifier, body: ProjectDeleteCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        from oa.project_lifecycle import delete_project
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with _access(actor, write=True) as store:
            store.require_admin(actor)
            return delete_project(
                store.db, actor, project_id,
                force=body.force, confirm_name=body.confirm_name, reason=body.reason,
                access_store=store, area_enabled=area_on,
            )

    @router.post("/api/admin/projects/{project_id}/blocks")
    def create_block(project_id: Identifier, body: BlockCommand, request: Request, actor=Depends(authenticated_dependency)):
        require_mutation_request(request)
        with _access(actor, write=True) as store:
            result = store.create_block(actor, project_id=project_id, **body.model_dump())
            project_name, process = resolve_project_sync_metadata(WorkspaceStore(store.db), project_id)
        return try_auto_prepare_area_scope(
            request,
            result,
            actor_id=actor.user_id,
            project_id=result["project_id"],
            project_name=project_name,
            process=process,
            block_id=result["block_id"],
            block_name=body.name.strip(),
        )

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
