"""OA NET_VER tracking overview, live block rows, mail status, and pre-activation preview."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from common.area_power.tracking import (
    CornerSpec,
    DEFAULT_DEADLINE_DAYS,
    DEFAULT_DEADLINE_TIME,
    DEFAULT_DEADLINE_TIMEZONE,
    effective_corners,
    is_full_chip_block,
    render_block_mail_text,
    render_top_mail_text,
    resolve_versioned_spec,
)
from common.area_power.triage import Thresholds
from common.sanity_common import ReviewError
from oa.sanity_ai_connections import ConnectionStore
from ..schemas import Input


def is_tracking_admin(service) -> bool:
    access = getattr(service, "access", None)
    return access is None or access.development or bool(access.is_admin)


def require_admin(service) -> None:
    if not is_tracking_admin(service):
        raise ReviewError("FORBIDDEN", "Administrator access is required for tracking configuration.")


def host_db(service):
    store = getattr(getattr(service.access, "actor", None), "access", None)
    return getattr(store, "db", None)


def latest_one(service, conn, kind: str, **filters) -> dict[str, Any] | None:
    rows = service.db.records(conn, kind, latest=True, limit=1, **filters)
    return rows[0] if rows else None


def mail_delivery_info(service, conn, mail_record: dict | None, *, include_recipients: bool = True) -> dict[str, Any]:
    if mail_record is None:
        return {"state": "waiting", "mail_id": None, "job_state": None, "phase": None, "error": None}
    jobs = service.db.records(conn, "job", parent_id=mail_record["id"], limit=1)
    job = jobs[0] if jobs else None
    emissions = list((job or {}).get("payload", {}).get("emissions") or [])
    last = emissions[-1] if emissions else {}
    phase = last.get("phase")
    if phase in ("accepted", "partially_accepted", "ambiguous", "rejected"):
        state = phase
    elif job and job["state"] == "failed":
        state = "failed"
    elif job and job["state"] in ("pending", "running"):
        state = "requested"
    else:
        state = mail_record.get("state") or "requested"
    info: dict[str, Any] = {
        "state": state,
        "mail_id": mail_record["id"],
        "trigger": mail_record["payload"].get("trigger"),
        "frozen_at": mail_record["payload"].get("snapshot", {}).get("frozen_at") or mail_record.get("created_at"),
        "job_id": job["id"] if job else None,
        "job_state": job["state"] if job else None,
        "phase": phase,
        "error": (job or {}).get("error_detail"),
    }
    if include_recipients:
        info["recipients"] = mail_record["payload"].get("recipients") or []
        info["accepted_recipients"] = last.get("accepted_recipients") or []
        info["rejected_recipients"] = last.get("rejected_recipients") or []
    return info


def _block_live_row(
    service, conn, round_row: dict, project_row: dict, block_id: UUID,
    proj_cfg: dict, block_cfgs: dict, *, include_recipients: bool = True,
) -> dict[str, Any]:
    block_row = service.db.record(conn, block_id, kind="block")
    block_cfg_row = block_cfgs.get(str(block_id))
    block_cfg = block_cfg_row["payload"] if block_cfg_row else {}
    full_chip = is_full_chip_block(project_row["payload"].get("name"), block_row["payload"].get("name"))
    req_corners = effective_corners(proj_cfg, block_cfg)
    slots = {
        s["payload"]["corner_id"]: s
        for s in service.all_records(conn, "tracking_slot", parent_id=round_row["id"], block_id=block_id)
    }

    corner_rows = []
    missing_labels = []
    arrived = 0
    completed = 0
    totals = {"unexpected": 0, "needs_review": 0, "expected": 0, "minor": 0, "code_explained": 0}
    revisions = set()
    post_top_revised = False
    issues = []
    typical_power_eval = None

    net_ver = round_row["payload"]["net_ver"]
    typical_spec = resolve_versioned_spec(proj_cfg.get("typical_specs") or [], net_ver)
    typical_cid = CornerSpec.model_validate(typical_spec["corner"]).corner_id() if typical_spec and typical_spec.get("corner") else None

    for corner in req_corners:
        cid = corner.corner_id()
        slot = slots.get(cid)
        if not slot or not slot["payload"].get("latest_upload_id"):
            missing_labels.append(corner.label)
            corner_rows.append({
                "corner_id": cid, "corner_label": corner.label, "status": "waiting_upload",
                "upload_id": None, "comparison_id": None, "ai_run_id": None,
            })
            continue
        sp = slot["payload"]
        arrived += 1
        if sp.get("revision_label"):
            revisions.add(sp["revision_label"])
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
            issues.append(f"{corner.label} 인증 만료/권한 오류")
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
            "logic_area_delta_pct": summary.get("logic_area_delta_pct"),
            "power_current_mw": summary.get("power_current_mw"),
            "power_delta_pct": summary.get("power_delta_pct"),
            "counts": c_counts,
        })

    completed_slot = next((c for c in corner_rows if c["status"] == "completed" and c.get("comparison_id")), None)
    mapping_slot = next((c for c in corner_rows if c["status"] == "mapping_review"), None)
    baseline_slot = next((c for c in corner_rows if c["status"] == "waiting_baseline"), None)
    running_slot = next((c for c in corner_rows if c["status"] in ("queued", "running")), None)
    if mapping_slot:
        next_action = {"kind": "mapping", "label": "매핑 확인", "upload_id": mapping_slot.get("upload_id"), "baseline_upload_id": mapping_slot.get("baseline_upload_id")}
    elif baseline_slot:
        next_action = {"kind": "configure", "label": "기준 실행 승인"}
    elif completed_slot:
        lbl = "Full-chip 결과 보기" if full_chip else (f"도착한 {completed_slot['corner_label']} 결과 보기" if missing_labels else "블록 결과 보기")
        next_action = {"kind": "result", "label": lbl, "comparison_id": completed_slot.get("comparison_id"), "ai_run_id": completed_slot.get("ai_run_id"), "upload_id": completed_slot.get("upload_id"), "baseline_upload_id": completed_slot.get("baseline_upload_id")}
    elif running_slot:
        next_action = {"kind": "result", "label": "진행 상황 보기", "comparison_id": running_slot.get("comparison_id"), "ai_run_id": running_slot.get("ai_run_id")}
    elif not req_corners:
        next_action = {"kind": "configure", "label": "필수 corner 지정"}
    else:
        next_action = {"kind": "upload", "label": "CSV 도착 대기"}

    target_status = ((typical_power_eval or {}).get("typical") or {}).get("target") or {}
    parts = []
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

    block_mails = service.db.records(conn, "tracking_block_mail", parent_id=round_row["id"], block_id=block_id, limit=5)
    latest_block_mail = mail_delivery_info(service, conn, block_mails[0] if block_mails else None, include_recipients=include_recipients)

    return {
        "block_id": block_id,
        "block_name": block_row["payload"].get("name") or "",
        "is_full_chip": full_chip,
        "revision_label": ", ".join(sorted(revisions)),
        "required_corners": len(req_corners),
        "arrived_corners": arrived,
        "completed_corners": completed,
        "missing_corners": missing_labels,
        "all_completed": len(req_corners) > 0 and completed == len(req_corners),
        "totals": totals,
        "power_evaluation": typical_power_eval,
        "corners": corner_rows,
        "post_top_mail_revised": post_top_revised,
        "status_summary": " · ".join(parts) or "대기",
        "next_action": next_action,
        "block_mail": latest_block_mail,
        "needs_attention": bool(missing_labels or issues or totals["unexpected"] > 0 or totals["needs_review"] > 0 or target_status.get("status") == "exceeded"),
    }


def overview(service, project_id: UUID) -> dict[str, Any]:
    is_admin = is_tracking_admin(service)
    with service.db.connect() as conn:
        project_row = service.db.record(conn, project_id, kind="project")
        proj_cfg_row = latest_one(service, conn, "tracking_project_config", project_id=project_id)
        proj_cfg = proj_cfg_row["payload"] if proj_cfg_row else {
            "enabled": False,
            "owner_user_id": None,
            "default_required_corners": [],
            "deadline_days": DEFAULT_DEADLINE_DAYS,
            "deadline_time": DEFAULT_DEADLINE_TIME,
            "deadline_timezone": DEFAULT_DEADLINE_TIMEZONE,
            "top_recipients": [],
            "thresholds": Thresholds().model_dump(mode="python"),
            "typical_specs": [],
            "power_targets": [],
        }
        block_cfg_rows = service.all_records(conn, "tracking_block_config", project_id=project_id, latest=True)
        block_cfgs = {str(row["block_id"]): row for row in block_cfg_rows}
        round_rows = service.all_records(conn, "tracking_round", project_id=project_id)
        round_rows.sort(key=lambda r: (r["payload"].get("netlist_arrived_at") or "", str(r.get("created_at") or "")), reverse=True)

        rounds_out = []
        for r in round_rows:
            rp = r["payload"]
            target_ids = [UUID(str(bid)) for bid in rp.get("target_block_ids") or []]
            blocks = [_block_live_row(service, conn, r, project_row, bid, proj_cfg, block_cfgs, include_recipients=is_admin) for bid in target_ids]
            full_chips = [b for b in blocks if b["is_full_chip"]]
            subs = [b for b in blocks if not b["is_full_chip"]]
            top_mail_id = rp.get("top_mail_record_id")
            top_mail_row = service.db.record(conn, UUID(str(top_mail_id)), kind="tracking_top_mail") if top_mail_id else None
            rounds_out.append({
                "id": r["id"],
                "row_version": r["row_version"],
                "state": r["state"],
                "net_ver": rp["net_ver"],
                "netlist_arrived_at": rp.get("netlist_arrived_at"),
                "deadline_date": rp.get("deadline_date"),
                "deadline_time": rp.get("deadline_time"),
                "deadline_timezone": rp.get("deadline_timezone"),
                "deadline_at": rp.get("deadline_at"),
                "target_block_ids": target_ids,
                "full_chip_counts": {
                    "total": len(full_chips),
                    "arrived": sum(b["arrived_corners"] > 0 for b in full_chips),
                    "completed": sum(b["all_completed"] for b in full_chips),
                },
                "sub_block_counts": {
                    "total": len(subs),
                    "arrived": sum(b["arrived_corners"] > 0 for b in subs),
                    "completed": sum(b["all_completed"] for b in subs),
                },
                "top_mail": mail_delivery_info(service, conn, top_mail_row, include_recipients=is_admin),
                "has_post_top_revisions": any(b["post_top_mail_revised"] for b in blocks),
                "blocks": blocks,
            })

        expectations = [
            {"id": row["id"], "entity_id": row["entity_id"], "row_version": row["row_version"], "block_id": row["block_id"], **row["payload"]}
            for row in service.all_records(conn, "tracking_expectation", project_id=project_id, latest=True)
            if not row["payload"].get("archived") and row.get("state") != "archived"
        ]
        proposals = [
            {"id": row["id"], "block_id": row["block_id"], "state": row["state"], "created_at": row.get("created_at"), "error_detail": row.get("error_detail"), **row["payload"]}
            for row in service.all_records(conn, "expected_change_proposal", project_id=project_id)
        ]
        proposals.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)

        ai_model = None
        if is_admin:
            host = host_db(service)
            ai_model = {"ready": False, "error": "AI_AUTHENTICATED_REQUEST_REQUIRED"}
            if host is not None:
                from oa.sanity_ai_task_defaults import task_selection
                _, selected, default = task_selection(ConnectionStore(host), service.access.actor.user_id, "AREA_TRIAGE")
                err = (
                    default.get("reason") if default and not default.get("applied")
                    else "AI_VWP_CONNECTION_REQUIRED" if selected and selected.get("execution_site") != "vwp"
                    else selected.get("credential_error") if selected and not selected.get("credential_ready")
                    else None
                )
                ai_model = {
                    "ready": bool(selected and err is None),
                    "error": err,
                    "connection_label": selected.get("label") if selected else None,
                    "model": selected.get("model") if selected else None,
                    "site": selected.get("execution_site") if selected else None,
                }

        public_proj_cfg = {k: v for k, v in proj_cfg.items() if k != "ai_context"}
        if not is_admin:
            public_proj_cfg["top_recipients"] = []
        public_block_cfgs = []
        for row in block_cfg_rows:
            entry = {"id": row["id"], "block_id": row["block_id"], "row_version": row["row_version"], **row["payload"]}
            if not is_admin:
                entry["recipients"] = []
            bvid = entry.get("baseline_version_id")
            if bvid:
                try:
                    bver = service.db.record(conn, UUID(str(bvid)), kind="version")
                    bp = bver.get("payload") or {}
                    entry["baseline_version_info"] = {
                        "id": bver["id"],
                        "net_ver": bp.get("net_ver") or "",
                        "revision": bp.get("revision") or "",
                        "layout": bp.get("layout") or "",
                    }
                except Exception:
                    entry["baseline_version_info"] = None
            public_block_cfgs.append(entry)

        result: dict[str, Any] = {
            "project_id": project_id,
            "project_name": project_row["payload"].get("name") or "",
            "project_config": {
                "id": proj_cfg_row["id"] if proj_cfg_row else None,
                "row_version": proj_cfg_row["row_version"] if proj_cfg_row else None,
                **public_proj_cfg,
            },
            "block_configs": public_block_cfgs,
            "rounds": rounds_out,
            "expectations": expectations if is_admin else [],
            "expectation_proposals": proposals[:20] if is_admin else [],
        }
        if ai_model is not None:
            result["ai_model"] = ai_model
        return result


class PreviewInput(Input):
    net_ver: str | None = Field(default=None, max_length=200)


def preview_tracking(service, project_id: UUID, body: PreviewInput) -> dict[str, Any]:
    """Pre-activation preview: evaluate existing uploads & render block/TOP mail previews without sending."""
    require_admin(service)
    data = overview(service, project_id)
    rounds = data["rounds"]
    chosen = next((r for r in rounds if r["net_ver"] == body.net_ver), None) if body.net_ver else (rounds[0] if rounds else None)
    if chosen is None:
        return {"round": None, "top_mail_preview": None, "block_mail_previews": []}
    full_chip = next((b for b in chosen["blocks"] if b["is_full_chip"]), None)
    top_snapshot = {
        "project_name": data["project_name"],
        "net_ver": chosen["net_ver"],
        "trigger": "all_completed" if all(b["all_completed"] for b in chosen["blocks"]) and chosen["blocks"] else "deadline",
        "netlist_arrived_at": chosen["netlist_arrived_at"],
        "deadline_display": f"{chosen.get('deadline_date') or ''} {chosen.get('deadline_time') or ''} ({chosen.get('deadline_timezone') or ''})".strip(),
        "full_chip": full_chip,
        "full_chip_counts": chosen["full_chip_counts"],
        "sub_block_counts": chosen["sub_block_counts"],
        "blocks": chosen["blocks"],
    }
    top_subject, top_body = render_top_mail_text(top_snapshot)

    block_previews = []
    for block in chosen["blocks"]:
        bsnap = {
            "project_name": data["project_name"],
            "block_name": block["block_name"],
            "is_full_chip": block["is_full_chip"],
            "net_ver": chosen["net_ver"],
            "revision_label": block["revision_label"],
            "totals": block["totals"],
            "power_evaluation": block["power_evaluation"],
            "corners": block["corners"],
            "revised": False,
            "post_top_mail_revised": block["post_top_mail_revised"],
        }
        b_subj, b_body = render_block_mail_text(bsnap)
        block_previews.append({
            "block_id": block["block_id"],
            "block_name": block["block_name"],
            "subject": b_subj,
            "body": b_body,
        })

    return {
        "round": {"id": chosen["id"], "net_ver": chosen["net_ver"]},
        "top_mail_preview": {"subject": top_subject, "body": top_body},
        "block_mail_previews": block_previews,
    }
