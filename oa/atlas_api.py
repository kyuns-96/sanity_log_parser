"""Authenticated Design Atlas catalog routes; mutations require explicit intent."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from common.sanity_common import ReviewError
from common.sanity_db import Database
from oa.access_api import ProjectCommand
from oa.atlas_area_sync import (
    AreaAdapters,
    AreaPreparationError,
    resolve_project_sync_metadata,
    sync_all_area_project_blocks,
    try_auto_prepare_area_scope,
)
from oa.atlas_catalog import AtlasCatalog, fail
from oa.sanity_access import require_mutation_request
from oa.sanity_workspace import WorkspaceStore

ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")]
SourceText = Annotated[str, StringConstraints(min_length=1, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f][^\x00-\x1f\x7f]*$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Process(Input):
    name: Text
    unit_area: Decimal = Field(gt=0, max_digits=38, decimal_places=24)


class Block(Input):
    request_id: UUID
    project_id: ID
    name: Text


class VersionIdentity(Input):
    project_id: ID
    block_id: ID
    source_namespace: SourceText
    net_ver: SourceText
    revision: SourceText
    layout: Literal["PRE", "POST"]
    eco_num: Text


class Version(VersionIdentity):
    request_id: UUID
    version_id: ID
    ecos: list[Text | None] = Field(default_factory=list)
    source_pre_id: ID | None = None
    previous_version_id: ID | None = None


class Scope(Input):
    project_id: ID
    block_id: ID | None = None
    version_id: ID | None = None
    previous_version_id: ID | None = None

    @model_validator(mode="after")
    def scope_shape(self):
        if self.version_id is not None and self.block_id is None:
            raise ValueError("Version requires a block")
        return self


class BindArea(Scope):
    area_project_id: UUID
    area_block_id: UUID | None = None
    area_version_id: UUID | None = None

    @model_validator(mode="after")
    def binding_shape(self):
        if bool(self.block_id) != bool(self.area_block_id) or bool(self.version_id) != bool(self.area_version_id):
            raise ValueError("Both sides of each binding are required")
        return self


class ProjectStatus(Input):
    status: Literal["active", "completed"]


class ProjectUpdate(Input):
    name: Text
    process: Text | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""


class ProjectVisibility(Input):
    hidden: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""


class ProjectDelete(Input):
    force: bool = False
    confirm_name: Text
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""


class PorAssign(Input):
    project_id: ID
    version_id: ID
    expected_revision: int = Field(ge=0, strict=True)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


def create_atlas_router(authorized_dependency):
    router = APIRouter(prefix="/api/atlas")

    @contextmanager
    def context(actor, *, readonly=False):
        with Database("oa") as db, db.transaction(readonly=readonly):
            yield AtlasCatalog(db, actor)

    # Native admin writes share the grant-lock transaction: commit before response.
    def mutation(request: Request, actor=Depends(authorized_dependency, scope="function")):
        require_mutation_request(request)
        return actor

    def area_enabled(request):
        if not getattr(request.app.state, "area_power_enabled", False):
            fail("AREA_POWER_UNAVAILABLE")

    def area_error(exc):
        from common.area_power.errors import ConfigurationError, DomainError
        if isinstance(exc, ConfigurationError):
            return JSONResponse({"code": "AREA_POWER_UNAVAILABLE", "status": "NOT_PREPARED"}, 503)
        if isinstance(exc, DomainError):
            return JSONResponse({"code": exc.code.upper(), "status": "NOT_PREPARED"}, exc.status_code)
        raise exc

    @router.get("/catalog")
    def catalog(
        request: Request,
        include_hidden: bool = Query(default=False),
        actor=Depends(authorized_dependency, scope="function"),
    ):
        with context(actor, readonly=True) as store:
            result = {"product": "Design Atlas", "local_mode": store.local, "can_manage_catalog": store.can_manage,
                      "area_enabled": bool(getattr(request.app.state, "area_power_enabled", False)),
                      "projects": store.catalog(include_hidden=include_hidden),
                      "processes": store.ws.processes()}
            result["area_status"] = "disabled"
            if result["area_enabled"]:
                try:
                    unbound = AreaAdapters(store).project_links(result["projects"])
                    result["area_status"] = "ready"
                    if unbound is not None:
                        result.update(unbound)
                except ReviewError:
                    raise
                except Exception as exc:
                    from common.area_power.errors import ConfigurationError, DomainError
                    if not isinstance(exc, (ConfigurationError, DomainError)):
                        raise
                    result.update(area_status="unavailable", area_error="AREA_POWER_UNAVAILABLE")
            if result["area_enabled"]:
                from common.area_power.jsonutil import public
                return JSONResponse(public(result))
            return result
    @router.get("/projects/{project_id}/version-inventory")
    def version_inventory(
        project_id: ID,
        request: Request,
        block_id: ID | None = Query(default=None),
        actor=Depends(authorized_dependency, scope="function"),
    ):
        from oa.version_inventory import build_version_inventory
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with context(actor, readonly=True) as store:
            return build_version_inventory(store, project_id, block_id=block_id, area_enabled=area_on)

    @router.get("/reports")
    def reports(project_id: ID, block_id: ID, version_id: ID,
                offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
                actor=Depends(authorized_dependency, scope="function")):
        with context(actor, readonly=True) as store:
            project, block, version = store.scope(project_id, block_id, version_id)
            params = (project["record_id"], block["record_id"], version["record_id"])
            predicate = "record_type='WS_REPORT' AND workspace_project_ref=%s AND workspace_block_ref=%s AND workspace_version_ref=%s"
            total = store.db.execute("SELECT count(*) AS n FROM {oa} WHERE " + predicate, params).fetchone()["n"]
            rows = store.db.execute("SELECT payload FROM {oa} WHERE " + predicate +
                                    " ORDER BY workspace_key LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()
            return {"items": [r["payload"] for r in rows], "total": total, "offset": offset, "limit": limit}


    @router.post("/processes")
    def register_process(body: Process, actor=Depends(mutation)):
        with context(actor) as store:
            if not store.can_manage:
                fail("FORBIDDEN")
            return store.ws.register_process(body.name, body.unit_area)

    @router.post("/projects")
    def project(body: ProjectCommand, request: Request, actor=Depends(mutation)):
        with context(actor) as store:
            result = store.create_project(body)
            ws = store.ws if store.local else WorkspaceStore(store.access.db)
            _, process = resolve_project_sync_metadata(ws, result["project_id"], explicit_process=body.process)
        return try_auto_prepare_area_scope(
            request,
            result,
            actor_id=actor.user_id,
            project_id=result["project_id"],
            project_name=body.name.strip(),
            process=process,
        )

    @router.post("/blocks")
    def block(body: Block, request: Request, actor=Depends(mutation)):
        with context(actor) as store:
            result = store.create_block(body)
            ws = store.ws if store.local else WorkspaceStore(store.access.db)
            project_name, process = resolve_project_sync_metadata(ws, body.project_id)
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

    @router.post("/version-identity")
    def identity(body: VersionIdentity, actor=Depends(mutation)):
        with context(actor, readonly=True) as store:
            store.scope(body.project_id, body.block_id, write=True)
            return {"version_id": store.identity(body)}

    @router.post("/versions")
    def version(body: Version, request: Request, actor=Depends(mutation)):
        with context(actor) as store:
            result = store.create_version(body)
        if getattr(request.app.state, "area_power_enabled", False):
            from common.area_power.errors import ConfigurationError, DomainError
            try:
                with context(actor) as store:
                    prepared = AreaAdapters(store).prepare(
                        Scope(project_id=body.project_id, block_id=body.block_id, version_id=body.version_id)
                    )
                result = {
                    **result,
                    "area_prepared": True,
                    "area_project_id": prepared["area_project_id"],
                    "area_block_id": prepared["area_block_id"],
                    "area_version_id": prepared["area_version_id"],
                }
            except AreaPreparationError:
                result = {**result, "area_prepared": False, "area_error": "AREA_BINDING_NOT_RECORDED"}
            except ReviewError as exc:
                result = {**result, "area_prepared": False, "area_error": exc.code}
            except (ConfigurationError, DomainError) as exc:
                code = "AREA_POWER_UNAVAILABLE" if isinstance(exc, ConfigurationError) else exc.code.upper()
                result = {**result, "area_prepared": False, "area_error": code}
        return result

    def prepare_area(body, request, actor, *, bind=False):
        area_enabled(request)
        result = None
        try:
            with context(actor) as store:
                if "previous_version_id" in body.model_fields_set:
                    store.declare_lineage(body)
                result = AreaAdapters(store).prepare(body, bind=bind)
            return result
        except AreaPreparationError as exc:
            return JSONResponse({**exc.result, "status": "PARTIAL", "code": "AREA_BINDING_NOT_RECORDED"}, 409)
        except Exception as exc:
            if result is not None:
                return JSONResponse({**result, "status": "PARTIAL", "code": "AREA_BINDING_NOT_RECORDED"}, 409)
            if isinstance(exc, ReviewError):
                raise
            return area_error(exc)

    @router.post("/projects/{project_id}/status")
    def project_status(project_id: ID, body: ProjectStatus, actor=Depends(mutation)):
        with context(actor) as store:
            return store.set_project_status(project_id, body.status)

    @router.post("/projects/{project_id}/update")
    def update_project_route(project_id: ID, body: ProjectUpdate, request: Request, actor=Depends(mutation)):
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with context(actor) as store:
            return store.update_project(
                project_id, name=body.name, description=body.description, process=body.process, area_enabled=area_on,
            )

    @router.post("/projects/{project_id}/visibility")
    def project_visibility(project_id: ID, body: ProjectVisibility, actor=Depends(mutation)):
        with context(actor) as store:
            return store.set_project_hidden(project_id, hidden=body.hidden, reason=body.reason)

    @router.get("/projects/{project_id}/delete-preview")
    def project_delete_preview_route(project_id: ID, request: Request, actor=Depends(authorized_dependency, scope="function")):
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with context(actor, readonly=True) as store:
            return store.project_delete_preview(project_id, area_enabled=area_on)

    @router.post("/projects/{project_id}/delete")
    def delete_project_route(project_id: ID, body: ProjectDelete, request: Request, actor=Depends(mutation)):
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with context(actor) as store:
            return store.delete_project(
                project_id, force=body.force, confirm_name=body.confirm_name, reason=body.reason, area_enabled=area_on,
            )

    @router.get("/projects/{project_id}/blocks/{block_id}/delete-preview")
    def block_delete_preview_route(project_id: ID, block_id: ID, request: Request, actor=Depends(authorized_dependency, scope="function")):
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with context(actor, readonly=True) as store:
            return store.block_delete_preview(project_id, block_id, area_enabled=area_on)

    @router.post("/projects/{project_id}/blocks/{block_id}/delete")
    def delete_block_route(project_id: ID, block_id: ID, body: ProjectDelete, request: Request, actor=Depends(mutation)):
        area_on = bool(getattr(request.app.state, "area_power_enabled", False))
        with context(actor) as store:
            return store.delete_block(
                project_id, block_id, force=body.force, confirm_name=body.confirm_name, reason=body.reason, area_enabled=area_on,
            )

    @router.post("/blocks/{block_id}/por")
    def block_por(block_id: ID, body: PorAssign, actor=Depends(mutation)):
        with context(actor) as store:
            return store.assign_por(
                body.project_id, block_id, body.version_id,
                body.expected_revision, body.request_id, body.reason,
            )

    @router.post("/area-scope/sync")
    def sync_area_scopes(request: Request, actor=Depends(mutation)):
        area_enabled(request)
        with context(actor, readonly=True) as store:
            if not store.can_manage:
                fail("FORBIDDEN")
        try:
            result = sync_all_area_project_blocks(actor_id=actor.user_id)
            return {"status": "SYNCED", **result}
        except ReviewError:
            raise
        except Exception as exc:
            return area_error(exc)

    @router.post("/area-scope")
    def prepare(body: Scope, request: Request, actor=Depends(mutation)):
        return prepare_area(body, request, actor)

    @router.post("/bind-area")
    def bind(body: BindArea, request: Request, actor=Depends(mutation)):
        return prepare_area(body, request, actor, bind=True)

    return router
