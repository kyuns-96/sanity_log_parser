from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def finite(value, *, nonnegative=False):
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("A finite number is required") from None
    if not number.is_finite() or (nonnegative and number < 0):
        raise ValueError("A finite nonnegative number is required" if nonnegative else "A finite number is required")
    return number


class UploadMetadata(ContractModel):
    project_id: UUID
    version_id: UUID
    block_id: UUID
    snapshot_id: UUID | None = None
    execution_name: str = Field(min_length=1, max_length=500)
    design_stage: str = Field(min_length=1, max_length=500)
    hierarchy_separator: str = Field(min_length=1, max_length=8)
    area_unit: str | None = None
    area_basis: str | None = None
    library: str | None = None
    classification: str | None = None
    constraint_context: dict = Field(default_factory=dict)
    analysis_conditions: dict = Field(default_factory=dict)
    pct_tolerance: Decimal | None = None
    power_tolerance_mw: Decimal | None = None

    @field_validator("execution_name", "design_stage", "hierarchy_separator", "area_unit", "area_basis", "library", "classification")
    @classmethod
    def meaningful_text(cls, value):
        if value is not None and (not value.strip() or any(char in value for char in ("\x00", "\n", "\r"))):
            raise ValueError("A nonblank single-line value is required")
        return value

    @field_validator("pct_tolerance", "power_tolerance_mw")
    @classmethod
    def valid_tolerance(cls, value):
        return finite(value, nonnegative=True) if value is not None else None


class ScopeSelection(ContractModel):
    kind: Literal["all", "targets"] = "all"
    target_ids: list[UUID] = Field(default_factory=list)
    include_descendants: bool = False

    @model_validator(mode="after")
    def coherent_scope(self):
        if self.kind == "targets" and not self.target_ids:
            raise ValueError("Select at least one target")
        if self.kind == "all" and self.target_ids:
            raise ValueError("All-hierarchy scope must not contain target IDs")
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("Target IDs must be unique")
        return self


class MetricThreshold(ContractModel):
    absolute_change: Decimal | None = None
    relative_change_pct: Decimal | None = None
    pct_point_change: Decimal | None = None
    min_denominator: Decimal | None = None

    @field_validator("absolute_change", "relative_change_pct", "pct_point_change", "min_denominator")
    @classmethod
    def valid_bound(cls, value):
        return finite(value, nonnegative=True) if value is not None else None

    @model_validator(mode="after")
    def not_empty(self):
        if all(value is None for value in (self.absolute_change, self.relative_change_pct, self.pct_point_change, self.min_denominator)):
            raise ValueError("A metric rule must specify a threshold or minimum denominator")
        return self


class DetectionPolicy(ContractModel):
    metrics: dict[str, MetricThreshold] = Field(default_factory=dict)
    operating_limits: dict[str, dict] = Field(default_factory=dict)

    @field_validator("operating_limits")
    @classmethod
    def valid_limits(cls, value):
        result = {}
        for metric, limits in value.items():
            if not metric.strip() or not limits or set(limits) - {"lower", "upper", "min_denominator"}:
                raise ValueError("Operating limits use lower, upper and min_denominator")
            checked = {key: finite(bound, nonnegative=key == "min_denominator") for key, bound in limits.items()}
            if "lower" in checked and "upper" in checked and checked["lower"] > checked["upper"]:
                raise ValueError("Operating lower limit exceeds upper limit")
            result[metric] = checked
        return result


class MappingRule(ContractModel):
    id: UUID = Field(default_factory=uuid4)
    kind: Literal["one_to_one", "split", "merge", "added", "removed"]
    scope: Literal["node", "subtree"] = "node"
    baseline_paths: list[str] = Field(default_factory=list)
    current_paths: list[str] = Field(default_factory=list)
    target_id: UUID | None = None
    description: str = ""

    @model_validator(mode="after")
    def valid_members(self):
        before, after = len(self.baseline_paths), len(self.current_paths)
        allowed = {
            "one_to_one": before == 1 and after == 1,
            "split": before == 1 and after >= 2,
            "merge": before >= 2 and after == 1,
            "added": before == 0 and after >= 1,
            "removed": before >= 1 and after == 0,
        }
        if not allowed[self.kind]:
            raise ValueError("Mapping member counts do not match the mapping kind")
        if self.scope == "subtree" and self.kind != "one_to_one":
            raise ValueError("Subtree rules require one-to-one mapping")
        for paths in (self.baseline_paths, self.current_paths):
            if len(set(paths)) != len(paths) or any(not path.strip() or "\x00" in path for path in paths):
                raise ValueError("Mapping paths must be nonblank and unique")
        return self


class MappingPayload(ContractModel):
    baseline_project_id: UUID
    baseline_version_id: UUID
    baseline_block_id: UUID
    current_project_id: UUID
    current_version_id: UUID
    current_block_id: UUID
    rules: list[MappingRule] = Field(min_length=1)
    valid_from_version_id: UUID | None = None
    valid_through_version_id: UUID | None = None

    @model_validator(mode="after")
    def unique_rule_ids(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Mapping rule IDs must be unique")
        return self


def email_address(value: str) -> str:
    from email.headerregistry import Address
    if not isinstance(value, str) or any(char in value for char in ("\r", "\n", "\x00")):
        raise ValueError("Invalid email address")
    try:
        address = Address(addr_spec=value.strip())
    except (ValueError, IndexError):
        raise ValueError("Invalid email address") from None
    if not address.username or not address.domain:
        raise ValueError("Email address must include a domain")
    return address.addr_spec


class ComparisonRequest(ContractModel):
    current_upload_id: UUID
    baseline_upload_id: UUID | None = None
    comparison_kind: Literal["previous_version", "previous_project"] = "previous_version"
    scope: ScopeSelection = Field(default_factory=ScopeSelection)
    mapping_id: UUID | None = None
    change_ids: list[UUID] = Field(default_factory=list)
    policy: DetectionPolicy = Field(default_factory=DetectionPolicy)
    recipients: list[str] = Field(default_factory=list)
    power_pairs: list[dict] = Field(default_factory=list)

    @field_validator("recipients")
    @classmethod
    def valid_recipients(cls, value):
        return list(dict.fromkeys(email_address(item) for item in value))

    @field_validator("power_pairs")
    @classmethod
    def valid_pairs(cls, value):
        result = []
        for pair in value:
            if set(pair) - {"current_upload_id", "baseline_upload_id", "label"} or not pair.get("current_upload_id"):
                raise ValueError("Power pair requires current_upload_id and optional baseline_upload_id/label")
            item = {"current_upload_id": str(UUID(str(pair["current_upload_id"]))) }
            item["baseline_upload_id"] = str(UUID(str(pair["baseline_upload_id"]))) if pair.get("baseline_upload_id") else None
            if "label" in pair:
                if not isinstance(pair["label"], str) or not pair["label"].strip():
                    raise ValueError("Power pair label must be nonblank")
                item["label"] = pair["label"]
            result.append(item)
        return result

    @model_validator(mode="after")
    def distinct_inputs(self):
        if self.current_upload_id == self.baseline_upload_id:
            raise ValueError("Current and baseline uploads must differ")
        if len(set(self.change_ids)) != len(self.change_ids):
            raise ValueError("Change revisions must be unique")
        return self
