"""Pure Phase 2 domain models and rules for NET_VER automatic tracking and result mails.

No DB queries, HTTP calls, or DDL. Shared by OA APIs, central VWP automation,
and OA mail rendering.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from area_power.common.jsonutil import digest
from area_power.common.models import ContractModel, email_address, finite
from area_power.common.triage import Thresholds

D = Decimal

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


def is_full_chip_block(project_name: str | None, block_name: str | None) -> bool:
    """Within a project, a registered block whose name equals the project name is Full-chip TOP."""
    left = (project_name or "").strip()
    right = (block_name or "").strip()
    return bool(left and right and left == right)


def compute_deadline(
    arrived_on: date,
    deadline_days: int = DEFAULT_DEADLINE_DAYS,
    deadline_time: str = DEFAULT_DEADLINE_TIME,
    deadline_timezone: str = DEFAULT_DEADLINE_TIMEZONE,
) -> dict[str, Any]:
    """Compute exact UTC deadline timestamp and display metadata from actual netlist arrival date."""
    tz = ZoneInfo(deadline_timezone)
    hour, minute = int(deadline_time[:2]), int(deadline_time[3:])
    due_date = arrived_on + timedelta(days=deadline_days)
    local_dt = datetime.combine(due_date, time(hour=hour, minute=minute), tzinfo=tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    return {
        "netlist_arrived_at": arrived_on.isoformat(),
        "deadline_date": due_date.isoformat(),
        "deadline_time": deadline_time,
        "deadline_timezone": deadline_timezone,
        "deadline_local": local_dt.isoformat(),
        "deadline_at": utc_dt.isoformat(),
    }


def effective_corners(project_config: dict[str, Any] | None, block_config: dict[str, Any] | None) -> list[CornerSpec]:
    """Block required corners override project defaults; otherwise inherit project defaults."""
    raw = (block_config or {}).get("required_corners") or (project_config or {}).get("default_required_corners") or []
    return [CornerSpec.model_validate(item) for item in raw]


def resolve_versioned_spec(
    specs: list[dict[str, Any]],
    current_net_ver: str,
    net_ver_order: list[str] | None = None,
) -> dict[str, Any] | None:
    """Pick the spec whose effective_net_ver applies to current_net_ver.

    Uses explicit NET_VER ordering when available (from version lineage / rounds);
    otherwise matches exact NET_VER or falls back to the sole/latest registered
    spec before/at current_net_ver without guessing unrelated names.
    """
    if not specs:
        return None
    by_net = {item.get("effective_net_ver"): item for item in specs if item.get("effective_net_ver")}
    if current_net_ver in by_net:
        return by_net[current_net_ver]
    if net_ver_order and current_net_ver in net_ver_order:
        cutoff = net_ver_order.index(current_net_ver)
        applicable = [by_net[nv] for nv in net_ver_order[: cutoff + 1] if nv in by_net]
        if applicable:
            return applicable[-1]
    # If the user configured a single default/baseline spec (e.g. from the initial round),
    # or earlier round entries, return None if an explicit order excludes it, else last known prior.
    if net_ver_order is None and len(specs) == 1:
        return specs[0]
    return None


def evaluate_power_target(current_mw: Decimal | None, target_mw: Decimal | None) -> dict[str, Any]:
    if target_mw is None:
        return {"status": "unconfigured", "target_mw": None, "current_mw": current_mw, "exceeded_mw": None}
    if current_mw is None:
        return {"status": "unavailable", "target_mw": target_mw, "current_mw": None, "exceeded_mw": None}
    diff = current_mw - target_mw
    if diff > 0:
        return {"status": "exceeded", "target_mw": target_mw, "current_mw": current_mw, "exceeded_mw": diff}
    return {"status": "met", "target_mw": target_mw, "current_mw": current_mw, "exceeded_mw": D(0)}


def _path_matches_expectation(path: str, target_paths: list[str], scope: str, separator: str = "/") -> bool:
    for target in target_paths:
        if path == target:
            return True
        if scope == "subtree" and path.startswith(target + separator):
            return True
    return False


def evaluate_single_expectation(delta_pct: Decimal | None, expectation: dict[str, Any]) -> dict[str, Any]:
    """Compare observed percentage change against one human-approved expected change."""
    if delta_pct is None:
        return {"status": "unavailable", "expectation_id": expectation.get("id"), "quote": expectation.get("quote", "")}
    direction = expectation.get("direction")
    min_pct = D(str(expectation["min_pct"])) if expectation.get("min_pct") is not None else None
    max_pct = D(str(expectation["max_pct"])) if expectation.get("max_pct") is not None else None
    if direction == "increase":
        if delta_pct <= 0:
            return {"status": "direction_mismatch", "expectation_id": expectation.get("id"), "quote": expectation.get("quote", "")}
        magnitude = delta_pct
    elif direction == "decrease":
        if delta_pct >= 0:
            return {"status": "direction_mismatch", "expectation_id": expectation.get("id"), "quote": expectation.get("quote", "")}
        magnitude = -delta_pct
    else:
        return {"status": "unavailable", "expectation_id": expectation.get("id"), "quote": expectation.get("quote", "")}

    if min_pct is None and max_pct is None:
        return {"status": "direction_match", "expectation_id": expectation.get("id"), "quote": expectation.get("quote", "")}
    if min_pct is not None and magnitude < min_pct:
        return {
            "status": "under_expected",
            "expectation_id": expectation.get("id"),
            "min_pct": min_pct,
            "max_pct": max_pct,
            "observed_magnitude_pct": magnitude,
            "quote": expectation.get("quote", ""),
        }
    if max_pct is not None and magnitude > max_pct:
        return {
            "status": "over_expected",
            "expectation_id": expectation.get("id"),
            "min_pct": min_pct,
            "max_pct": max_pct,
            "observed_magnitude_pct": magnitude,
            "quote": expectation.get("quote", ""),
        }
    return {
        "status": "within_range",
        "expectation_id": expectation.get("id"),
        "min_pct": min_pct,
        "max_pct": max_pct,
        "observed_magnitude_pct": magnitude,
        "quote": expectation.get("quote", ""),
    }


def match_approved_expectations(
    card: dict[str, Any],
    expectations: list[dict[str, Any]],
    *,
    comparison_condition: str = "same_condition",
    separator: str = "/",
) -> dict[str, Any]:
    """Evaluate all approved expectations applicable to one card's paths and metrics.

    Expectations apply strictly to their approved target paths, metric, and condition.
    Other metrics, other instances, and power target exceedance remain independent.
    """
    current_path = card.get("current_path") or ""
    paths = [p.strip() for p in current_path.split(" + ") if p.strip()]
    if not paths:
        return {"matches": {}, "fully_explained": False, "evidence": []}

    matches: dict[str, list[dict[str, Any]]] = {"logic_area": [], "pwr_all_total_mw": []}
    for exp in expectations:
        if exp.get("archived"):
            continue
        cond = exp.get("comparison_condition") or "any"
        if cond != "any" and cond != comparison_condition:
            continue
        metric = exp.get("metric")
        if metric not in matches:
            continue
        target_paths = exp.get("target_paths") or []
        scope = exp.get("scope") or "subtree"
        if not any(_path_matches_expectation(path, target_paths, scope, separator) for path in paths):
            continue
        if metric == "logic_area":
            area = card.get("area") or {}
            delta_pct = area.get("delta_pct") if "excluded" not in area else None
        else:
            power = card.get("power_mw") or {}
            delta_pct = power.get("delta_pct") if "excluded" not in power else None
        if delta_pct is not None and not isinstance(delta_pct, Decimal):
            delta_pct = D(str(delta_pct))
        outcome = evaluate_single_expectation(delta_pct, exp)
        outcome["metric"] = metric
        outcome["direction"] = exp.get("direction")
        matches[metric].append(outcome)

    reasons = list(card.get("reasons") or [])
    if not reasons:
        return {"matches": matches, "fully_explained": False, "evidence": []}

    reason_to_metric = {"area_increase": "logic_area", "power_increase": "pwr_all_total_mw"}
    evidence = []
    all_explained = True
    for reason in reasons:
        metric = reason_to_metric.get(reason)
        if metric is None:
            # new_instance or other non-percentage reason cannot be dismissed solely by a pct range
            all_explained = False
            continue
        hits = [item for item in matches[metric] if item["status"] == "within_range"]
        if hits:
            evidence.extend(hits)
        else:
            all_explained = False
    return {"matches": matches, "fully_explained": all_explained, "evidence": evidence}


def evaluate_typical_and_same_condition(
    *,
    current_typical: dict[str, Any] | None,
    baseline_typical: dict[str, Any] | None,
    same_condition_current: dict[str, Any] | None = None,
    same_condition_baseline: dict[str, Any] | None = None,
    power_target_mw: Decimal | None = None,
    expectations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Separate representative Typical power comparison from exact same-condition comparison."""
    def _root_power(upload: dict[str, Any] | None) -> Decimal | None:
        if not upload:
            return None
        metrics = (upload.get("root_summary") or {}).get("metrics") or {}
        val = metrics.get("pwr_all_total_mw")
        return D(str(val)) if val is not None else None

    cur_mw = _root_power(current_typical)
    base_mw = _root_power(baseline_typical)
    typ_delta = (cur_mw - base_mw) if (cur_mw is not None and base_mw is not None) else None
    typ_pct = (typ_delta / base_mw * D(100)).quantize(D("0.01")) if (typ_delta is not None and base_mw) else None

    condition_changes: list[str] = []
    same_typical_conditions = False
    if current_typical and baseline_typical:
        c_t, b_t = upload_corner_tuple(current_typical), upload_corner_tuple(baseline_typical)
        if b_t[0] != c_t[0]:
            condition_changes.append(f"공정 corner {b_t[0] or '?'} → {c_t[0] or '?'}")
        if b_t[1] != c_t[1]:
            condition_changes.append(f"전압 {b_t[1] or '?'} V → {c_t[1] or '?'} V")
        if b_t[2] != c_t[2]:
            condition_changes.append(f"온도 {b_t[2] or '?'} °C → {c_t[2] or '?'} °C")
        if b_t[3] != c_t[3]:
            condition_changes.append(f"PEX {b_t[3] or '?'} → {c_t[3] or '?'}")
        same_typical_conditions = (
            bool(current_typical.get("conditions_complete"))
            and bool(baseline_typical.get("conditions_complete"))
            and bytes(current_typical.get("condition_key") or b"") == bytes(baseline_typical.get("condition_key") or b"")
        )
        if not condition_changes and not same_typical_conditions:
            if not current_typical.get("conditions_complete") or not baseline_typical.get("conditions_complete"):
                condition_changes.append("분석 조건 미확인")
            else:
                condition_changes.append("분석 조건(mode/클록/activity 등) 다름")

    target_eval = evaluate_power_target(cur_mw, power_target_mw)

    # Evaluate root Typical expectations if supplied
    typ_expectation = None
    if expectations and typ_pct is not None:
        root_name = ((current_typical or {}).get("root_summary") or {}).get("hier_name") or ""
        for exp in expectations:
            if exp.get("archived") or exp.get("metric") != "pwr_all_total_mw":
                continue
            if (exp.get("comparison_condition") or "any") not in ("typical", "any"):
                continue
            targets = exp.get("target_paths") or []
            if not targets or (root_name and _path_matches_expectation(root_name, targets, exp.get("scope") or "subtree")):
                typ_expectation = evaluate_single_expectation(typ_pct, exp)
                break

    # Same-condition pair evaluation
    sc_cur = same_condition_current or (current_typical if same_typical_conditions else None)
    sc_base = same_condition_baseline or (baseline_typical if same_typical_conditions else None)
    if sc_cur is None or sc_base is None:
        if current_typical and baseline_typical and (
            not current_typical.get("conditions_complete") or not baseline_typical.get("conditions_complete")
        ) and upload_corner_tuple(current_typical) == upload_corner_tuple(baseline_typical):
            sc_status = "conditions_unconfirmed"
        else:
            sc_status = "no_same_condition_data"
        same_cond = {
            "status": sc_status,
            "current_upload_id": sc_cur["id"] if sc_cur else None,
            "baseline_upload_id": sc_base["id"] if sc_base else None,
            "current_mw": _root_power(sc_cur),
            "baseline_mw": _root_power(sc_base),
            "delta_mw": None,
            "delta_pct": None,
        }
    else:
        sc_complete = bool(sc_cur.get("conditions_complete") and sc_base.get("conditions_complete"))
        sc_same_key = bytes(sc_cur.get("condition_key") or b"") == bytes(sc_base.get("condition_key") or b"")
        sc_cmw, sc_bmw = _root_power(sc_cur), _root_power(sc_base)
        sc_delta = (sc_cmw - sc_bmw) if (sc_cmw is not None and sc_bmw is not None) else None
        sc_pct = (sc_delta / sc_bmw * D(100)).quantize(D("0.01")) if (sc_delta is not None and sc_bmw) else None
        same_cond = {
            "status": "comparable" if (sc_complete and sc_same_key) else "conditions_unconfirmed",
            "current_upload_id": sc_cur["id"],
            "baseline_upload_id": sc_base["id"],
            "current_mw": sc_cmw,
            "baseline_mw": sc_bmw,
            "delta_mw": sc_delta,
            "delta_pct": sc_pct,
        }

    return {
        "typical": {
            "current_upload_id": current_typical["id"] if current_typical else None,
            "baseline_upload_id": baseline_typical["id"] if baseline_typical else None,
            "current_corner": upload_corner_label(current_typical) if current_typical else None,
            "baseline_corner": upload_corner_label(baseline_typical) if baseline_typical else None,
            "current_mw": cur_mw,
            "baseline_mw": base_mw,
            "delta_mw": typ_delta,
            "delta_pct": typ_pct,
            "condition_changes": condition_changes,
            "same_conditions": same_typical_conditions,
            "expectation": typ_expectation,
            "target": target_eval,
        },
        "same_condition": same_cond,
    }


