"""Post-triage slot summary, block/TOP mail snapshot dispatch, and deadline sweeps."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from common.area_power.analysis import advisory_lock, all_records, existing_dedupe
from common.area_power.jsonutil import digest
from common.area_power.tracking import (
    CornerSpec,
    block_mail_dedupe_key,
    effective_corners,
    evaluate_typical_and_same_condition,
    is_full_chip_block,
    resolve_versioned_spec,
    top_mail_dedupe_key,
)
from ..ai_models import ACTOR, AI_CONTEXT_ERRORS
from .context import iso_now, latest_one, now, parse_dt

D = Decimal


def slot_summary_from_run(db, conn, run: dict, comparison: dict, proj_cfg: dict, round_row: dict, *, is_full_chip: bool) -> dict[str, Any]:
    judgements = [row["payload"] for row in all_records(db, conn, "judgement", parent_id=run["id"])]
    counts = {
        "unexpected": sum(item.get("verdict") == "unexpected" for item in judgements),
        "needs_review": sum(item.get("verdict") == "needs_review" for item in judgements),
        "expected": sum(item.get("verdict") == "expected" for item in judgements),
        "minor": sum(item.get("verdict") == "minor" for item in judgements),
        "code_explained": sum(bool(item.get("code_explained")) for item in judgements),
    }
    cu = db.upload(conn, comparison["upload_id"])
    bu = db.upload(conn, comparison["baseline_upload_id"]) if comparison.get("baseline_upload_id") else None

    def _root_metric(upload, name):
        if not upload:
            return None
        val = ((upload.get("root_summary") or {}).get("metrics") or {}).get(name)
        return D(str(val)) if val is not None else None

    b_la, c_la = _root_metric(bu, "logic_area"), _root_metric(cu, "logic_area")
    la_pct = ((c_la - b_la) / b_la * D(100)).quantize(D("0.01")) if (b_la and c_la is not None) else None
    b_pw, c_pw = _root_metric(bu, "pwr_all_total_mw"), _root_metric(cu, "pwr_all_total_mw")
    pw_pct = ((c_pw - b_pw) / b_pw * D(100)).quantize(D("0.01")) if (b_pw and c_pw is not None) else None

    net_ver = round_row["payload"]["net_ver"]
    power_target_spec = resolve_versioned_spec(proj_cfg.get("power_targets") or [], net_ver) if is_full_chip else None
    target_mw = D(str(power_target_spec["target_mw"])) if power_target_spec and power_target_spec.get("target_mw") is not None else None
    power_eval = evaluate_typical_and_same_condition(
        current_typical=cu,
        baseline_typical=bu,
        power_target_mw=target_mw,
        expectations=run["payload"].get("approved_expectations") or [],
    )
    highlights = []
    for item in judgements:
        if item.get("verdict") in ("unexpected", "needs_review"):
            card = item.get("card") or {}
            area_c = card.get("area") or {}
            pwr_c = card.get("power_mw") or {}
            pwr_ok = "excluded" not in pwr_c
            highlights.append({
                "group_id": item.get("group_id"),
                "path": card.get("current_path") or card.get("baseline_path") or item.get("group_id"),
                "depth1_block": card.get("depth1_block"),
                "verdict": item.get("verdict"),
                "cause": item.get("cause"),
                "ranking_tags": card.get("ranking_tags") or [],
                "distributed_metrics": card.get("distributed_metrics") or [],
                "logic_area_delta": area_c.get("delta") if "excluded" not in area_c else None,
                "logic_area_delta_pct": area_c.get("delta_pct") if "excluded" not in area_c else None,
                "logic_area_damped_pct": area_c.get("damped_pct") if "excluded" not in area_c else None,
                "power_delta_mw": pwr_c.get("delta") if pwr_ok else None,
                "power_delta_pct": pwr_c.get("delta_pct") if pwr_ok else None,
                "power_damped_pct": pwr_c.get("damped_pct") if pwr_ok else None,
                "explanation": item.get("explanation") or "",
            })
    highlights.sort(key=lambda h: (0 if h["verdict"] == "unexpected" else 1, h["path"]))
    return {
        "counts": counts,
        "attribution": run["payload"].get("attribution"),
        "logic_area_baseline": b_la,
        "logic_area_current": c_la,
        "logic_area_delta_pct": la_pct,
        "power_baseline_mw": b_pw,
        "power_current_mw": c_pw,
        "power_delta_pct": pw_pct,
        "power_evaluation": power_eval,
        "highlights": highlights[:40],
    }


def block_snapshot(db, conn, round_row: dict, project_row: dict, block_id: UUID, proj_cfg: dict) -> dict[str, Any]:
    block_row = db.record(conn, block_id, kind="block")
    block_cfg_row = latest_one(db, conn, "tracking_block_config", project_id=round_row["project_id"], block_id=block_id)
    block_cfg = block_cfg_row["payload"] if block_cfg_row else {}
    full_chip = is_full_chip_block(project_row["payload"].get("name"), block_row["payload"].get("name"))
    req_corners = effective_corners(proj_cfg, block_cfg)
    slots = {
        s["payload"]["corner_id"]: s
        for s in all_records(db, conn, "tracking_slot", parent_id=round_row["id"], block_id=block_id)
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
            issues.append(f"{corner.label} 인증 만료/오류")
        elif status == "failed":
            issues.append(f"{corner.label} 분석 실패")
        else:
            issues.append(f"{corner.label} 분석 중")
        if sp.get("last_failed_upload"):
            issues.append(f"{corner.label} 수정본 검증 실패(직전 정상본 유지)")

        if summary.get("power_evaluation") and (typical_power_eval is None or cid == typical_cid):
            typical_power_eval = summary["power_evaluation"]

        corner_rows.append({
            "corner_id": cid,
            "corner_label": corner.label,
            "status": status,
            "status_reason": sp.get("status_reason"),
            "upload_id": sp.get("latest_upload_id"),
            "baseline_upload_id": sp.get("baseline_upload_id"),
            "comparison_id": sp.get("comparison_id"),
            "ai_run_id": sp.get("ai_run_id"),
            "logic_area_baseline": summary.get("logic_area_baseline"),
            "logic_area_current": summary.get("logic_area_current"),
            "logic_area_delta_pct": summary.get("logic_area_delta_pct"),
            "power_baseline_mw": summary.get("power_baseline_mw"),
            "power_current_mw": summary.get("power_current_mw"),
            "power_delta_pct": summary.get("power_delta_pct"),
            "expected_count": c_counts.get("expected", 0),
            "unexpected_count": c_counts.get("unexpected", 0),
            "needs_review_count": c_counts.get("needs_review", 0),
            "code_explained_count": c_counts.get("code_explained", 0),
            "attribution": summary.get("attribution"),
            "highlights": summary.get("highlights") or [],
            "last_failed_upload": sp.get("last_failed_upload"),
            "post_top_mail_revised": bool(sp.get("post_top_mail_revised")),
        })

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

    needs_attention = bool(
        missing_labels
        or issues
        or totals["unexpected"] > 0
        or totals["needs_review"] > 0
        or target_status.get("status") == "exceeded"
    )
    return {
        "project_id": str(round_row["project_id"]),
        "project_name": project_row["payload"].get("name") or "",
        "block_id": str(block_id),
        "block_name": block_row["payload"].get("name") or "",
        "is_full_chip": full_chip,
        "net_ver": net_ver,
        "revision_label": ", ".join(sorted(revisions)),
        "required_corners": len(req_corners),
        "arrived_corners": arrived,
        "completed_corners": completed,
        "missing_corners": missing_labels,
        "all_completed": len(req_corners) > 0 and completed == len(req_corners),
        "totals": totals,
        "power_evaluation": typical_power_eval,
        "corners": corner_rows,
        "recipients": block_cfg.get("recipients") or [],
        "post_top_mail_revised": post_top_revised,
        "needs_attention": needs_attention,
        "status_summary": " · ".join(parts) or "대기",
    }


def maybe_dispatch_block_mail(db, round_id: UUID, block_id: UUID) -> dict | None:
    lock_key = digest(["tracking_block_mail_lock", str(round_id), str(block_id)])
    with db.connect() as conn:
        advisory_lock(conn, lock_key)
        round_row = db.record(conn, round_id, kind="tracking_round")
        project_cfg_row = latest_one(db, conn, "tracking_project_config", project_id=round_row["project_id"])
        if not project_cfg_row or not project_cfg_row["payload"].get("enabled"):
            return None
        proj_cfg = project_cfg_row["payload"]
        project_row = db.record(conn, round_row["project_id"], kind="project")
        snap = block_snapshot(db, conn, round_row, project_row, block_id, proj_cfg)
        if not snap["all_completed"]:
            return None
        recipients = snap.get("recipients") or []
        if not recipients:
            return None
        sigs = [
            (c["corner_id"], str(c.get("upload_id") or ""), str(c.get("ai_run_id") or ""))
            for c in snap["corners"]
        ]
        mkey = block_mail_dedupe_key(round_id, block_id, sigs)
        existing = existing_dedupe(db, conn, "tracking_block_mail", mkey)
        if existing is not None:
            return existing
        prior_mails = db.records(conn, "tracking_block_mail", parent_id=round_id, block_id=block_id, limit=5)
        snap["revised"] = bool(prior_mails)
        snap["frozen_at"] = iso_now()
        owner_actor = proj_cfg.get("owner_user_id") or ACTOR
        mail_rec = db.insert_record(
            conn, "tracking_block_mail",
            {"recipients": recipients, "snapshot": snap, "requested_by": owner_actor},
            owner_actor, state="requested", parent_id=round_id,
            project_id=round_row["project_id"], block_id=block_id, dedupe_key=mkey,
        )
        db.enqueue(
            conn, "mail", actor=owner_actor, parent_id=mail_rec["id"],
            payload={"recipients": recipients},
            dedupe_key=digest(["tracking_block_mail_job", str(mail_rec["id"])]),
            project_id=round_row["project_id"], block_id=block_id,
        )
        return mail_rec


def maybe_dispatch_top_mail(db, round_id: UUID, *, trigger: str = "all_completed") -> dict | None:
    """Atomically finalize at most one TOP summary mail per project × NET_VER."""
    tkey = top_mail_dedupe_key(round_id)
    with db.connect() as conn:
        advisory_lock(conn, tkey)
        round_row = db.record(conn, round_id, kind="tracking_round", lock=True)
        rp = round_row["payload"]
        if rp.get("top_mail_record_id"):
            return existing_dedupe(db, conn, "tracking_top_mail", tkey)
        existing = existing_dedupe(db, conn, "tracking_top_mail", tkey)
        if existing is not None:
            return existing
        project_cfg_row = latest_one(db, conn, "tracking_project_config", project_id=round_row["project_id"])
        if not project_cfg_row or not project_cfg_row["payload"].get("enabled"):
            return None
        proj_cfg = project_cfg_row["payload"]
        if trigger == "deadline":
            due = parse_dt(rp.get("deadline_at"))
            if due is None or due > now():
                return None

        project_row = db.record(conn, round_row["project_id"], kind="project")
        target_ids = [UUID(str(bid)) for bid in rp.get("target_block_ids") or []]
        if not target_ids:
            return None
        blocks = [block_snapshot(db, conn, round_row, project_row, bid, proj_cfg) for bid in target_ids]
        if trigger == "all_completed" and not all(b["all_completed"] for b in blocks):
            return None

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
        recipients = rp.get("applied_config_snapshot", {}).get("top_recipients") or proj_cfg.get("top_recipients") or []
        snapshot = {
            "round_id": str(round_id),
            "project_id": str(round_row["project_id"]),
            "project_name": project_row["payload"].get("name") or "",
            "net_ver": rp["net_ver"],
            "trigger": trigger,
            "frozen_at": iso_now(),
            "netlist_arrived_at": rp.get("netlist_arrived_at"),
            "deadline_at": rp.get("deadline_at"),
            "deadline_display": f"{rp.get('deadline_date', '')} {rp.get('deadline_time', '')} ({rp.get('deadline_timezone', '')})".strip(),
            "full_chip": full_chips[0] if full_chips else None,
            "full_chip_counts": fc_counts,
            "sub_block_counts": sub_counts,
            "blocks": blocks,
        }
        owner_actor = proj_cfg.get("owner_user_id") or ACTOR
        mail_rec = db.insert_record(
            conn, "tracking_top_mail",
            {"recipients": recipients, "trigger": trigger, "snapshot": snapshot, "requested_by": owner_actor},
            owner_actor, state="requested", parent_id=round_id,
            project_id=round_row["project_id"], dedupe_key=tkey,
        )
        db.enqueue(
            conn, "mail", actor=owner_actor, parent_id=mail_rec["id"],
            payload={"recipients": recipients},
            dedupe_key=digest(["tracking_top_mail_job", str(round_id)]),
            project_id=round_row["project_id"],
        )
        new_rp = {
            **rp,
            "top_mail_record_id": mail_rec["id"],
            "top_mail_trigger": trigger,
            "top_mail_frozen_at": snapshot["frozen_at"],
            "top_mail_state": "requested",
        }
        db.update_record(conn, round_id, ACTOR, payload=new_rp)
        return mail_rec


def on_triage_finished(db, settings, run: dict) -> None:
    """Update tracking_slot after VWP triage settles, then evaluate block & TOP mail triggers."""
    slot_id_raw = (run.get("payload") or {}).get("tracking_slot_id")
    if not slot_id_raw:
        return
    slot_id = UUID(str(slot_id_raw))
    expected_gen = (run.get("payload") or {}).get("tracking_generation")
    with db.connect() as conn:
        slot = db.record(conn, slot_id, kind="tracking_slot", lock=True)
        if expected_gen is not None and slot["payload"].get("generation") != expected_gen:
            return
        round_row = db.record(conn, slot["parent_id"], kind="tracking_round")
        comparison = db.record(conn, run["parent_id"], kind="comparison")
        project_cfg_row = latest_one(db, conn, "tracking_project_config", project_id=slot["project_id"])
        proj_cfg = project_cfg_row["payload"] if project_cfg_row else {}
        err_code = (run.get("error_detail") or {}).get("code") or ""
        if run["state"] == "completed":
            status = "completed"
            reason = None
        elif err_code in AI_CONTEXT_ERRORS or str(err_code).startswith("AUTHORIZATION_"):
            status = "auth_error"
            reason = err_code
        else:
            status = "failed"
            reason = err_code or "ai_incomplete"
        summary = slot_summary_from_run(
            db, conn, run, comparison, proj_cfg, round_row,
            is_full_chip=bool(slot["payload"].get("is_full_chip")),
        )
        new_payload = {
            **slot["payload"],
            "status": status,
            "status_reason": reason,
            "ai_run_id": run["id"],
            "comparison_id": comparison["id"],
            "summary": summary,
        }
        db.update_record(conn, slot_id, ACTOR, state=status, payload=new_payload)
        round_id = round_row["id"]
        block_id = slot["block_id"]
        is_required = bool(slot["payload"].get("is_required"))

    if status == "completed" and is_required:
        maybe_dispatch_block_mail(db, round_id, block_id)
        maybe_dispatch_top_mail(db, round_id, trigger="all_completed")


def check_deadlines(db) -> list[dict]:
    """Sweep active tracking rounds whose deadline has passed and finalize TOP mail once."""
    sent = []
    current = now()
    with db.connect() as conn:
        rounds = all_records(db, conn, "tracking_round", state="active")
    for round_row in rounds:
        rp = round_row["payload"]
        if rp.get("top_mail_record_id"):
            continue
        due = parse_dt(rp.get("deadline_at"))
        if due is not None and due <= current:
            rec = maybe_dispatch_top_mail(db, round_row["id"], trigger="deadline")
            if rec is not None:
                sent.append(rec)
    return sent
