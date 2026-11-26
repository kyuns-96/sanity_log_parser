"""Shared block tracking aggregation and TOP/block mail snapshot builders for OA & VWP."""
from __future__ import annotations

from typing import Any

from .evaluation import effective_corners, is_full_chip_block, resolve_versioned_spec
from .models import CornerSpec


def aggregate_block_tracking_row(
    *,
    project_id: Any,
    project_name: str,
    block_id: Any,
    block_name: str,
    net_ver: str,
    proj_cfg: dict[str, Any],
    block_cfg: dict[str, Any],
    slots_by_corner: dict[str, dict[str, Any]],
    auth_error_label: str = "인증 만료/권한 오류",
) -> dict[str, Any]:
    """Aggregate required corners, slot summaries, revisions, ECOs, and attention flags for one block."""
    full_chip = is_full_chip_block(project_name, block_name)
    req_corners = effective_corners(proj_cfg, block_cfg)
    typical_spec = resolve_versioned_spec(proj_cfg.get("typical_specs") or [], net_ver)
    typical_cid = (
        CornerSpec.model_validate(typical_spec["corner"]).corner_id()
        if typical_spec and typical_spec.get("corner")
        else None
    )

    corner_rows: list[dict[str, Any]] = []
    missing_labels: list[str] = []
    arrived = 0
    completed = 0
    totals = {"unexpected": 0, "needs_review": 0, "expected": 0, "minor": 0, "code_explained": 0}
    revisions: set[str] = set()
    eco_labels: set[str] = set()
    post_top_revised = False
    issues: list[str] = []
    typical_power_eval = None

    for corner in req_corners:
        cid = corner.corner_id()
        slot = slots_by_corner.get(cid)
        if not slot or not slot["payload"].get("latest_upload_id"):
            missing_labels.append(corner.label)
            corner_rows.append({
                "corner_id": cid,
                "corner_label": corner.label,
                "status": "waiting_upload",
                "upload_id": None,
                "comparison_id": None,
                "ai_run_id": None,
            })
            continue
        sp = slot["payload"]
        arrived += 1
        if sp.get("revision_label"):
            revisions.add(sp["revision_label"])
        if sp.get("eco_label"):
            eco_labels.add(sp["eco_label"])
        post_top_revised |= bool(sp.get("post_top_mail_revised"))
        status = sp.get("status") or "queued"
        summary = sp.get("summary") or {}
        c_counts = summary.get("counts") or {}
        if status == "completed":
            completed += 1
            for k in totals:
                totals[k] += int(c_counts.get(k) or 0)
        elif status == "mapping_review":
            issues.append(f"{corner.label} 매핑 확인 필요")
        elif status == "waiting_baseline":
            issues.append(f"{corner.label} 기준 실행 미승인")
        elif status == "auth_error":
            issues.append(f"{corner.label} {auth_error_label}")
        elif status == "failed":
            issues.append(f"{corner.label} 분석 실패")
        else:
            issues.append(f"{corner.label} 분석 중")
        if sp.get("last_failed_upload"):
            issues.append(f"{corner.label} 수정본 검증 실패(직전 정상본 유지)")
        if summary.get("power_evaluation") and (typical_power_eval is None or cid == typical_cid):
            typical_power_eval = summary["power_evaluation"]

        corner_rows.append({
            "slot_id": slot["id"],
            "corner_id": cid,
            "corner_label": corner.label,
            "status": status,
            "status_reason": sp.get("status_reason"),
            "upload_id": sp.get("latest_upload_id"),
            "baseline_upload_id": sp.get("baseline_upload_id"),
            "comparison_id": sp.get("comparison_id"),
            "ai_run_id": sp.get("ai_run_id"),
            "mapping_check": sp.get("mapping_check"),
            "last_failed_upload": sp.get("last_failed_upload"),
            "post_top_mail_revised": bool(sp.get("post_top_mail_revised")),
            "logic_area_baseline": summary.get("logic_area_baseline"),
            "logic_area_current": summary.get("logic_area_current"),
            "logic_area_delta_pct": summary.get("logic_area_delta_pct"),
            "power_baseline_mw": summary.get("power_baseline_mw"),
            "power_current_mw": summary.get("power_current_mw"),
            "power_delta_pct": summary.get("power_delta_pct"),
            "counts": c_counts,
            "expected_count": c_counts.get("expected", 0),
            "unexpected_count": c_counts.get("unexpected", 0),
            "needs_review_count": c_counts.get("needs_review", 0),
            "code_explained_count": c_counts.get("code_explained", 0),
            "attribution": summary.get("attribution"),
            "highlights": summary.get("highlights") or [],
        })

    target_status = ((typical_power_eval or {}).get("typical") or {}).get("target") or {}
    parts: list[str] = []
    if full_chip and typical_power_eval:
        typ = typical_power_eval.get("typical") or {}
        if typ.get("current_mw") is not None:
            parts.append(f"대표 전력 {typ['current_mw']} mW")
        if target_status.get("status") == "exceeded":
            parts.append(f"목표 {target_status['exceeded_mw']} mW 초과")
        elif target_status.get("status") == "met":
            parts.append("목표 달성")
    if missing_labels:
        parts.append(", ".join(f"{lbl} 미도착" for lbl in missing_labels))
    if issues:
        parts.extend(issues)
    if not parts and completed == len(req_corners) and req_corners:
        if totals["unexpected"] or totals["needs_review"]:
            parts.append(f"이상 {totals['unexpected']}건 · 확인 필요 {totals['needs_review']}건")
        else:
            parts.append(f"완료 · 설명됨 {totals['expected']}건")
    if post_top_revised:
        parts.append("발송 후 수정")

    needs_attention = bool(
        missing_labels
        or issues
        or totals["unexpected"] > 0
        or totals["needs_review"] > 0
        or target_status.get("status") == "exceeded"
    )
    return {
        "project_id": project_id,
        "project_name": project_name,
        "block_id": block_id,
        "block_name": block_name,
        "is_full_chip": full_chip,
        "net_ver": net_ver,
        "revision_label": ", ".join(sorted(revisions)),
        "eco_label": ", ".join(sorted(eco_labels)),
        "required_corners": len(req_corners),
        "arrived_corners": arrived,
        "completed_corners": completed,
        "missing_corners": missing_labels,
        "all_completed": len(req_corners) > 0 and completed == len(req_corners),
        "totals": totals,
        "power_evaluation": typical_power_eval,
        "corners": corner_rows,
        "post_top_mail_revised": post_top_revised,
        "needs_attention": needs_attention,
        "status_summary": " · ".join(parts) or "대기",
    }


