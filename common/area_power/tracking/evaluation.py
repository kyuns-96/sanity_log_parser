"""Pure tracking evaluation rules: deadlines, expectations, power targets, typical/same-condition."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .models import (
    CornerSpec,
    DEFAULT_DEADLINE_DAYS,
    DEFAULT_DEADLINE_TIME,
    DEFAULT_DEADLINE_TIMEZONE,
    upload_corner_label,
    upload_corner_tuple,
)

D = Decimal


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
    """Pick the spec whose effective_net_ver applies to current_net_ver."""
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
    """Evaluate all approved expectations applicable to one card's paths and metrics."""
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
