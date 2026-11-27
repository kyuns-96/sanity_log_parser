"""Post-triage slot summary, block/TOP mail snapshot dispatch, and deadline sweeps."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from common.area_power.analysis import advisory_lock, all_records, existing_dedupe
from common.area_power.jsonutil import digest
from common.area_power.tracking import (
    aggregate_block_tracking_row,
    block_mail_dedupe_key,
    evaluate_typical_and_same_condition,
    make_carried_over_block_row,
    resolve_carried_over_source,
    resolve_versioned_spec,
    round_sort_key,
    summarize_block_counts,
    top_mail_dedupe_key,
)
from ..ai_runtime import ACTOR, AI_CONTEXT_ERRORS
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
    slots = {
        s["payload"]["corner_id"]: s
        for s in all_records(db, conn, "tracking_slot", parent_id=round_row["id"], block_id=block_id)
    }
    aggregated = aggregate_block_tracking_row(
        project_id=str(round_row["project_id"]),
        project_name=project_row["payload"].get("name") or "",
        block_id=str(block_id),
        block_name=block_row["payload"].get("name") or "",
        net_ver=round_row["payload"]["net_ver"],
        proj_cfg=proj_cfg,
        block_cfg=block_cfg,
        slots_by_corner=slots,
        auth_error_label="인증 만료/오류",
    )
    return {
        **aggregated,
        "recipients": block_cfg.get("recipients") or [],
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

        target_id_set = {str(bid) for bid in target_ids}
        active_blocks = [
            b for b in all_records(db, conn, "block", project_id=round_row["project_id"])
            if b.get("state") == "active"
        ]
        all_rounds = all_records(db, conn, "tracking_round", project_id=round_row["project_id"])
        all_rounds.sort(key=round_sort_key, reverse=True)
        cur_idx = next((i for i, rr in enumerate(all_rounds) if str(rr["id"]) == str(round_id)), -1)
        earlier_rounds = all_rounds[cur_idx + 1:] if cur_idx >= 0 else []

        def _load_slots(rid: UUID, bid: UUID) -> dict[str, dict[str, Any]]:
            return {
                s["payload"]["corner_id"]: s
                for s in all_records(db, conn, "tracking_slot", parent_id=rid, block_id=bid)
            }

        carried_over_blocks = []
        for blk in active_blocks:
            if str(blk["id"]) in target_id_set:
                continue
            src_round, src_slots = resolve_carried_over_source(blk["id"], earlier_rounds, _load_slots)
            bcfg_row = latest_one(db, conn, "tracking_block_config", project_id=round_row["project_id"], block_id=blk["id"])
            carried_over_blocks.append(
                make_carried_over_block_row(
                    project_id=str(round_row["project_id"]),
                    project_name=project_row["payload"].get("name") or "",
                    block_id=str(blk["id"]),
                    block_name=(blk.get("payload") or {}).get("name") or "",
                    proj_cfg=proj_cfg,
                    block_cfg=bcfg_row["payload"] if bcfg_row else {},
                    source_round=src_round,
                    source_slots_by_corner=src_slots,
                    auth_error_label="인증 만료/오류",
                )
            )

        full_chips, fc_counts, sub_counts = summarize_block_counts(blocks)
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
            "carried_over_blocks": carried_over_blocks,
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
