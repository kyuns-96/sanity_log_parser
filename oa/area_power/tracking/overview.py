"""OA NET_VER tracking overview, live block rows, mail status, and pre-activation preview."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from common.area_power.tracking import (
    DEFAULT_DEADLINE_DAYS,
    DEFAULT_DEADLINE_TIME,
    DEFAULT_DEADLINE_TIMEZONE,
    aggregate_block_tracking_row,
    build_live_round_mail_snapshots,
    compute_next_tracking_action,
    make_carried_over_block_row,
    render_block_mail,
    render_top_mail,
    resolve_carried_over_source,
    round_sort_key,
    summarize_block_counts,
)
from common.area_power.triage import Thresholds
from common.sanity_common import ReviewError
from ..ai_context import describe_area_triage_model
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
    slots = {
        s["payload"]["corner_id"]: s
        for s in service.all_records(conn, "tracking_slot", parent_id=round_row["id"], block_id=block_id)
    }
    aggregated = aggregate_block_tracking_row(
        project_id=round_row["project_id"],
        project_name=project_row["payload"].get("name") or "",
        block_id=block_id,
        block_name=block_row["payload"].get("name") or "",
        net_ver=round_row["payload"]["net_ver"],
        proj_cfg=proj_cfg,
        block_cfg=block_cfg,
        slots_by_corner=slots,
    )
    block_mails = service.db.records(conn, "tracking_block_mail", parent_id=round_row["id"], block_id=block_id, limit=5)
    latest_block_mail = mail_delivery_info(service, conn, block_mails[0] if block_mails else None, include_recipients=include_recipients)
    return {
        **{k: v for k, v in aggregated.items() if k not in ("project_id", "project_name", "net_ver")},
        "next_action": compute_next_tracking_action(aggregated),
        "block_mail": latest_block_mail,
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
        active_blocks = [
            b for b in service.all_records(conn, "block", project_id=project_id)
            if b.get("state") == "active"
        ]
        round_rows = service.all_records(conn, "tracking_round", project_id=project_id)
        round_rows.sort(key=round_sort_key, reverse=True)

        slot_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

        def _load_slots(rid: UUID, bid: UUID) -> dict[str, dict[str, Any]]:
            ck = (str(rid), str(bid))
            if ck not in slot_cache:
                slot_cache[ck] = {
                    s["payload"]["corner_id"]: s
                    for s in service.all_records(conn, "tracking_slot", parent_id=rid, block_id=bid)
                }
            return slot_cache[ck]

        project_name = project_row["payload"].get("name") or ""
        rounds_out = []
        for idx, r in enumerate(round_rows):
            rp = r["payload"]
            target_ids = [UUID(str(bid)) for bid in rp.get("target_block_ids") or []]
            target_id_set = {str(bid) for bid in target_ids}
            blocks = [_block_live_row(service, conn, r, project_row, bid, proj_cfg, block_cfgs, include_recipients=is_admin) for bid in target_ids]
            earlier_rounds = round_rows[idx + 1:]
            carried_over_blocks = []
            for blk in active_blocks:
                if str(blk["id"]) in target_id_set:
                    continue
                src_round, src_slots = resolve_carried_over_source(blk["id"], earlier_rounds, _load_slots)
                bcfg_row = block_cfgs.get(str(blk["id"]))
                carried_over_blocks.append(
                    make_carried_over_block_row(
                        project_id=r["project_id"],
                        project_name=project_name,
                        block_id=blk["id"],
                        block_name=(blk.get("payload") or {}).get("name") or "",
                        proj_cfg=proj_cfg,
                        block_cfg=bcfg_row["payload"] if bcfg_row else {},
                        source_round=src_round,
                        source_slots_by_corner=src_slots,
                    )
                )
            _, fc_counts, sub_counts = summarize_block_counts(blocks)
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
                "full_chip_counts": fc_counts,
                "sub_block_counts": sub_counts,
                "top_mail": mail_delivery_info(service, conn, top_mail_row, include_recipients=is_admin),
                "has_post_top_revisions": any(b["post_top_mail_revised"] for b in blocks),
                "blocks": blocks,
                "carried_over_blocks": carried_over_blocks,
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
            actor = getattr(service.access, "actor", None)
            ai_model = describe_area_triage_model(host_db(service), getattr(actor, "user_id", None), vwp_only=True)

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
                        "eco_num": bp.get("eco_num") or "",
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
    top_snapshot, block_snapshots = build_live_round_mail_snapshots(data["project_name"], chosen)
    dashboard_url = getattr(getattr(getattr(service, "db", None), "settings", None), "dashboard_url", None)
    top_subject, top_body, top_html = render_top_mail(top_snapshot, dashboard_url=dashboard_url)

    block_previews = []
    for bsnap in block_snapshots:
        b_subj, b_body, b_html = render_block_mail(bsnap, dashboard_url=dashboard_url)
        block_previews.append({
            "block_id": bsnap["block_id"],
            "block_name": bsnap["block_name"],
            "subject": b_subj,
            "body": b_body,
            "html_body": b_html,
        })

    return {
        "round": {"id": chosen["id"], "net_ver": chosen["net_ver"]},
        "top_mail_preview": {"subject": top_subject, "body": top_body, "html_body": top_html},
        "block_mail_previews": block_previews,
    }
