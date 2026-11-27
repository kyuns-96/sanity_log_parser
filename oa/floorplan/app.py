"""OA Floorplan Review FastAPI router mounted under /api/floorplan."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from common.area_power.config import Settings
from common.area_power.db import Database as AreaDatabase
from common.area_power.errors import DomainError
from common.area_power.jsonutil import public
from common.floorplan import ConfluenceNotConfigured, ConfluencePublishError
from common.sanity_common import ReviewError
from common.sanity_db import Database as HostDatabase
from oa.area_power.access import AreaAccess
from oa.area_power.app import SafeAreaRoute
from oa.area_power.service import Service
from oa.confluence_pat_store import ConfluencePatStore
from oa.sanity_access import require_mutation_request
from oa.sanity_workspace import WorkspaceStore
from .service import (
    ConfluencePublishOptionsInput,
    SaveChecklistTemplateInput,
    SaveConfluenceConfigInput,
    SaveFloorplanReviewInput,
    SavePlacementProposalInput,
    UploadWebAttachmentInput,
    execute_confluence_publish,
    export_proposal_script,
    get_version_detail,
    get_workspace_overview,
    preview_confluence_publish,
    read_artifact_binary,
    save_checklist_template,
    save_confluence_config,
    save_proposal,
    save_review,
    upload_web_attachment,
)


class PatSaveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=4096)
    expected_revision: int = Field(ge=0)


class PatRemoveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


def _host_unit_area(identity: object, process_name: str | None) -> float | None:
    if not process_name:
        return None
    try:
        store = getattr(identity, "access", None)
        if store is not None:
            procs = WorkspaceStore(store.db).list_processes()
        else:
            with HostDatabase("oa") as host:
                procs = WorkspaceStore(host).list_processes()
        hit = next((p for p in procs if p.get("name") == process_name), None)
        return float(hit["unit_area"]) if hit and hit.get("unit_area") else None
    except Exception:
        return None


def _pat_store_context(identity: object):
    store = getattr(identity, "access", None)
    local_mode = getattr(identity, "auth_method", None) == "local_token"
    if store is not None:
        return ConfluencePatStore(store.db, local_mode=local_mode), None
    host = HostDatabase("oa")
    host.__enter__()
    tx = host.transaction()
    tx.__enter__()
    return ConfluencePatStore(host, local_mode=local_mode), (tx, host)


def _close_pat_context(ctx) -> None:
    if ctx is not None:
        tx, host = ctx
        try:
            tx.__exit__(None, None, None)
        finally:
            host.__exit__(None, None, None)


def create_floorplan_router(authorized_dependency):
    router = APIRouter(prefix="/api/floorplan", route_class=SafeAreaRoute)

    def json_resp(value, status_code: int = 200) -> JSONResponse:
        return JSONResponse(public(value), status_code=status_code)

    def context(request: Request, identity=Depends(authorized_dependency, scope="function")):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            require_mutation_request(request)
        perm = AreaAccess(identity)
        return Service(AreaDatabase(Settings.from_env()), perm), identity

    @router.get("/pat")
    def pat_status_route(ctx=Depends(context)):
        _, identity = ctx
        pat_store, handle = _pat_store_context(identity)
        try:
            return json_resp(pat_store.status(identity.user_id))
        finally:
            _close_pat_context(handle)

    @router.post("/pat")
    def pat_save_route(body: PatSaveInput, ctx=Depends(context)):
        _, identity = ctx
        pat_store, handle = _pat_store_context(identity)
        try:
            return json_resp(pat_store.save(identity.user_id, body.token, body.expected_revision))
        finally:
            _close_pat_context(handle)

    @router.post("/pat/remove")
    def pat_remove_route(body: PatRemoveInput, ctx=Depends(context)):
        _, identity = ctx
        pat_store, handle = _pat_store_context(identity)
        try:
            return json_resp(pat_store.remove(identity.user_id, body.expected_revision))
        finally:
            _close_pat_context(handle)

    @router.get("/projects/{project_id}/workspace")
    def workspace_route(project_id: UUID, block_id: UUID | None = None, ctx=Depends(context)):
        svc, identity = ctx
        pat_info = {"registered": False, "revision": 0, "updated_at": None}
        try:
            pat_store, handle = _pat_store_context(identity)
            try:
                pat_info = pat_store.status(identity.user_id)
            finally:
                _close_pat_context(handle)
        except ReviewError:
            pass
        return json_resp(get_workspace_overview(svc, project_id, block_id=block_id, pat_status=pat_info))

    @router.get("/versions/{fp_version_id}")
    def version_detail_route(
        fp_version_id: UUID,
        compare_fp_version_id: UUID | None = None,
        area_upload_id: UUID | None = None,
        process: str | None = None,
        ctx=Depends(context),
    ):
        svc, identity = ctx
        u_area = _host_unit_area(identity, process)
        return json_resp(
            get_version_detail(
                svc,
                fp_version_id,
                compare_fp_version_id=compare_fp_version_id,
                area_upload_id=area_upload_id,
                unit_area=u_area,
            )
        )

    @router.get("/artifacts/{artifact_id}/content")
    def artifact_content_route(request: Request, artifact_id: UUID, ctx=Depends(context)):
        svc, _ = ctx
        art = read_artifact_binary(svc, artifact_id, thumbnail=False)
        inm = request.headers.get("if-none-match", "").strip('"')
        headers = {
            "ETag": f'"{art["etag"]}"',
            "Cache-Control": "private, max-age=31536000, immutable",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        }
        if inm and inm == art["etag"]:
            return Response(status_code=304, headers=headers)
        return Response(content=art["bytes"], media_type=art["mime_type"], headers=headers)

    @router.get("/artifacts/{artifact_id}/thumbnail")
    def artifact_thumb_route(artifact_id: UUID, ctx=Depends(context)):
        svc, _ = ctx
        art = read_artifact_binary(svc, artifact_id, thumbnail=True)
        return Response(
            content=art["bytes"],
            media_type=art["mime_type"],
            headers={"ETag": f'"{art["etag"]}"', "Cache-Control": "private, max-age=31536000, immutable"},
        )

    @router.post("/projects/{project_id}/checklist-template")
    def save_checklist_route(project_id: UUID, body: SaveChecklistTemplateInput, ctx=Depends(context)):
        svc, identity = ctx
        return json_resp(save_checklist_template(svc, project_id, body, identity.user_id))

    @router.post("/projects/{project_id}/confluence-config")
    def save_conf_cfg_route(project_id: UUID, body: SaveConfluenceConfigInput, ctx=Depends(context)):
        svc, identity = ctx
        return json_resp(save_confluence_config(svc, project_id, body, identity.user_id))

    @router.post("/versions/{fp_version_id}/review")
    def save_review_route(fp_version_id: UUID, body: SaveFloorplanReviewInput, ctx=Depends(context)):
        svc, identity = ctx
        return json_resp(save_review(svc, fp_version_id, body, identity.user_id))

    @router.post("/versions/{fp_version_id}/attachments", status_code=201)
    def upload_attachment_route(fp_version_id: UUID, body: UploadWebAttachmentInput, ctx=Depends(context)):
        svc, identity = ctx
        return json_resp(upload_web_attachment(svc, fp_version_id, body, identity.user_id), status_code=201)

    @router.post("/versions/{fp_version_id}/proposals")
    def save_proposal_route(fp_version_id: UUID, body: SavePlacementProposalInput, ctx=Depends(context)):
        svc, identity = ctx
        return json_resp(save_proposal(svc, fp_version_id, body, identity.user_id))

    @router.get("/proposals/{proposal_id}/export")
    def export_proposal_route(
        proposal_id: UUID,
        fmt: Literal["innovus", "icc2", "csv"] = Query(default="innovus"),
        only_changed: bool = Query(default=False),
        ctx=Depends(context),
    ):
        svc, _ = ctx
        text_body, fname, mime = export_proposal_script(svc, proposal_id, fmt=fmt, only_changed=only_changed)
        return Response(
            content=text_body.encode("utf-8"),
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    @router.post("/versions/{fp_version_id}/confluence-preview")
    def confluence_preview_route(fp_version_id: UUID, body: ConfluencePublishOptionsInput, ctx=Depends(context)):
        svc, identity = ctx
        return json_resp(preview_confluence_publish(svc, fp_version_id, body, identity.reviewer))

    @router.post("/versions/{fp_version_id}/confluence-publish")
    def confluence_publish_route(fp_version_id: UUID, body: ConfluencePublishOptionsInput, ctx=Depends(context)):
        svc, identity = ctx
        pat_store, handle = _pat_store_context(identity)
        try:
            pat = pat_store.get_pat(identity.user_id)
        finally:
            _close_pat_context(handle)
        try:
            return json_resp(
                execute_confluence_publish(
                    svc, fp_version_id, body,
                    personal_pat=pat, actor_user_id=identity.user_id, reviewer=identity.reviewer,
                )
            )
        except ConfluenceNotConfigured as exc:
            raise DomainError(str(exc), code="confluence_not_configured", status_code=503) from None
        except ConfluencePublishError as exc:
            raise DomainError(str(exc), code="confluence_publish_failed", status_code=502) from None

    return router