def compute_next_tracking_action(row: dict[str, Any]) -> dict[str, Any]:
    corners = row["corners"]
    full_chip = row["is_full_chip"]
    missing_labels = row["missing_corners"]
    completed_slot = next((c for c in corners if c["status"] == "completed" and c.get("comparison_id")), None)
    mapping_slot = next((c for c in corners if c["status"] == "mapping_review"), None)
    baseline_slot = next((c for c in corners if c["status"] == "waiting_baseline"), None)
    running_slot = next((c for c in corners if c["status"] in ("queued", "running")), None)
    if mapping_slot:
        return {
            "kind": "mapping",
            "label": "매핑 확인",
            "upload_id": mapping_slot.get("upload_id"),
            "baseline_upload_id": mapping_slot.get("baseline_upload_id"),
        }
    if baseline_slot:
        return {"kind": "configure", "label": "기준 실행 승인"}
    if completed_slot:
        lbl = (
            "Full-chip 결과 보기"
            if full_chip
            else (f"도착한 {completed_slot['corner_label']} 결과 보기" if missing_labels else "블록 결과 보기")
        )
        return {
            "kind": "result",
            "label": lbl,
            "comparison_id": completed_slot.get("comparison_id"),
            "ai_run_id": completed_slot.get("ai_run_id"),
            "upload_id": completed_slot.get("upload_id"),
            "baseline_upload_id": completed_slot.get("baseline_upload_id"),
        }
    if running_slot:
        return {
            "kind": "result",
            "label": "진행 상황 보기",
            "comparison_id": running_slot.get("comparison_id"),
            "ai_run_id": running_slot.get("ai_run_id"),
        }
    if not row["required_corners"]:
        return {"kind": "configure", "label": "필수 corner 지정"}
    return {"kind": "upload", "label": "CSV 도착 대기"}


def summarize_block_counts(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    full_chips = [b for b in blocks if b["is_full_chip"]]
    subs = [b for b in blocks if not b["is_full_chip"]]
    fc_counts = {
        "total": len(full_chips),
        "arrived": sum(b["arrived_corners"] > 0 for b in full_chips),
        "completed": sum(b["all_completed"] for b in full_chips),
    }
    sub_counts = {
        "total": len(subs),
        "arrived": sum(b["arrived_corners"] > 0 for b in subs),
        "completed": sum(b["all_completed"] for b in subs),
    }
    return full_chips, fc_counts, sub_counts


def build_live_round_mail_snapshots(project_name: str, chosen_round: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blocks = chosen_round["blocks"]
    full_chip = next((b for b in blocks if b["is_full_chip"]), None)
    top_snapshot = {
        "project_name": project_name,
        "net_ver": chosen_round["net_ver"],
        "trigger": "all_completed" if all(b["all_completed"] for b in blocks) and blocks else "deadline",
        "netlist_arrived_at": chosen_round["netlist_arrived_at"],
        "deadline_display": f"{chosen_round.get('deadline_date') or ''} {chosen_round.get('deadline_time') or ''} ({chosen_round.get('deadline_timezone') or ''})".strip(),
        "full_chip": full_chip,
        "full_chip_counts": chosen_round["full_chip_counts"],
        "sub_block_counts": chosen_round["sub_block_counts"],
        "blocks": blocks,
    }
    block_snapshots = [
        {
            "block_id": block["block_id"],
            "project_name": project_name,
            "block_name": block["block_name"],
            "is_full_chip": block["is_full_chip"],
            "net_ver": chosen_round["net_ver"],
            "revision_label": block["revision_label"],
            "eco_label": block.get("eco_label") or "",
            "totals": block["totals"],
            "power_evaluation": block["power_evaluation"],
            "corners": block["corners"],
            "revised": False,
            "post_top_mail_revised": block["post_top_mail_revised"],
        }
        for block in blocks
    ]
    return top_snapshot, block_snapshots
