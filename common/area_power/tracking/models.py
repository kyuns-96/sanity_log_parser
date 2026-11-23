"""Pydantic input models, corner matching, and dedupe keys for NET_VER tracking."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from common.area_power.jsonutil import digest
from common.area_power.models import ContractModel, email_address, finite
from common.area_power.triage import Thresholds

DEFAULT_DEADLINE_DAYS = 7
DEFAULT_DEADLINE_TIME = "18:00"
DEFAULT_DEADLINE_TIMEZONE = "Asia/Seoul"


def _norm_decimal(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value).strip()
    if not dec.is_finite():
        raise ValueError("A finite decimal number is required")
    normalized = dec.normalize()
    if normalized == 0:
        return "0"
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _clean_text(value: str | None, *, max_len: int = 200) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_len or any(ch in cleaned for ch in ("\x00", "\r", "\n")):
        raise ValueError("A single-line nonblank string is required")
    return cleaned


class CornerSpec(ContractModel):
    label: str = Field(min_length=1, max_length=120)
    process_label: str = Field(min_length=1, max_length=80)
    voltage_v: Decimal | None = None
    temp_c: Decimal | None = None
    pex: str | None = None

    @field_validator("label", "process_label", "pex")
    @classmethod
    def check_text(cls, value):
        if value is None:
            return None
        cleaned = _clean_text(value, max_len=120)
        if cleaned is None:
            raise ValueError("Corner text fields must be nonblank")
        return cleaned

    @field_validator("voltage_v", "temp_c")
    @classmethod
    def check_finite(cls, value):
        return finite(value) if value is not None else None

    def corner_id(self) -> str:
        """Stable identifier matching uploads with identical PVT/PEX (or process-only spec)."""
        parts = [
            self.process_label.strip(),
            _norm_decimal(self.voltage_v) if self.voltage_v is not None else "*",
            _norm_decimal(self.temp_c) if self.temp_c is not None else "*",
            self.pex.strip() if self.pex is not None else "*",
        ]
        return "|".join(parts)

    def matches_upload(self, upload: dict[str, Any]) -> bool:
        if (upload.get("process_label") or "").strip() != self.process_label.strip():
            return False
        if self.voltage_v is not None and _norm_decimal(upload.get("voltage_v")) != _norm_decimal(self.voltage_v):
            return False
        if self.temp_c is not None and _norm_decimal(upload.get("temp_c")) != _norm_decimal(self.temp_c):
            return False
        if self.pex is not None and (upload.get("pex") or "").strip() != self.pex.strip():
            return False
        return True


def upload_corner_tuple(upload: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    return (
        (upload.get("process_label") or "").strip() or None,
        _norm_decimal(upload.get("voltage_v")),
        _norm_decimal(upload.get("temp_c")),
        (upload.get("pex") or "").strip() or None,
    )


def upload_corner_label(upload: dict[str, Any]) -> str:
    proc, volt, temp, pex = upload_corner_tuple(upload)
    parts = [proc or "?"]
    if volt is not None:
        parts.append(f"{volt} V")
    if temp is not None:
        parts.append(f"{temp} °C")
    if pex is not None:
        parts.append(pex)
    return " / ".join(parts)


class TypicalSpec(ContractModel):
    effective_net_ver: str = Field(min_length=1, max_length=200)
    corner: CornerSpec
    analysis_conditions: dict[str, Any] = Field(default_factory=dict)
    label: str = Field(default="", max_length=200)

    @field_validator("effective_net_ver")
    @classmethod
    def check_net_ver(cls, value):
        cleaned = _clean_text(value)
        if not cleaned:
            raise ValueError("effective_net_ver is required")
        return cleaned


class PowerTargetSpec(ContractModel):
    effective_net_ver: str = Field(min_length=1, max_length=200)
    target_mw: Decimal
    label: str = Field(default="", max_length=200)

    @field_validator("effective_net_ver")
    @classmethod
    def check_net_ver(cls, value):
        cleaned = _clean_text(value)
        if not cleaned:
            raise ValueError("effective_net_ver is required")
        return cleaned

    @field_validator("target_mw")
    @classmethod
    def check_positive(cls, value):
        num = finite(value, nonnegative=True)
        if num <= 0:
            raise ValueError("target_mw must be positive")
        return num


class ProjectTrackingConfigInput(ContractModel):
    enabled: bool = False
    default_required_corners: list[CornerSpec] = Field(default_factory=list)
    deadline_days: int = Field(default=DEFAULT_DEADLINE_DAYS, ge=1, le=90)
    deadline_time: str = Field(default=DEFAULT_DEADLINE_TIME)
    deadline_timezone: str = Field(default=DEFAULT_DEADLINE_TIMEZONE, min_length=1, max_length=80)
    top_recipients: list[str] = Field(default_factory=list)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    typical_specs: list[TypicalSpec] = Field(default_factory=list)
    power_targets: list[PowerTargetSpec] = Field(default_factory=list)
    expected_row_version: int | None = Field(default=None, ge=1)

    @field_validator("deadline_time")
    @classmethod
    def check_deadline_time(cls, value):
        cleaned = _clean_text(value, max_len=10)
        if not cleaned or len(cleaned) != 5 or cleaned[2] != ":":
            raise ValueError("deadline_time must use HH:MM format")
        hour, minute = int(cleaned[:2]), int(cleaned[3:])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("deadline_time must be a valid 24-hour clock time")
        return cleaned

    @field_validator("deadline_timezone")
    @classmethod
    def check_timezone(cls, value):
        try:
            ZoneInfo(value.strip())
        except ZoneInfoNotFoundError:
            raise ValueError("deadline_timezone must be a valid IANA timezone") from None
        return value.strip()

    @field_validator("top_recipients")
    @classmethod
    def check_recipients(cls, value):
        return list(dict.fromkeys(email_address(item) for item in value))

    @model_validator(mode="after")
    def unique_specs(self):
        cids = [corner.corner_id() for corner in self.default_required_corners]
        if len(cids) != len(set(cids)):
            raise ValueError("default_required_corners must be unique")
        if len({s.effective_net_ver for s in self.typical_specs}) != len(self.typical_specs):
            raise ValueError("typical_specs must have unique effective_net_ver values")
        if len({s.effective_net_ver for s in self.power_targets}) != len(self.power_targets):
            raise ValueError("power_targets must have unique effective_net_ver values")
        return self


class ApprovedBaselineRef(ContractModel):
    corner_id: str = Field(min_length=1, max_length=240)
    upload_id: UUID
    version_id: UUID | None = None
    project_id: UUID | None = None
    block_id: UUID | None = None


class BlockTrackingConfigInput(ContractModel):
    enabled: bool = True
    comparison_kind: Literal["previous_project", "previous_version"] = "previous_project"
    baseline_project_id: UUID | None = None
    baseline_block_id: UUID | None = None
    baseline_version_id: UUID | None = None
    design_stage: str | None = Field(default=None, max_length=200)
    required_corners: list[CornerSpec] = Field(default_factory=list)
    recipients: list[str] = Field(default_factory=list)
    approved_baselines: list[ApprovedBaselineRef] = Field(default_factory=list)
    approved_mapping_id: UUID | None = None
    no_change_net_vers: list[str] = Field(default_factory=list)
    expected_row_version: int | None = Field(default=None, ge=1)

    @field_validator("design_stage")
    @classmethod
    def check_stage(cls, value):
        return _clean_text(value, max_len=200)

    @field_validator("recipients")
    @classmethod
    def check_recipients(cls, value):
        return list(dict.fromkeys(email_address(item) for item in value))

    @field_validator("no_change_net_vers")
    @classmethod
    def check_no_change(cls, value):
        cleaned = []
        for item in value:
            text = _clean_text(item, max_len=200)
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @model_validator(mode="after")
    def unique_corners(self):
        cids = [corner.corner_id() for corner in self.required_corners]
        if len(cids) != len(set(cids)):
            raise ValueError("required_corners must be unique")
        bcids = [ref.corner_id for ref in self.approved_baselines]
        if len(bcids) != len(set(bcids)):
            raise ValueError("approved_baselines must have unique corner_id entries")
        return self


class RoundInput(ContractModel):
    net_ver: str = Field(min_length=1, max_length=200)
    netlist_arrived_at: date
    target_block_ids: list[UUID] = Field(min_length=1)
    deadline_days: int | None = Field(default=None, ge=1, le=90)
    deadline_time: str | None = None
    deadline_timezone: str | None = None
    expected_row_version: int | None = Field(default=None, ge=1)

    @field_validator("net_ver")
    @classmethod
    def check_net_ver(cls, value):
        cleaned = _clean_text(value)
        if not cleaned:
            raise ValueError("net_ver is required")
        return cleaned

    @field_validator("deadline_time")
    @classmethod
    def check_time(cls, value):
        if value is None:
            return None
        cleaned = _clean_text(value, max_len=10)
        if not cleaned or len(cleaned) != 5 or cleaned[2] != ":":
            raise ValueError("deadline_time must use HH:MM format")
        hour, minute = int(cleaned[:2]), int(cleaned[3:])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("deadline_time must be a valid 24-hour clock time")
        return cleaned

    @field_validator("deadline_timezone")
    @classmethod
    def check_tz(cls, value):
        if value is None:
            return None
        try:
            ZoneInfo(value.strip())
        except ZoneInfoNotFoundError:
            raise ValueError("deadline_timezone must be a valid IANA timezone") from None
        return value.strip()

    @model_validator(mode="after")
    def unique_blocks(self):
        if len(set(self.target_block_ids)) != len(self.target_block_ids):
            raise ValueError("target_block_ids must be unique")
        return self


class ExpectationInput(ContractModel):
    effective_net_ver: str | None = Field(default=None, max_length=200)
    target_paths: list[str] = Field(min_length=1)
    scope: Literal["node", "subtree"] = "subtree"
    metric: Literal["logic_area", "pwr_all_total_mw"]
    comparison_condition: Literal["typical", "same_condition", "any"] = "any"
    direction: Literal["increase", "decrease"]
    min_pct: Decimal | None = None
    max_pct: Decimal | None = None
    quote: str = Field(default="", max_length=1000)
    source_note_id: UUID | None = None
    archived: bool = False
    expected_row_version: int | None = Field(default=None, ge=1)

    @field_validator("effective_net_ver")
    @classmethod
    def check_net_ver(cls, value):
        return _clean_text(value, max_len=200)

    @field_validator("target_paths")
    @classmethod
    def check_paths(cls, value):
        cleaned = []
        for path in value:
            text = _clean_text(path, max_len=1000)
            if not text:
                raise ValueError("target_paths must be nonblank")
            if text not in cleaned:
                cleaned.append(text)
        return cleaned

    @field_validator("min_pct", "max_pct")
    @classmethod
    def check_pct(cls, value):
        return finite(value, nonnegative=True) if value is not None else None

    @model_validator(mode="after")
    def check_bounds(self):
        if self.min_pct is not None and self.max_pct is not None and self.min_pct > self.max_pct:
            raise ValueError("min_pct cannot exceed max_pct")
        return self


class ExpectationProposalInput(ContractModel):
    request_id: UUID
    effective_net_ver: str | None = Field(default=None, max_length=200)
    source_text: str = Field(min_length=1, max_length=20000)
    source_note_id: UUID | None = None


class ExpectationApprovalInput(ContractModel):
    selected: list[int] = Field(min_length=1)


def round_dedupe_key(project_id: UUID, net_ver: str) -> bytes:
    return digest(["tracking_round", str(project_id), net_ver])


def slot_dedupe_key(round_id: UUID, block_id: UUID, corner_id: str) -> bytes:
    return digest(["tracking_slot", str(round_id), str(block_id), corner_id])


def top_mail_dedupe_key(round_id: UUID) -> bytes:
    return digest(["tracking_top_mail", str(round_id)])


def block_mail_dedupe_key(round_id: UUID, block_id: UUID, corner_signatures: list[tuple[str, str, str]]) -> bytes:
    return digest(["tracking_block_mail", str(round_id), str(block_id), sorted(corner_signatures)])