def _fmt_num(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    text = format(value, "f") if isinstance(value, Decimal) and value.is_finite() else str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text + suffix


def _fmt_signed(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    dec = value if isinstance(value, Decimal) else D(str(value))
    prefix = "+" if dec > 0 else ""
    return prefix + _fmt_num(dec, suffix)


def render_block_mail_text(snapshot: dict[str, Any], *, mail_id: str | None = None) -> tuple[str, str]:
    """Build subject and plain-text body for one block owner's NET_VER result mail."""
    project = snapshot.get("project_name") or "프로젝트"
    block = snapshot.get("block_name") or "블록"
    net_ver = snapshot.get("net_ver") or ""
    revised = bool(snapshot.get("revised"))
    post_top = bool(snapshot.get("post_top_mail_revised"))
    tag = " (TOP 발송 후 수정본)" if post_top else (" (수정본)" if revised else "")
    totals = snapshot.get("totals") or {}
    subject = " ".join(
        f"[Design Atlas] {project} · {block} · {net_ver}{tag} · 이상 {totals.get('unexpected', 0)}건 · 확인 필요 {totals.get('needs_review', 0)}건".split()
    )[:200]

    lines = [
        f"Design Atlas NET_VER 자동 추적 블록 결과입니다.{tag}",
        "",
        f"프로젝트: {project}",
        f"블록: {block}" + (" (Full-chip)" if snapshot.get("is_full_chip") else ""),
        f"NET_VER: {net_ver}" + (f" · REVISION {snapshot['revision_label']}" if snapshot.get("revision_label") else ""),
        f"결과 요약: 이상 {totals.get('unexpected', 0)}건 · 확인 필요 {totals.get('needs_review', 0)}건 · 설명됨 {totals.get('expected', 0)}건(코드 대조 {totals.get('code_explained', 0)}건) · 변화 미미 {totals.get('minor', 0)}건",
    ]
    power_eval = snapshot.get("power_evaluation") or {}
    typ = power_eval.get("typical") or {}
    target = typ.get("target") or {}
    if typ.get("current_mw") is not None:
        tline = f"대표 전력(Typical): {_fmt_num(typ.get('baseline_mw'), ' mW')} → {_fmt_num(typ.get('current_mw'), ' mW')} ({_fmt_signed(typ.get('delta_pct'), '%')})"
        if typ.get("condition_changes"):
            tline += " · 조건 변경: " + ", ".join(typ["condition_changes"])
        lines.append(tline)
    if target.get("status") == "exceeded":
        lines.append(f"목표 전력 초과: 목표 {_fmt_num(target.get('target_mw'), ' mW')} 대비 {_fmt_num(target.get('exceeded_mw'), ' mW')} 초과")
    elif target.get("status") == "met":
        lines.append(f"목표 전력 달성: 목표 {_fmt_num(target.get('target_mw'), ' mW')} 이하")

    sc = power_eval.get("same_condition") or {}
    if sc.get("status") == "comparable" and not typ.get("same_conditions"):
        lines.append(f"동일 조건 전력: {_fmt_num(sc.get('baseline_mw'), ' mW')} → {_fmt_num(sc.get('current_mw'), ' mW')} ({_fmt_signed(sc.get('delta_pct'), '%')})")
    elif sc.get("status") == "conditions_unconfirmed":
        lines.append("동일 조건 전력: 조건 미확인")
    elif sc.get("status") == "no_same_condition_data" and not typ.get("same_conditions"):
        lines.append("동일 조건 전력: 동일 조건 데이터 없음")

    for corner in snapshot.get("corners") or []:
        lines += [
            "",
            f"■ Corner: {corner.get('corner_label') or corner.get('corner_id')}",
            f"  Logic Area: {_fmt_num(corner.get('logic_area_baseline'))} → {_fmt_num(corner.get('logic_area_current'))} ({_fmt_signed(corner.get('logic_area_delta_pct'), '%')})",
            f"  Total Power: {_fmt_num(corner.get('power_baseline_mw'), ' mW')} → {_fmt_num(corner.get('power_current_mw'), ' mW')} ({_fmt_signed(corner.get('power_delta_pct'), '%')})",
            f"  설명됨 {corner.get('expected_count', 0)}건 · 이상 {corner.get('unexpected_count', 0)}건 · 확인 필요 {corner.get('needs_review_count', 0)}건",
        ]
        highlights = corner.get("highlights") or []
        if highlights:
            for item in highlights[:25]:
                lines.append(
                    f"  - [{item.get('verdict')}] {item.get('path')} · Logic Area {_fmt_signed(item.get('logic_area_delta_pct'), '%')} · Power {_fmt_signed(item.get('power_delta_pct'), '%')} · {item.get('explanation', '')}"
                )
        else:
            lines.append("  검토가 필요한 이상 항목 없음")

    if mail_id:
        lines += ["", f"발송 당시 고정 결과 링크: /?page=area-power&tracking_mail={mail_id}"]
    lines += [
        "최신 현황에서 후속 수정본 여부를 함께 확인할 수 있습니다.",
        "이 메일은 검토 대상을 알리는 것이며 정상·이상의 최종 판정이 아닙니다.",
    ]
    return subject, "\n".join(lines)


def render_top_mail_text(snapshot: dict[str, Any], *, mail_id: str | None = None) -> tuple[str, str]:
    """Build subject and plain-text body for one project × NET_VER TOP summary mail."""
    project = snapshot.get("project_name") or "프로젝트"
    net_ver = snapshot.get("net_ver") or ""
    trigger = "전체 분석 완료" if snapshot.get("trigger") == "all_completed" else "마감 도달"
    full_chip = snapshot.get("full_chip")
    sub_counts = snapshot.get("sub_block_counts") or {}
    fc_counts = snapshot.get("full_chip_counts") or {}

    subject = " ".join(
        f"[Design Atlas] TOP 요약 · {project} · {net_ver} · {trigger} · 하위 블록 완료 {sub_counts.get('completed', 0)}/{sub_counts.get('total', 0)}".split()
    )[:200]

    lines = [
        f"Design Atlas NET_VER 자동 추적 TOP 요약입니다. ({trigger})",
        "",
        f"프로젝트: {project} · NET_VER: {net_ver}",
        f"netlist 도착일: {snapshot.get('netlist_arrived_at') or '—'} · 마감: {snapshot.get('deadline_display') or snapshot.get('deadline_at') or '—'}",
        f"Full-chip: 도착 {fc_counts.get('arrived', 0)}/{fc_counts.get('total', 0)} · 분석 완료 {fc_counts.get('completed', 0)}/{fc_counts.get('total', 0)}",
        f"하위 블록: 도착 {sub_counts.get('arrived', 0)}/{sub_counts.get('total', 0)} · 분석 완료 {sub_counts.get('completed', 0)}/{sub_counts.get('total', 0)}",
    ]

    if full_chip and full_chip.get("arrived_corners", 0) > 0:
        typ = (full_chip.get("power_evaluation") or {}).get("typical") or {}
        target = typ.get("target") or {}
        if typ.get("current_mw") is not None:
            fc_line = f"Full-chip 대표 전력: {_fmt_num(typ.get('current_mw'), ' mW')}"
            if typ.get("delta_pct") is not None:
                fc_line += f" (기준 대비 {_fmt_signed(typ.get('delta_pct'), '%')})"
            if target.get("status") == "exceeded":
                fc_line += f" · 목표 {_fmt_num(target.get('target_mw'), ' mW')} 대비 {_fmt_num(target.get('exceeded_mw'), ' mW')} 초과"
            elif target.get("status") == "met":
                fc_line += f" · 목표 {_fmt_num(target.get('target_mw'), ' mW')} 달성"
            if typ.get("condition_changes"):
                fc_line += " · " + ", ".join(typ["condition_changes"])
            lines.append(fc_line)
        else:
            lines.append("Full-chip: 데이터 도착(대표 전력 계산 대기)")
    else:
        lines.append("Full-chip 미도착 · 전체 목표 판정 대기")

    lines += ["", "■ 대상 블록 요약 (주의 필요 블록 우선)"]
    rows = list(snapshot.get("blocks") or [])
    # Attention-needed blocks first
    rows.sort(key=lambda row: (0 if row.get("needs_attention") else 1, 0 if row.get("is_full_chip") else 1, row.get("block_name") or ""))
    for row in rows:
        name = row.get("block_name") or str(row.get("block_id"))
        if row.get("is_full_chip"):
            name = f"{project} · Full-chip"
        rev = f" · {row['revision_label']}" if row.get("revision_label") else ""
        lines.append(
            f"- {name}{rev} | 필수 corner 도착 {row.get('arrived_corners', 0)}/{row.get('required_corners', 0)} | "
            f"분석 완료 {row.get('completed_corners', 0)}/{row.get('required_corners', 0)} | {row.get('status_summary') or '대기'}"
        )

    if mail_id:
        lines += ["", f"발송 당시 고정 요약 링크: /?page=area-power&tracking_mail={mail_id}"]
    lines += [
        "이 요약은 발송 요청 시점의 결과를 고정한 것입니다. 이후 도착한 수정본은 현황에서 '발송 후 수정'으로 확인할 수 있습니다.",
    ]
    return subject, "\n".join(lines)


def round_dedupe_key(project_id: UUID, net_ver: str) -> bytes:
    return digest(["tracking_round", str(project_id), net_ver])


def slot_dedupe_key(round_id: UUID, block_id: UUID, corner_id: str) -> bytes:
    return digest(["tracking_slot", str(round_id), str(block_id), corner_id])


def top_mail_dedupe_key(round_id: UUID) -> bytes:
    return digest(["tracking_top_mail", str(round_id)])


def block_mail_dedupe_key(round_id: UUID, block_id: UUID, corner_signatures: list[tuple[str, str, str]]) -> bytes:
    return digest(["tracking_block_mail", str(round_id), str(block_id), sorted(corner_signatures)])
