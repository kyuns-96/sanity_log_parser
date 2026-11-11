"""Authenticated Design Atlas catalog routes; mutations require explicit intent."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from common.sanity_common import ReviewError
from common.sanity_db import Database
from oa.atlas_catalog import AtlasCatalog, AreaAdapters, AreaPreparationError, fail
from oa.sanity_access import require_mutation_request

ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")]
SourceText = Annotated[str, StringConstraints(min_length=1, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f][^\x00-\x1f\x7f]*$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Project(Input):
    request_id: UUID
    project_id: ID
    name: Text
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000, pattern=r"^[^\x00-\x1f\x7f]*$")] = ""


class Block(Input):
    request_id: UUID
    project_id: ID
    block_id: ID
    name: Text


class VersionIdentity(Input):
    project_id: ID
    block_id: ID
    source_namespace: SourceText
    net_ver: SourceText
    revision: SourceText
    layout: Literal["PRE", "POST"]


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
        from area_power.common.errors import ConfigurationError, DomainError
        if isinstance(exc, ConfigurationError):
            return JSONResponse({"code": "AREA_POWER_UNAVAILABLE", "status": "NOT_PREPARED"}, 503)
        if isinstance(exc, DomainError):
            return JSONResponse({"code": exc.code.upper(), "status": "NOT_PREPARED"}, exc.status_code)
        raise exc

    @router.get("/catalog")
    def catalog(request: Request, actor=Depends(authorized_dependency, scope="function")):
        with context(actor, readonly=True) as store:
            result = {"product": "Design Atlas", "local_mode": store.local, "can_manage_catalog": store.can_manage,
                      "area_enabled": bool(getattr(request.app.state, "area_power_enabled", False)), "projects": store.catalog()}
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
                    from area_power.common.errors import ConfigurationError, DomainError
                    if not isinstance(exc, (ConfigurationError, DomainError)):
                        raise
                    result.update(area_status="unavailable", area_error="AREA_POWER_UNAVAILABLE")
            if result["area_enabled"]:
                from area_power.common.jsonutil import public
                return JSONResponse(public(result))
            return result
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


    @router.post("/projects")
    def project(body: Project, actor=Depends(mutation)):
        with context(actor) as store:
            return store.create_project(body)

    @router.post("/blocks")
    def block(body: Block, actor=Depends(mutation)):
        with context(actor) as store:
            return store.create_block(body)

    @router.post("/version-identity")
    def identity(body: VersionIdentity, actor=Depends(mutation)):
        with context(actor, readonly=True) as store:
            store.scope(body.project_id, body.block_id, write=True)
            return {"version_id": store.identity(body)}

    @router.post("/versions")
    def version(body: Version, actor=Depends(mutation)):
        with context(actor) as store:
            return store.create_version(body)

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

    @router.post("/area-scope")
    def prepare(body: Scope, request: Request, actor=Depends(mutation)):
        return prepare_area(body, request, actor)

    @router.post("/bind-area")
    def bind(body: BindArea, request: Request, actor=Depends(mutation)):
        return prepare_area(body, request, actor, bind=True)

    return router
