"""Shared stateless request payload models and HTTP helpers for workspace & workbench APIs."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints

from common.sanity_clustering import check
from oa.sanity_workspace_clustering import MAX_GROUPS, MAX_RECORDS

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


class ReviewException(BaseModel):
    """Human annotation; never an executable rule or catalog modification."""
    model_config = ConfigDict(extra="forbid")
    rule: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]
    reason: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)]
    conditions: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)]


class ReviewChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conclusion: Literal["PASS", "FAIL", "DEFER"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    criteria: Annotated[str, Field(max_length=2000)] = ""
    conditions: Annotated[dict[str, Annotated[str, Field(strict=True, max_length=500)]], Field(max_length=12)] = Field(default_factory=dict)
    reusable: StrictBool = False
    compatible_versions: StrictBool = False
    exception: ReviewException | None = None
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
    summary: Annotated[str, Field(min_length=1, max_length=120)] | None = None
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


def workbench_body_ceiling(path: str, *, default_ceiling: int) -> int:
    if path.endswith("/clustering/operations"):
        return 16777216
    if "/clustering" in path:
        return 4194304
    if path.endswith("/rationale"):
        return 262144
    return default_ceiling


def build_rationale_operation(group_id: str, body: RationaleChange) -> dict:
    operation: dict = {"kind": body.action, "group_id": group_id}
    if body.action == "SAVE":
        operation.update(
            content=body.content.model_dump(mode="json", exclude_none=True) if body.content else None,
            reviewed=body.reviewed,
        )
    else:
        check(body.content is None and not body.reviewed, "INVALID_REQUEST")
    return operation


def raw_report_source_response(report: dict, report_id: str) -> Response:
    check(report.get("source_storage") != "PARTS_V2", "SOURCE_FILE_ON_VWP", "Original report bytes are retained on VWP")
    return Response(
        report.get("raw_report") or "",
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.rpt"'},
    )
