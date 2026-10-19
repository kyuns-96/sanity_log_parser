"""Isolated, persistent synthetic workspace. Never a fallback for corporate SSO."""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
import secrets
import threading
import time
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware

from common.sanity_common import ReviewError
from common.sanity_clustering import check
from common.sanity_db import Database
from oa.sanity_workspace import WorkspaceStore
from oa.sanity_workspace_review import WorkspaceReviewStore
from oa.sanity_workspace_clustering import WorkspaceClusteringStore, MAX_RECORDS, MAX_GROUPS
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from oa.sanity_workspace_pipeline import WorkspacePipelineStore, compact_review, event_brief

COOKIE = "sanity_workspace_session"
API = "/workspace/api"
ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[str, Field(pattern=r"^user[0-9]{2}$")]
    password: Annotated[str, Field(min_length=1, max_length=128)]


class PorChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: ID
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    request_id: UUID
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class ReviewChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conclusion: Literal["PASS", "FAIL", "DEFER"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    criteria: Annotated[str, Field(max_length=2000)] = ""
    conditions: Annotated[dict[str, Annotated[str, Field(strict=True, max_length=500)]], Field(max_length=12)] = Field(default_factory=dict)
    reusable: StrictBool = False
    compatible_versions: StrictBool = False
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    request_id: UUID
    proposal_id: UUID | None = None


class ReviewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    request_id: UUID
    mode: Literal["CRITERIA", "CORPORATE"] = "CRITERIA"


class PipelineGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: ID
    label: Annotated[str, Field(min_length=1, max_length=120)]
    record_ids: Annotated[list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]], Field(min_length=1, max_length=MAX_RECORDS)]


class ClusterProposal(ReviewProposal):
    identity_fields: list[Literal["object", "check_point", "mode", "code", "severity", "observed", "expected", "unit"]] | None = None


class ClusterChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["EDIT", "CONFIRM", "WITHDRAW"]
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    request_id: UUID
    reason: Annotated[str, Field(max_length=2000)] = ""
    groups: Annotated[list[PipelineGroup], Field(min_length=1, max_length=MAX_GROUPS)] | None = None
    reusable: StrictBool = False
    compatible_versions: StrictBool = False
    criteria: Annotated[str, Field(max_length=2000)] = ""


class GroupReviewChange(ReviewChange):
    expected_cluster_revision: Annotated[StrictInt, Field(ge=1)]


class GroupReviewProposal(ReviewProposal):
    expected_cluster_revision: Annotated[StrictInt, Field(ge=1)]


class ClusterOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["MOVE", "MERGE", "RENAME"]
    record_ids: Annotated[list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]], Field(max_length=MAX_RECORDS)] = Field(default_factory=list)
    group_ids: Annotated[list[ID], Field(max_length=MAX_GROUPS)] = Field(default_factory=list)
    destination_id: ID | None = None
    new_group_id: ID | None = None
    label: Annotated[str, Field(max_length=120)] = ""


class ClusterEdit(ReviewProposal):
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    operation: ClusterOperation


class ClusterTaskStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    expected_chunk: Annotated[StrictInt, Field(ge=0)]
    action: Literal["ADVANCE", "RESUME", "CANCEL"] = "ADVANCE"


class RationaleReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: UUID
    group_id: ID
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class RationaleContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_ids: Annotated[list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]], Field(min_length=1, max_length=20)]
    reference_groups: Annotated[list[RationaleReference], Field(max_length=20)] = Field(default_factory=list)


class RationaleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    request_id: UUID
    action: Literal["SAVE", "ACCEPT"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    content: RationaleContent | None = None
    reviewed: StrictBool = False


class CodexProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    request_id: UUID


def create_app(*, origins, static_dir=None, codex_mock_enabled=False, codex_advisor_factory=None):
    origins = frozenset(origins)
    if not origins or any(urlsplit(origin).scheme not in ("http", "https") or urlsplit(origin).path for origin in origins):
        raise ValueError("Explicit workspace origins are required")
    app = FastAPI(title="Sanity Workspace", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted({urlsplit(origin).hostname for origin in origins}))
    attempts = defaultdict(deque)
    codex, mock_report_ids = None, ()
    if codex_mock_enabled is True:
        from devtools.sanity_workspace_codex import WorkspaceCodexService, REPORT_IDS
        codex = WorkspaceCodexService(enabled=True, advisor_factory=codex_advisor_factory)
        mock_report_ids = REPORT_IDS
        codex.recover()
    database_gate = threading.RLock()

    @contextmanager
    def workspace_transaction(*, readonly=False):
        # A report is an immutable JSONB source. Page responses are small, but
        # decoding four concurrent 100,000-row sources needlessly multiplies
        # peak memory. Queue short DB scopes within this workspace process.
        # External inference runs outside this gate and outside DB transactions.
        with database_gate:
            with Database("oa") as db, db.transaction(readonly=readonly):
                yield db

    @app.middleware("http")
    async def boundaries(request, call_next):
        if request.url.path.startswith(API):
            if request.method not in ("GET", "HEAD"):
                if request.headers.get("origin") not in origins:
                    return JSONResponse({"code": "INVALID_ORIGIN"}, status_code=403)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"code": "INVALID_CONTENT_TYPE"}, status_code=415)
                body = bytearray()
                async for chunk in request.stream():
                    ceiling = (16777216 if request.url.path.endswith('/clustering/operations') else
                               4194304 if '/clustering' in request.url.path else
                               262144 if request.url.path.endswith('/rationale') else 16384)
                    if len(body) + len(chunk) > ceiling:
                        return JSONResponse({"code": "REQUEST_TOO_LARGE"}, status_code=413)
                    body.extend(chunk)
                request._body = bytes(body)
            try:
                response = await call_next(request)
            except Exception:
                response = JSONResponse({"code": "INTERNAL_ERROR"}, status_code=500)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        return await call_next(request)

    @app.exception_handler(ReviewError)
    async def review_error(request, exc):
        code = exc.code
        status = {"UNAUTHORIZED": 401, "FORBIDDEN": 403, "INVALID_CSRF": 403, "NOT_FOUND": 404,
                  "REVISION_CONFLICT": 409, "REQUEST_CONFLICT": 409, "POR_UNCHANGED": 409,
                  "PROPOSAL_STALE": 409, "REVIEW_BLOCKED": 409, "AI_NOT_CONFIGURED": 503,
                  "CLUSTER_NOT_CONFIRMED": 409, "CLUSTER_NOT_READY": 409, "CLUSTER_BLOCKED": 409,
                  "CLUSTER_LIMIT": 413,
                  "REPORT_LIMIT": 413,
                  "SOURCE_CONTEXT_LIMIT": 413, "SOURCE_FILE_ON_VWP": 409,
                  "CLUSTER_TASK_ACTIVE": 409,
                  "RATIONALE_REQUIRED": 409, "RATIONALE_STALE": 409,
                  "AI_BUSY": 409, "AI_MOCK_LIMIT": 429, "SYNTHETIC_ONLY": 400,
                  "WORKSPACE_NOT_CONFIGURED": 503, "DATABASE_UNAVAILABLE": 503,
                  "DATABASE_ERROR": 503, "DATABASE_SCHEMA": 503, "STATE_CORRUPT": 503,
                  "TOO_MANY_ATTEMPTS": 429}.get(code, 400)
        return JSONResponse({"code": code}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"code": "INVALID_REQUEST"}, status_code=400)

    def authenticated(request: Request):
        with workspace_transaction(readonly=True) as db:
            result = WorkspaceStore(db).session(request.cookies.get(COOKIE))
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
                result["csrf"], request.headers.get("x-csrf-token", "")):
            raise ReviewError("INVALID_CSRF", "Session CSRF token is required")
        return result

    @app.post(API + "/login")
    def login(body: Login, request: Request, response: Response):
        key = (request.client.host if request.client else "local", body.username)
        at, recent = time.monotonic(), attempts[key]
        while recent and recent[0] < at - 60:
            recent.popleft()
        if len(recent) >= 20:
            raise ReviewError("TOO_MANY_ATTEMPTS", "Wait before logging in again")
        recent.append(at)
        with workspace_transaction() as db:
            token, result = WorkspaceStore(db).login(body.username, body.password)
        response.set_cookie(COOKIE, token, max_age=8 * 3600, httponly=True,
                            secure=request.headers["origin"].startswith("https://"), samesite="strict", path="/workspace")
        return result

    @app.get(API + "/session")
    def session(actor=Depends(authenticated)):
        return actor

    @app.post(API + "/logout")
    def logout(request: Request, response: Response, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            WorkspaceStore(db).logout(request.cookies[COOKIE])
        response.delete_cookie(COOKIE, path="/workspace")
        return {"status": "LOGGED_OUT"}

    @app.get(API + "/overview")
    def overview(actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspaceStore(db).overview(actor["user"]["id"])

    @app.get(API + "/blocks/{block_id}/versions")
    def versions(block_id: ID, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspaceStore(db).versions(actor["user"]["id"], block_id)

    @app.get(API + "/blocks/{block_id}/submissions")
    def submission_list(block_id: ID, offset: Annotated[int, Query(ge=0)] = 0,
                        limit: Annotated[int, Query(ge=1,le=100)] = 25, actor=Depends(authenticated)):
        from oa.sanity_workspace_sources import submissions
        with workspace_transaction(readonly=True) as db:
            return submissions(db,actor['user']['id'],block_id,offset=offset,limit=limit)

    @app.get(API + "/blocks/{block_id}/history")
    def history(block_id: ID, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspaceStore(db).history(actor["user"]["id"], block_id)

    @app.post(API + "/blocks/{block_id}/por")
    def por(block_id: ID, body: PorChange, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            return WorkspaceStore(db).assign_por(actor["user"]["id"], block_id, body.version_id,
                body.expected_revision, body.request_id, body.reason)

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports")
    def reports(block_id: ID, version_id: ID, eco: Annotated[str | None, Query(max_length=100)] = None,
                item_id: Annotated[str | None, Query(max_length=200)] = None,
                family: Annotated[str | None, Query(pattern=r"^(GCA|VCLP|SPYGLASS)$")] = None,
                status: Annotated[str | None, Query(pattern=r"^(PASS|FAIL|UNKNOWN|MISSING)$")] = None,
                search: Annotated[str, Query(max_length=100)] = "", offset: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=100)] = 25, include_history: bool = False, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspaceStore(db).reports(actor["user"]["id"], block_id, version_id, eco=eco,
                item_id=item_id, family=family, status=status, search=search, offset=offset, limit=limit,include_history=include_history)

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}")
    def report(block_id: ID, version_id: ID, report_id: ID, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspaceStore(db).report(actor["user"]["id"], block_id, version_id, report_id,hydrate_source=False)

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/review")
    def review_detail(block_id: ID, version_id: ID, report_id: ID, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspaceReviewStore(db).view(actor["user"]["id"], block_id, version_id, report_id)

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/review")
    def review_save(block_id: ID, version_id: ID, report_id: ID, body: ReviewChange, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            return WorkspaceReviewStore(db).save(actor["user"]["id"], block_id, version_id, report_id, **body.model_dump())

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/review-proposals")
    def review_proposal(block_id: ID, version_id: ID, report_id: ID, body: ReviewProposal, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            return WorkspaceReviewStore(db).propose(actor["user"]["id"], block_id, version_id, report_id, **body.model_dump())

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering")
    def clustering_detail(block_id: ID, version_id: ID, report_id: ID, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspaceClusteringStore(db).view(actor["user"]["id"], block_id, version_id, report_id)

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/proposals")
    def clustering_proposal(block_id: ID, version_id: ID, report_id: ID, body: ClusterProposal, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            return WorkspaceClusteringStore(db).change(actor["user"]["id"], block_id, version_id, report_id,
                                                       action="PROPOSE", **body.model_dump())

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering")
    def clustering_change(block_id: ID, version_id: ID, report_id: ID, body: ClusterChange, compact: bool = False, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            value = WorkspaceClusteringStore(db).change(actor["user"]["id"], block_id, version_id, report_id, **body.model_dump())
            return event_brief(value) if compact else value

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/workbench")
    def workbench(block_id: ID, version_id: ID, report_id: ID, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            value = WorkspacePipelineStore(db).view_paged(actor["user"]["id"], block_id, version_id, report_id)
            if codex is not None and report_id in mock_report_ids:
                value["ai"] = codex.view(db, actor["user"]["id"], block_id, version_id, report_id)
            return value

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/ai")
    def codex_status(block_id: ID, version_id: ID, report_id: ID, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            if codex is None:
                WorkspaceClusteringStore(db).context(actor["user"]["id"], block_id, version_id, report_id)
                return {"available": False, "supported": False, "enabled": False, "model": "",
                        "remaining": 0, "job": None}
            return codex.view(db, actor["user"]["id"], block_id, version_id, report_id)

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/ai", status_code=202)
    def codex_proposal(block_id: ID, version_id: ID, report_id: ID, body: CodexProposal, actor=Depends(authenticated)):
        # Reservations/results use short transactions; inference holds no DB lock.
        check(codex is not None, "AI_NOT_CONFIGURED")
        return codex.run(user_id=actor["user"]["id"], block_id=block_id, version_id=version_id,
                         report_id=report_id, **body.model_dump())

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/groups")
    def groups_page(block_id: ID, version_id: ID, report_id: ID, expected_revision: Annotated[int, Query(ge=0)],
                    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 25,
                    search: Annotated[str, Query(max_length=100)] = "", event_id: UUID | None = None,
                    status: Literal["", "UNREVIEWED", "PENDING", "PASS", "FAIL", "DEFER", "STALE"] = "",
                    rationale_status: Literal["", "UNRESOLVED", "MISSING", "DRAFT", "CONFIRMED", "STALE"] = "", actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspacePipelineStore(db).groups_page(actor["user"]["id"], block_id, version_id, report_id,
                expected_revision=expected_revision, offset=offset, limit=limit, search=search, status=status,
                event_id=str(event_id) if event_id else None, rationale_status=rationale_status)

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/groups/{group_id}/rationale")
    def group_rationale(block_id: ID, version_id: ID, report_id: ID, group_id: ID,
                        expected_revision: Annotated[int, Query(ge=1)], event_id: UUID | None = None,
                        offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=25)] = 10,
                        actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspacePipelineStore(db).rationale_detail(actor["user"]["id"], block_id, version_id, report_id,
                group_id, expected_revision=expected_revision, event_id=str(event_id) if event_id else None,
                offset=offset, limit=limit)

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/groups/{group_id}/rationale")
    def group_rationale_save(block_id: ID, version_id: ID, report_id: ID, group_id: ID,
                            body: RationaleChange, actor=Depends(authenticated)):
        operation = {"kind": body.action, "group_id": group_id}
        if body.action == "SAVE":
            operation.update(content=body.content.model_dump(mode="json") if body.content else None, reviewed=body.reviewed)
        else:
            check(body.content is None and not body.reviewed, "INVALID_REQUEST")
        with workspace_transaction() as db:
            return event_brief(WorkspaceClusteringStore(db).change(actor["user"]["id"], block_id, version_id, report_id,
                action="EDIT", expected_revision=body.expected_revision, request_id=body.request_id,
                reason=body.reason, rationale_operation=operation))

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/records")
    def records_page(block_id: ID, version_id: ID, report_id: ID, expected_revision: Annotated[int, Query(ge=0)],
                     offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                     search: Annotated[str, Query(max_length=100)] = "", group_id: ID | None = None,
                     event_id: UUID | None = None, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspacePipelineStore(db).records_page(actor["user"]["id"], block_id, version_id, report_id,
                expected_revision=expected_revision, offset=offset, limit=limit, search=search, group_id=group_id,
                event_id=str(event_id) if event_id else None)

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/history")
    def cluster_history(block_id: ID, version_id: ID, report_id: ID, offset: Annotated[int, Query(ge=0)] = 0,
                        limit: Annotated[int, Query(ge=1, le=100)] = 25, removed: bool = False, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            return WorkspacePipelineStore(db).history_page(actor["user"]["id"], block_id, version_id, report_id,
                                                          offset=offset, limit=limit, removed=removed)

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/operations")
    def cluster_edit(block_id: ID, version_id: ID, report_id: ID, body: ClusterEdit, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            fields = body.model_dump(exclude={"operation"})
            return event_brief(WorkspacePipelineStore(db).change(actor["user"]["id"], block_id, version_id, report_id,
                action="EDIT", operation=body.operation.model_dump(exclude_defaults=True, exclude_none=True), **fields))

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/tasks")
    def cluster_task(block_id: ID, version_id: ID, report_id: ID, body: ClusterProposal, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            return WorkspacePipelineStore(db).start_task(actor["user"]["id"], block_id, version_id, report_id, **body.model_dump())

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/clustering/tasks/step")
    def cluster_step(block_id: ID, version_id: ID, report_id: ID, body: ClusterTaskStep, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            return WorkspacePipelineStore(db).advance_task(actor["user"]["id"], block_id, version_id, report_id, **body.model_dump())

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/source")
    def report_source(block_id: ID, version_id: ID, report_id: ID, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            report = WorkspaceStore(db).report(actor["user"]["id"], block_id, version_id, report_id,hydrate_source=False)
            check(report.get('source_storage')!='PARTS_V2','SOURCE_FILE_ON_VWP','Original report bytes are retained on VWP')
            return Response(report.get("raw_report") or "", media_type="text/plain",
                headers={"Content-Disposition": f'attachment; filename="{report_id}.rpt"'})

    @app.get(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/groups/{group_id}/review")
    def group_review_detail(block_id: ID, version_id: ID, report_id: ID, group_id: ID, compact: bool = False, actor=Depends(authenticated)):
        with workspace_transaction(readonly=True) as db:
            value = WorkspaceGroupReviewStore(db, group_id).view(actor["user"]["id"], block_id, version_id, report_id)
            return compact_review(value) if compact else value

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/groups/{group_id}/review")
    def group_review_save(block_id: ID, version_id: ID, report_id: ID, group_id: ID, body: GroupReviewChange, compact: bool = False, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            value = WorkspaceGroupReviewStore(db, group_id, body.expected_cluster_revision).save(
                actor["user"]["id"], block_id, version_id, report_id, **body.model_dump(exclude={"expected_cluster_revision"}))
            return compact_review(value) if compact else value

    @app.post(API + "/blocks/{block_id}/versions/{version_id}/reports/{report_id}/groups/{group_id}/review-proposals")
    def group_review_proposal(block_id: ID, version_id: ID, report_id: ID, group_id: ID, body: GroupReviewProposal, compact: bool = False, actor=Depends(authenticated)):
        with workspace_transaction() as db:
            value = WorkspaceGroupReviewStore(db, group_id, body.expected_cluster_revision).propose(
                actor["user"]["id"], block_id, version_id, report_id, **body.model_dump(exclude={"expected_cluster_revision"}))
            return compact_review(value) if compact else value

    if static_dir is not None:
        static_dir = Path(static_dir).resolve()

        @app.get("/workspace")
        @app.get("/workspace/")
        def workspace():
            return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})

        @app.get("/")
        def home(request: Request):
            if request.query_params.get("demo") == "1":
                return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})
            return RedirectResponse("/workspace", status_code=307)

        # No legacy business API is enabled through this test deployment.
        @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        def legacy_api(path: str):
            return JSONResponse({"code": "AUTHORIZATION_NOT_IMPLEMENTED"}, status_code=503)

        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
