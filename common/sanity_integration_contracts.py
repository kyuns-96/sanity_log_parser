"""Company adapter types; existing validators remain the runtime authority.

These declarations do not load an SDK, discover plugins, grant access or change
the wire/storage format. Python 3.10 is supported. Read INTERNAL_AI_HANDOFF.md.
"""
from abc import ABC, abstractmethod
from typing import Any, Literal, TypedDict

from common.sanity_common import ReviewError

JsonObject = dict[str, Any]


class RunDescription(TypedDict):
    run_id: str
    project: str
    design: str
    stage: str
    tool: str
    tool_version: str


class _PlannedUnit(TypedDict):
    review_unit_id: str
    item_id: str
    report: str
    parser: str
    rule_version: str
    condition: JsonObject
    identity_fields: list[str]
    execution_status: Literal["COMPLETED", "FAILED", "UNKNOWN", "NOT_RUN"]
    applicable: bool


class PlannedUnit(_PlannedUnit, total=False):
    evaluation: JsonObject
    exclusion: JsonObject | None


class ExpectedInventory(TypedDict):
    confirmed: bool
    source: str
    version: str
    units: list[PlannedUnit]


class CollectionPlan(TypedDict):
    schema_version: Literal[1]
    run: RunDescription
    inventory: ExpectedInventory


class SanityPlanProvider(ABC):
    @abstractmethod
    def get_plan(self, run_reference: str) -> CollectionPlan:
        """Read approved run metadata, including missing/failed expected items.

        run_reference is a company-local lookup key, never a shell command or a
        promise of approval. Do not derive the inventory from discovered files,
        mark it confirmed without an authoritative decision, run sanity tools,
        create files or write the review DB. The caller explicitly validates the
        plan with vwp/main.py:validate_plan before collection. No automatic
        collector/registry/workspace connection is installed by this interface.
        """
        raise ReviewError("PLAN_NOT_CONFIGURED", "Company expected-inventory provider is not connected")


class ProposedGroup(TypedDict):
    id: str
    label: str
    record_ids: list[str]


class HistoricalGroupReference(TypedDict):
    case_id: str
    group_id: str
    reason: str


class ProposedGroupRationale(TypedDict):
    group_id: str
    reason: str
    evidence_ids: list[str]
    reference_groups: list[HistoricalGroupReference]


class ClusteringProposalV2(TypedDict):
    groups: list[ProposedGroup]
    reason: str
    reference_ids: list[str]
    group_rationales: list[ProposedGroupRationale]


class ReviewProposal(TypedDict):
    conclusion: Literal["PASS", "FAIL", "DEFER"]
    reason: str
    reference_ids: list[str]


class AdvisorScope(TypedDict, total=False):
    project_id: str
    block_id: str
    item_id: str
    family: str
    parser: str
    rule_version: str
    eco: str | None
    layout: Literal["PRE", "POST"]
    target_type: Literal["GROUP"]


class _AdvisorContext(TypedDict):
    report: JsonObject
    version: JsonObject
    scope: AdvisorScope
    facts: dict[str, str]
    source_sha256: str
    ready: bool
    can_review: bool


class AdvisorContext(_AdvisorContext, total=False):
    """Group fields are supplied together by the common group-review store.

    report/source_sha256 cover the whole immutable report. A group review uses
    group_evidence as its decision target, not report.records from other groups.
    These types do not perform scope/permission or readiness validation.
    """
    group_id: str
    group_snapshot: ProposedGroup
    group_evidence: list[JsonObject]
    cluster_event_id: str
    cluster_revision: int
    membership_sha256: str
    clustering_rationale: JsonObject | None
    blocked_reason: str | None


class _CaseIdentity(TypedDict):
    id: str


class ClusteringCase(_CaseIdentity, total=False):
    """Logical, already filtered cases, including historical optional fields."""
    report_id: str
    version_id: str
    revision: int
    groups: list[ProposedGroup]
    identity_fields: list[str]
    reason: str
    reusable: bool
    criteria: str
    compatible_versions: bool
    source_sha256: str
    records: list[JsonObject]
    report_rule: str
    rationales: dict[str, JsonObject]


class ReviewCase(_CaseIdentity, total=False):
    report_id: str
    version_id: str
    conclusion: Literal["PASS", "FAIL"]
    reason: str
    criteria: str
    conditions: dict[str, str]
    reusable: bool
    compatible_versions: bool
    revision: int
    actor: JsonObject
    at: str
    source_sha256: str
    source_version: JsonObject
    scope: AdvisorScope
    group_id: str
    group_snapshot: ProposedGroup
    cluster_event_id: str
    cluster_revision: int
    membership_sha256: str
    clustering_rationale: JsonObject | None
