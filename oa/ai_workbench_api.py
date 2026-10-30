"""Production SSO workbench routes on the caller's authorized transaction.

Only the durable AI job endpoint proposes model results. Synthetic login,
reference-engine proposals and legacy whole-report judgments are not mounted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from common.sanity_clustering import check
from common.sanity_ai_knowledge import shared_case_views
from oa.sanity_production_workspace import ProductionWorkspaceStore
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
from oa.sanity_workspace_pipeline import WorkspacePipelineStore, compact_review, event_brief
from oa.sanity_workspace_sources import submissions
from oa.workspace_api import (ID, ClusterChange, ClusterEdit, CodexProposal,
                              GroupReviewChange, PorChange, RationaleChange)


class AIProposal(CodexProposal):
    model_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    connection_id: UUID | None = None


class GroupAIProposal(AIProposal):
    expected_cluster_revision: Annotated[StrictInt, Field(ge=1)]


class AIResume(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID


class AIRetryUnknown(AIResume):
    expected_event_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_step_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    acknowledge_duplicate: StrictBool


class IntakeDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['CONFIRM','REJECT']
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    request_id: UUID
    reason: str = Field(min_length=1,max_length=2000)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    manifest_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    scope_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmed_scope: StrictBool


@dataclass(frozen=True)
class WorkbenchScope:
    user_id: str
    ws: ProductionWorkspaceStore

    @property
    def db(self):
        return self.ws.db


def jobs(scope):
    # No model invocation is performed by this service's enqueue/capability.
    from oa.sanity_ai_jobs import AIJobService
    return AIJobService(scope.db, scope.ws)


def create_workbench_router(authorized_actor):
    router = APIRouter(prefix="/api/workbench")
    report_path = "/blocks/{block_id}/versions/{version_id}/reports/{report_id}"

    def workspace(actor=Depends(authorized_actor)):
        access = getattr(actor, "access", None)
        check(getattr(actor, "auth_method", None) == "sso" and access is not None,
              "FORBIDDEN", "The production workbench requires an authorized SSO identity")
        return WorkbenchScope(actor.user_id, ProductionWorkspaceStore(access.db, access))

    @router.get("/session")
    def session(scope=Depends(workspace)):
        return {"user": scope.ws.user(scope.user_id), "csrf": "", "expires_at": "",
                "environment": "PRODUCTION_SSO", "is_admin": scope.ws.authorization.is_admin(scope.user_id)}

    @router.get("/overview")
    def overview(scope=Depends(workspace)):
        return scope.ws.overview(scope.user_id)

    @router.get("/ai/jobs")
    def ai_jobs(scope=Depends(workspace)):
        from oa.sanity_ai_monitor import Monitor
        return Monitor(jobs(scope)).items(scope.user_id)

    @router.post("/ai/jobs/{job_id}/cancel")
    def cancel_ai_job(job_id: UUID,scope=Depends(workspace)):
        return jobs(scope).cancel(scope.user_id,str(job_id))

    @router.post("/ai/jobs/{job_id}/resume",status_code=202)
    def resume_ai_job(job_id: UUID,body: AIResume,scope=Depends(workspace)):
        return jobs(scope).resume(scope.user_id,str(job_id),body.request_id)

    @router.post("/ai/jobs/{job_id}/retry-unknown",status_code=202)
    def retry_unknown_ai_job(job_id: UUID,body: AIRetryUnknown,scope=Depends(workspace)):
        check(body.acknowledge_duplicate is True, "AI_UNKNOWN_CONFIRM_REQUIRED")
        return jobs(scope).retry_unknown(scope.user_id,str(job_id),body.request_id,
            body.expected_event_sha256,body.expected_step_sha256,body.acknowledge_duplicate)

    @router.post("/ai/jobs/{job_id}/delete")
    def delete_ai_job(job_id: UUID, scope=Depends(workspace)):
        return jobs(scope).delete(scope.user_id, str(job_id))

    @router.get("/blocks/{block_id}/versions")
    def versions(block_id: ID, scope=Depends(workspace)):
        return scope.ws.versions(scope.user_id, block_id)

    @router.get("/blocks/{block_id}/submissions")
    def submission_list(block_id: ID, offset: Annotated[int, Query(ge=0)] = 0,
                        limit: Annotated[int, Query(ge=1, le=100)] = 25, scope=Depends(workspace)):
        return submissions(scope.db, scope.user_id, block_id, offset=offset, limit=limit, workspace=scope.ws)

    @router.get("/blocks/{block_id}/history")
    def history(block_id: ID, scope=Depends(workspace)):
        return scope.ws.history(scope.user_id, block_id)

    @router.get('/blocks/{block_id}/intake')
    def intake_list(block_id: ID, offset: Annotated[int,Query(ge=0)]=0,
                    limit: Annotated[int,Query(ge=1,le=100)]=25, scope=Depends(workspace)):
        from oa.sanity_submission_intake import SubmissionIntake
        return SubmissionIntake(scope.ws).list(scope.user_id,block_id,offset=offset,limit=limit)

    @router.get('/blocks/{block_id}/intake/{sid}')
    def intake_detail(block_id: ID, sid: UUID, offset: Annotated[int,Query(ge=0)]=0,
                      limit: Annotated[int,Query(ge=1,le=100)]=50, scope=Depends(workspace)):
        from oa.sanity_submission_intake import SubmissionIntake
        return SubmissionIntake(scope.ws).detail(scope.user_id,block_id,str(sid),offset=offset,limit=limit)

    @router.post('/blocks/{block_id}/intake/{sid}')
    def intake_decide(block_id: ID, sid: UUID, body: IntakeDecision, scope=Depends(workspace)):
        from oa.sanity_submission_intake import SubmissionIntake
        return SubmissionIntake(scope.ws).decide(scope.user_id,block_id,str(sid),**body.model_dump())

    @router.get("/blocks/{block_id}/por")
    def current_por(block_id: ID, scope=Depends(workspace)):
        scope.ws.access(scope.user_id, block_id)
        return scope.ws.por(block_id)

    @router.post("/blocks/{block_id}/por")
    def por(block_id: ID, body: PorChange, scope=Depends(workspace)):
        return scope.ws.assign_por(scope.user_id, block_id, body.version_id,
                                   body.expected_revision, body.request_id, body.reason)

    @router.get("/blocks/{block_id}/versions/{version_id}/reports")
    def reports(block_id: ID, version_id: ID, eco: Annotated[str | None, Query(max_length=100)] = None,
                item_id: Annotated[str | None, Query(max_length=200)] = None,
                family: Annotated[str | None, Query(pattern=r"^(GCA|VCLP|SPYGLASS)$")] = None,
                status: Annotated[str | None, Query(pattern=r"^(PASS|FAIL|UNKNOWN|MISSING)$")] = None,
                search: Annotated[str, Query(max_length=100)] = "", offset: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=100)] = 25, include_history: bool = False,
                scope=Depends(workspace)):
        return scope.ws.reports(scope.user_id, block_id, version_id, eco=eco, item_id=item_id,
                                family=family, status=status, search=search, offset=offset,
                                limit=limit, include_history=include_history)

    @router.get(report_path)
    def report(block_id: ID, version_id: ID, report_id: ID, scope=Depends(workspace)):
        return scope.ws.report(scope.user_id, block_id, version_id, report_id, hydrate_source=False)

    @router.get(report_path + "/source")
    def source(block_id: ID, version_id: ID, report_id: ID, scope=Depends(workspace)):
        value = scope.ws.report(scope.user_id, block_id, version_id, report_id, hydrate_source=False)
        check(value.get("source_storage") != "PARTS_V2", "SOURCE_FILE_ON_VWP",
              "Original report bytes are retained on VWP")
        return Response(value.get("raw_report") or "", media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{report_id}.rpt"'})

    @router.get(report_path + "/preserved/{source_id}")
    def preserved_text(block_id: ID, version_id: ID, report_id: ID, source_id: ID,
                       offset: Annotated[int, Query(ge=0)] = 0,
                       limit: Annotated[int, Query(ge=1, le=20)] = 10, download: bool = False,
                       scope=Depends(workspace)):
        from oa.sanity_preserved_text import read
        report = scope.ws.report(scope.user_id, block_id, version_id, report_id, hydrate_source=False)
        file, segments = read(scope.db, report, source_id)
        if download:
            return Response(''.join(s['text'] for s in segments), media_type='text/plain',
                headers={'Content-Disposition': 'attachment; filename="preserved-masked.txt"',
                         'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})
        return {'source': file, 'items': segments[offset:offset+limit], 'total': len(segments),
                'offset': offset, 'limit': limit, 'complete': True, 'masked': True}

    @router.get(report_path + "/clustering")
    def clustering_detail(block_id: ID, version_id: ID, report_id: ID, scope=Depends(workspace)):
        value = WorkspaceClusteringStore(scope.db, ws=scope.ws).view(scope.user_id, block_id, version_id, report_id)
        value["ai"] = jobs(scope).capability(scope.user_id, block_id, version_id, report_id)
        value["corporate_ai_connected"] = value["ai"]["enabled"]
        return value

    @router.post(report_path + "/clustering")
    def clustering_change(block_id: ID, version_id: ID, report_id: ID, body: ClusterChange,
                          compact: bool = False, scope=Depends(workspace)):
        value = WorkspaceClusteringStore(scope.db, ws=scope.ws).change(
            scope.user_id, block_id, version_id, report_id, **body.model_dump())
        return event_brief(value) if compact else shared_case_views(value)

    @router.get(report_path + "/workbench")
    def workbench(block_id: ID, version_id: ID, report_id: ID, scope=Depends(workspace)):
        value = WorkspacePipelineStore(scope.db, ws=scope.ws).view_paged(scope.user_id, block_id, version_id, report_id)
        value["ai"] = jobs(scope).capability(scope.user_id, block_id, version_id, report_id)
        value["corporate_ai_connected"] = value["ai"]["enabled"]
        return value

    @router.get(report_path + "/clustering/ai")
    def cluster_ai_status(block_id: ID, version_id: ID, report_id: ID, scope=Depends(workspace)):
        return jobs(scope).capability(scope.user_id, block_id, version_id, report_id)

    @router.post(report_path + "/clustering/ai", status_code=202)
    def cluster_ai_enqueue(block_id: ID, version_id: ID, report_id: ID, body: AIProposal,
                           scope=Depends(workspace)):
        return jobs(scope).request(scope.user_id, block_id, version_id, report_id, **body.model_dump(exclude_none=True))

    @router.get(report_path + "/clustering/groups")
    def groups_page(block_id: ID, version_id: ID, report_id: ID, expected_revision: Annotated[int, Query(ge=0)],
                    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 25,
                    search: Annotated[str, Query(max_length=100)] = "", event_id: UUID | None = None,
                    status: Literal["", "UNREVIEWED", "PENDING", "PASS", "FAIL", "DEFER", "STALE"] = "",
                    rationale_status: Literal["", "UNRESOLVED", "MISSING", "DRAFT", "CONFIRMED", "STALE"] = "",
                    scope=Depends(workspace)):
        return WorkspacePipelineStore(scope.db, ws=scope.ws).groups_page(
            scope.user_id, block_id, version_id, report_id, expected_revision=expected_revision,
            offset=offset, limit=limit, search=search, status=status,
            event_id=str(event_id) if event_id else None, rationale_status=rationale_status)

    @router.get(report_path + "/clustering/records")
    def records_page(block_id: ID, version_id: ID, report_id: ID, expected_revision: Annotated[int, Query(ge=0)],
                     offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                     search: Annotated[str, Query(max_length=100)] = "", group_id: ID | None = None,
                     event_id: UUID | None = None, scope=Depends(workspace)):
        return WorkspacePipelineStore(scope.db, ws=scope.ws).records_page(
            scope.user_id, block_id, version_id, report_id, expected_revision=expected_revision,
            offset=offset, limit=limit, search=search, group_id=group_id, event_id=str(event_id) if event_id else None)

    @router.get(report_path + "/clustering/history")
    def cluster_history(block_id: ID, version_id: ID, report_id: ID, offset: Annotated[int, Query(ge=0)] = 0,
                        limit: Annotated[int, Query(ge=1, le=100)] = 25, removed: bool = False, scope=Depends(workspace)):
        return WorkspacePipelineStore(scope.db, ws=scope.ws).history_page(
            scope.user_id, block_id, version_id, report_id, offset=offset, limit=limit, removed=removed)

    @router.post(report_path + "/clustering/operations")
    def cluster_edit(block_id: ID, version_id: ID, report_id: ID, body: ClusterEdit, scope=Depends(workspace)):
        return event_brief(WorkspacePipelineStore(scope.db, ws=scope.ws).change(
            scope.user_id, block_id, version_id, report_id, action="EDIT",
            operation=body.operation.model_dump(exclude_defaults=True, exclude_none=True),
            **body.model_dump(exclude={"operation"})))

    @router.get(report_path + "/groups/{group_id}/rationale")
    def group_rationale(block_id: ID, version_id: ID, report_id: ID, group_id: ID,
                        expected_revision: Annotated[int, Query(ge=1)], event_id: UUID | None = None,
                        offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=25)] = 10,
                        scope=Depends(workspace)):
        return WorkspacePipelineStore(scope.db, ws=scope.ws).rationale_detail(
            scope.user_id, block_id, version_id, report_id, group_id, expected_revision=expected_revision,
            event_id=str(event_id) if event_id else None, offset=offset, limit=limit)

    @router.post(report_path + "/groups/{group_id}/rationale")
    def group_rationale_save(block_id: ID, version_id: ID, report_id: ID, group_id: ID,
                            body: RationaleChange, scope=Depends(workspace)):
        operation = {"kind": body.action, "group_id": group_id}
        if body.action == "SAVE":
            operation.update(content=body.content.model_dump(mode="json") if body.content else None, reviewed=body.reviewed)
        else:
            check(body.content is None and not body.reviewed, "INVALID_REQUEST")
        return event_brief(WorkspaceClusteringStore(scope.db, ws=scope.ws).change(
            scope.user_id, block_id, version_id, report_id, action="EDIT", expected_revision=body.expected_revision,
            request_id=body.request_id, reason=body.reason, rationale_operation=operation))

    @router.get(report_path + "/groups/{group_id}/review")
    def group_review_detail(block_id: ID, version_id: ID, report_id: ID, group_id: ID,
                            compact: bool = False, scope=Depends(workspace)):
        value = WorkspaceGroupReviewStore(scope.db, group_id, ws=scope.ws).view(scope.user_id, block_id, version_id, report_id)
        value["ai"] = jobs(scope).capability(scope.user_id, block_id, version_id, report_id, group_id=group_id)
        value["corporate_ai_connected"] = value["ai"]["enabled"]
        return compact_review(value) if compact else value

    @router.post(report_path + "/groups/{group_id}/review")
    def group_review_save(block_id: ID, version_id: ID, report_id: ID, group_id: ID, body: GroupReviewChange,
                          compact: bool = False, scope=Depends(workspace)):
        value = WorkspaceGroupReviewStore(scope.db, group_id, body.expected_cluster_revision, ws=scope.ws).save(
            scope.user_id, block_id, version_id, report_id, **body.model_dump(exclude={"expected_cluster_revision"}))
        return shared_case_views(compact_review(value) if compact else value)

    @router.get(report_path + "/groups/{group_id}/review/ai")
    def group_ai_status(block_id: ID, version_id: ID, report_id: ID, group_id: ID,
                        expected_cluster_revision: Annotated[int | None, Query(ge=1)] = None, scope=Depends(workspace)):
        return jobs(scope).capability(scope.user_id, block_id, version_id, report_id, group_id=group_id,
                                      expected_cluster_revision=expected_cluster_revision)

    @router.post(report_path + "/groups/{group_id}/review/ai", status_code=202)
    def group_ai_enqueue(block_id: ID, version_id: ID, report_id: ID, group_id: ID, body: GroupAIProposal,
                         scope=Depends(workspace)):
        return jobs(scope).request(scope.user_id, block_id, version_id, report_id, group_id=group_id, **body.model_dump(exclude_none=True))

    return router
