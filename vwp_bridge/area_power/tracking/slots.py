"""Tracking slot lifecycle: upload intake triggers, slot progression, and round reconciliation."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from common.area_power.analysis import advisory_lock, all_records, existing_dedupe
from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest
from common.area_power.tracking import (
    CornerSpec,
    effective_corners,
    is_full_chip_block,
    slot_dedupe_key,
    upload_corner_label,
)
from common.area_power.triage import ENGINE as TRIAGE_ENGINE, Thresholds
from ..ai_models import ACTOR
from .context import (
    applicable_notes_and_watch,
    check_mapping_for_uploads,
    ensure_comparison,
    iso_now,
    latest_one,
    now,
    parse_dt,
    resolve_baseline_upload,
    resolve_version_info,
)
from .dispatch import maybe_dispatch_top_mail, on_triage_finished

LOG = logging.getLogger("vwp_bridge.area_power.tracking")


def _slot_identity(upload: dict) -> dict[str, Any]:
    meta = (upload.get("input_contract") or {}).get("metadata") or {}
    return {
        "design_stage": meta.get("design_stage"),
        "eco_num": meta.get("eco_num") or "",
        "condition_key": bytes(upload["condition_key"]).hex() if upload.get("condition_key") else None,
        "corner_label": upload_corner_label(upload),
    }


def upsert_slot(
    db, conn, *, round_row: dict, block_id: UUID, corner: CornerSpec, is_required: bool,
    is_full_chip: bool, upload: dict | None, version_info: dict, actor: str,
) -> dict:
    cid = corner.corner_id()
    key = slot_dedupe_key(round_row["id"], block_id, cid)
    advisory_lock(conn, key)
    existing = existing_dedupe(db, conn, "tracking_slot", key, lock=True)
    top_sent = bool(round_row["payload"].get("top_mail_record_id"))
    if existing is None:
        ident = _slot_identity(upload) if upload else {"design_stage": None, "eco_num": "", "condition_key": None}
        payload = {
            "round_id": str(round_row["id"]),
            "net_ver": round_row["payload"]["net_ver"],
            "corner_id": cid,
            "corner_label": corner.label,
            "is_required": is_required,
            "is_full_chip": is_full_chip,
            "latest_upload_id": upload["id"] if upload else None,
            "latest_upload_created_at": upload.get("created_at") if upload else None,
            "revision_label": version_info.get("revision") or "",
            "layout_label": version_info.get("layout") or "",
            "eco_label": version_info.get("eco_num") or ident["eco_num"] or "",
            "design_stage": ident["design_stage"],
            "condition_key_hex": ident["condition_key"],
            "last_failed_upload": None,
            "baseline_upload_id": None,
            "mapping_id": None,
            "comparison_id": None,
            "ai_run_id": None,
            "status": "queued" if upload else "waiting_upload",
            "status_reason": None,
            "mapping_check": None,
            "summary": None,
            "post_top_mail_revised": top_sent if upload else False,
            "generation": 1 if upload else 0,
        }
        return db.insert_record(
            conn, "tracking_slot", payload, actor,
            parent_id=round_row["id"], project_id=round_row["project_id"], block_id=block_id,
            version_id=upload["version_id"] if upload else None,
            upload_id=upload["id"] if upload else None,
            state=payload["status"], dedupe_key=key,
        )

    payload = dict(existing["payload"])
    payload["is_required"] = is_required
    payload["is_full_chip"] = is_full_chip
    payload["corner_label"] = corner.label
    if upload is not None:
        same_upload = str(payload.get("latest_upload_id")) == str(upload["id"])
        if not same_upload:
            payload["generation"] = int(payload.get("generation") or 0) + 1
            payload["latest_upload_id"] = upload["id"]
            payload["latest_upload_created_at"] = upload.get("created_at")
            payload["revision_label"] = version_info.get("revision") or payload.get("revision_label") or ""
            payload["layout_label"] = version_info.get("layout") or payload.get("layout_label") or ""
            ident = _slot_identity(upload)
            payload["eco_label"] = version_info.get("eco_num") or ident["eco_num"] or payload.get("eco_label") or ""
            payload["design_stage"] = ident["design_stage"]
            payload["condition_key_hex"] = ident["condition_key"]
            payload["last_failed_upload"] = None
            payload["status"] = "queued"
            payload["status_reason"] = None
            payload["comparison_id"] = None
            payload["ai_run_id"] = None
            payload["summary"] = None
            if top_sent:
                payload["post_top_mail_revised"] = True
        return db.update_record(
            conn, existing["id"], actor,
            version_id=upload["version_id"], upload_id=upload["id"],
            state=payload["status"], payload=payload,
        )
    return db.update_record(conn, existing["id"], actor, payload=payload)


def advance_slot(db, settings, slot_id: UUID, *, host_db=None) -> dict | None:
    """Drive one tracking_slot forward from validated CSV → comparison → queued VWP triage."""
    with db.connect() as conn:
        slot = db.record(conn, slot_id, kind="tracking_slot")
        round_row = db.record(conn, slot["parent_id"], kind="tracking_round")
        project_cfg_row = latest_one(db, conn, "tracking_project_config", project_id=slot["project_id"])
        block_cfg_row = latest_one(db, conn, "tracking_block_config", project_id=slot["project_id"], block_id=slot["block_id"])
        if not project_cfg_row or not block_cfg_row:
            return slot
        proj_cfg = project_cfg_row["payload"]
        block_cfg = block_cfg_row["payload"]
        if not proj_cfg.get("enabled") or not block_cfg.get("enabled"):
            return slot
        upload_id = slot["payload"].get("latest_upload_id")
        if not upload_id:
            return slot
        current_upload = db.upload(conn, UUID(str(upload_id)))
        if current_upload["state"] != "ready":
            return slot

        corners = effective_corners(proj_cfg, block_cfg)
        cid = slot["payload"]["corner_id"]
        corner_spec = next((c for c in corners if c.corner_id() == cid), None)
        if corner_spec is None:
            return slot
        baseline_upload = resolve_baseline_upload(db, conn, block_cfg, corner_spec)
        if baseline_upload is None:
            with db.connect() as wconn:
                locked = db.record(wconn, slot_id, kind="tracking_slot", lock=True)
                if locked["payload"].get("generation") != slot["payload"].get("generation"):
                    return locked
                payload = {**locked["payload"], "status": "waiting_baseline", "status_reason": "baseline_not_approved"}
                return db.update_record(wconn, slot_id, ACTOR, state="waiting_baseline", payload=payload)
        mapping_id = UUID(str(block_cfg["approved_mapping_id"])) if block_cfg.get("approved_mapping_id") else None
        mapping_row = db.record(conn, mapping_id, kind="mapping") if mapping_id else None
        project_row = db.record(conn, slot["project_id"], kind="project")
        block_row = db.record(conn, slot["block_id"], kind="block")
        baseline_project_row = db.record(conn, baseline_upload["project_id"], kind="project")
        baseline_block_row = db.record(conn, baseline_upload["block_id"], kind="block")
        triage_ctx = applicable_notes_and_watch(
            db, conn, current_upload, baseline_upload,
            block_cfg.get("comparison_kind") or "previous_project",
            round_row["payload"]["net_ver"], host_db=host_db,
        )

    mapping_check = check_mapping_for_uploads(db, settings, current_upload, baseline_upload, mapping_row)
    if not mapping_check["ok"]:
        with db.connect() as conn:
            locked = db.record(conn, slot_id, kind="tracking_slot", lock=True)
            if locked["payload"].get("generation") != slot["payload"].get("generation"):
                return locked
            payload = {
                **locked["payload"],
                "baseline_upload_id": baseline_upload["id"],
                "mapping_id": mapping_id,
                "status": "mapping_review",
                "status_reason": mapping_check["reason"],
                "mapping_check": mapping_check,
            }
            return db.update_record(
                conn, slot_id, ACTOR, state="mapping_review",
                baseline_upload_id=baseline_upload["id"], payload=payload,
            )

    owner_actor = proj_cfg.get("owner_user_id") or ACTOR
    comparison = ensure_comparison(
        db, settings, current_upload, baseline_upload,
        block_cfg.get("comparison_kind") or "previous_project",
        mapping_id, owner_actor,
    )

    thresholds = Thresholds.model_validate(proj_cfg.get("thresholds") or {})
    labels = {
        "current": f"{project_row['payload'].get('name', '')} · {block_row['payload'].get('name', '')} · {round_row['payload']['net_ver']}".strip(" ·"),
        "baseline": f"{baseline_project_row['payload'].get('name', '')} · {baseline_block_row['payload'].get('name', '')}".strip(" ·"),
    }
    ai_context = proj_cfg.get("ai_context") or {}
    retry_limit = int(proj_cfg.get("retry_limit", 5))

    run_key = digest({
        "tracking_triage_run": True,
        "slot_id": str(slot_id),
        "generation": slot["payload"]["generation"],
        "comparison_id": str(comparison["id"]),
        "thresholds": thresholds.model_dump(mode="python"),
        "notes": [n["id"] for n in triage_ctx["notes"]],
        "expectations": [e["id"] for e in triage_ctx["approved_expectations"]],
        "watched_paths": triage_ctx["watched_paths"],
    })
    with db.connect() as conn:
        advisory_lock(conn, run_key)
        run = existing_dedupe(db, conn, "ai_run", run_key)
        if run is None:
            run_payload = {
                "engine": TRIAGE_ENGINE,
                "retry_limit": retry_limit,
                "thresholds": thresholds.model_dump(mode="python"),
                "notes": triage_ctx["notes"],
                "note_warnings": triage_ctx["note_warnings"],
                "same_rtl": triage_ctx["same_rtl"],
                "watched_paths": triage_ctx["watched_paths"],
                "approved_expectations": triage_ctx["approved_expectations"],
                "process": triage_ctx["process"],
                "comparison_kind": block_cfg.get("comparison_kind") or "previous_project",
                "labels": labels,
                "requested_by": owner_actor,
                "ai_context": ai_context,
                "tracking_slot_id": str(slot_id),
                "tracking_round_id": str(round_row["id"]),
                "tracking_generation": slot["payload"]["generation"],
                "comparison_condition": "same_condition",
                "coverage": {},
            }
            scope = {k: comparison[k] for k in ("project_id", "version_id", "block_id", "snapshot_id") if comparison.get(k) is not None}
            run = db.insert_record(
                conn, "ai_run", run_payload, owner_actor, state="queued",
                parent_id=comparison["id"], upload_id=comparison["upload_id"],
                baseline_upload_id=comparison.get("baseline_upload_id"),
                dedupe_key=run_key, **scope,
            )
            db.enqueue(
                conn, "judge", actor=owner_actor, parent_id=run["id"], payload={},
                dedupe_key=digest(["tracking_triage_job", str(run["id"])]), **scope,
            )

        locked = db.record(conn, slot_id, kind="tracking_slot", lock=True)
        if locked["payload"].get("generation") != slot["payload"].get("generation"):
            return locked
        slot_status = "completed" if run["state"] == "completed" else ("failed" if run["state"] == "failed" else "queued")
        payload = {
            **locked["payload"],
            "baseline_upload_id": baseline_upload["id"],
            "mapping_id": mapping_id,
            "comparison_id": comparison["id"],
            "ai_run_id": run["id"],
            "mapping_check": mapping_check,
            "status": slot_status,
            "status_reason": None,
        }
        updated = db.update_record(
            conn, slot_id, ACTOR, state=slot_status,
            baseline_upload_id=baseline_upload["id"], payload=payload,
        )
    if run["state"] in ("completed", "failed"):
        on_triage_finished(db, settings, run)
    return updated


def on_upload_ready(db, settings, upload: dict, *, host_db=None) -> list[dict]:
    """Match a newly validated ready CSV against active tracking rounds and kick off analysis."""
    triggered = []
    try:
        with db.connect() as conn:
            project_cfg_row = latest_one(db, conn, "tracking_project_config", project_id=upload["project_id"])
            if not project_cfg_row or not project_cfg_row["payload"].get("enabled"):
                return []
            block_cfg_row = latest_one(db, conn, "tracking_block_config", project_id=upload["project_id"], block_id=upload["block_id"])
            if not block_cfg_row or not block_cfg_row["payload"].get("enabled"):
                return []
            proj_cfg, block_cfg = project_cfg_row["payload"], block_cfg_row["payload"]
            stage_filter = block_cfg.get("design_stage")
            upload_stage = ((upload.get("input_contract") or {}).get("metadata") or {}).get("design_stage")
            if stage_filter and upload_stage and stage_filter != upload_stage:
                return []
            version_info = resolve_version_info(db, conn, upload["version_id"], upload["block_id"], host_db=host_db)
            net_ver = version_info.get("net_ver")
            if not net_ver:
                return []
            round_key = digest(["tracking_round", str(upload["project_id"]), net_ver])
            round_row = existing_dedupe(db, conn, "tracking_round", round_key)
            if round_row is None or round_row.get("state") == "archived":
                return []
            target_blocks = {str(bid) for bid in round_row["payload"].get("target_block_ids") or []}
            if str(upload["block_id"]) not in target_blocks:
                return []
            project_row = db.record(conn, upload["project_id"], kind="project")
            block_row = db.record(conn, upload["block_id"], kind="block")
            full_chip = is_full_chip_block(project_row["payload"].get("name"), block_row["payload"].get("name"))
            req_corners = effective_corners(proj_cfg, block_cfg)
            matched_required = [c for c in req_corners if c.matches_upload(upload)]
            if not matched_required:
                return []
            slots = []
            for corner in matched_required:
                slot = upsert_slot(
                    db, conn, round_row=round_row, block_id=upload["block_id"], corner=corner,
                    is_required=True, is_full_chip=full_chip, upload=upload,
                    version_info=version_info, actor=ACTOR,
                )
                slots.append(slot)
        for slot in slots:
            result = advance_slot(db, settings, slot["id"], host_db=host_db)
            if result is not None:
                triggered.append(result)
    except Exception as exc:
        LOG.error("Tracking trigger on upload failed (%s)", type(exc).__name__)
    return triggered


def on_upload_failed(db, metadata, filename: str, error: DomainError, *, host_db=None) -> None:
    """Keep the previous valid upload intact on validation failure; record failure reason on matching slots."""
    try:
        with db.connect() as conn:
            version_info = resolve_version_info(db, conn, metadata.version_id, metadata.block_id, host_db=host_db)
            net_ver = version_info.get("net_ver")
            if not net_ver:
                return
            round_key = digest(["tracking_round", str(metadata.project_id), net_ver])
            round_row = existing_dedupe(db, conn, "tracking_round", round_key)
            if round_row is None:
                return
            slots = all_records(db, conn, "tracking_slot", parent_id=round_row["id"], block_id=metadata.block_id)
            failure = {
                "filename": filename,
                "execution_name": getattr(metadata, "execution_name", None),
                "code": error.code,
                "message": str(error)[:400],
                "failed_at": iso_now(),
            }
            for slot in slots:
                locked = db.record(conn, slot["id"], kind="tracking_slot", lock=True)
                payload = {**locked["payload"], "last_failed_upload": failure}
                db.update_record(conn, locked["id"], ACTOR, payload=payload)
    except Exception as exc:
        LOG.warning("Could not record failed upload on tracking slot (%s)", type(exc).__name__)


def reconcile_round(db, settings, round_id: UUID, *, host_db=None) -> list[dict]:
    """Scan existing ready uploads for a round's target blocks, advance waiting slots, and check mail triggers."""
    with db.connect() as conn:
        round_row = db.record(conn, round_id, kind="tracking_round")
        project_cfg_row = latest_one(db, conn, "tracking_project_config", project_id=round_row["project_id"])
        if not project_cfg_row or not project_cfg_row["payload"].get("enabled"):
            return []
        proj_cfg = project_cfg_row["payload"]
        project_row = db.record(conn, round_row["project_id"], kind="project")
        net_ver = round_row["payload"]["net_ver"]
        target_ids = [UUID(str(bid)) for bid in round_row["payload"].get("target_block_ids") or []]
        slots_to_advance: list[UUID] = []
        for bid in target_ids:
            block_cfg_row = latest_one(db, conn, "tracking_block_config", project_id=round_row["project_id"], block_id=bid)
            if not block_cfg_row or not block_cfg_row["payload"].get("enabled"):
                continue
            block_cfg = block_cfg_row["payload"]
            block_row = db.record(conn, bid, kind="block")
            full_chip = is_full_chip_block(project_row["payload"].get("name"), block_row["payload"].get("name"))
            req_corners = effective_corners(proj_cfg, block_cfg)
            stage_filter = block_cfg.get("design_stage")
            uploads = db.uploads(conn, project_id=round_row["project_id"], block_id=bid, state="ready", limit=500)
            chosen: dict[str, tuple[dict, dict]] = {}
            for upload in uploads:
                upload_stage = ((upload.get("input_contract") or {}).get("metadata") or {}).get("design_stage")
                if stage_filter and upload_stage and stage_filter != upload_stage:
                    continue
                vinfo = resolve_version_info(db, conn, upload["version_id"], bid, host_db=host_db)
                if vinfo.get("net_ver") != net_ver:
                    continue
                for corner in req_corners:
                    cid = corner.corner_id()
                    if cid not in chosen and corner.matches_upload(upload):
                        chosen[cid] = (upload, vinfo)
            for corner in req_corners:
                cid = corner.corner_id()
                if cid in chosen:
                    upload, vinfo = chosen[cid]
                    slot = upsert_slot(
                        db, conn, round_row=round_row, block_id=bid, corner=corner,
                        is_required=True, is_full_chip=full_chip, upload=upload,
                        version_info=vinfo, actor=ACTOR,
                    )
                    if slot["payload"].get("status") not in ("completed",):
                        slots_to_advance.append(slot["id"])
    advanced = []
    for sid in slots_to_advance:
        res = advance_slot(db, settings, sid, host_db=host_db)
        if res is not None:
            advanced.append(res)
    due = parse_dt(round_row["payload"].get("deadline_at"))
    if due is not None and due <= now():
        maybe_dispatch_top_mail(db, round_id, trigger="deadline")
    else:
        maybe_dispatch_top_mail(db, round_id, trigger="all_completed")
    return advanced
