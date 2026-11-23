"""OA API logic for NET_VER automatic tracking, settings, preview, and mail snapshots.

Never imports area_power.vwp_server. Cross-server continuation is communicated
strictly through DB jobs (job_kind='compare' or 'judge', consumer_server='vwp_server').
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from area_power.common.ai import capture_context
from area_power.common.analysis import advisory_lock, existing_dedupe, observation_view
from area_power.common.jsonutil import digest
from area_power.common.tracking import (
    BlockTrackingConfigInput,
    CornerSpec,
    DEFAULT_DEADLINE_DAYS,
    DEFAULT_DEADLINE_TIME,
    DEFAULT_DEADLINE_TIMEZONE,
    ExpectationApprovalInput,
    ExpectationInput,
    ExpectationProposalInput,
    ProjectTrackingConfigInput,
    RoundInput,
    compute_deadline,
    effective_corners,
    evaluate_typical_and_same_condition,
    is_full_chip_block,
    match_approved_expectations,
    render_block_mail_text,
    render_top_mail_text,
    resolve_versioned_spec,
    round_dedupe_key,
)
from area_power.common.triage import Thresholds, build_card, select_items, side_context
from common.sanity_common import ReviewError
from oa.sanity_ai_task_defaults import DEFAULT_AREA_RETRY_LIMIT, TaskDefaults
from .service import Input, reject

D = Decimal


def _is_tracking_admin(service) -> bool:
    access = getattr(service, "access", None)
    return access is None or access.development or bool(access.is_admin)


def _require_admin(service) -> None:
    if not _is_tracking_admin(service):
        raise ReviewError("FORBIDDEN", "Administrator access is required for tracking configuration.")


def _host_db(service):
    store = getattr(getattr(service.access, "actor", None), "access", None)
    return getattr(store, "db", None)


def _latest_one(service, conn, kind: str, **filters) -> dict[str, Any] | None:
    rows = service.db.records(conn, kind, latest=True, limit=1, **filters)
    return rows[0] if rows else None


def _mail_delivery_info(service, conn, mail_record: dict | None, *, include_recipients: bool = True) -> dict[str, Any]:
    if mail_record is None:
        return {"state": "waiting", "mail_id": None, "job_state": None, "phase": None, "error": None}
    jobs = service.db.records(conn, "job", parent_id=mail_record["id"], limit=1)
    job = jobs[0] if jobs else None
    emissions = list((job or {}).get("payload", {}).get("emissions") or [])
    last = emissions[-1] if emissions else {}
    phase = last.get("phase")
    if phase == "accepted":
        state = "accepted"
    elif phase == "partially_accepted":
        state = "partially_accepted"
    elif phase == "ambiguous":
        state = "ambiguous"
    elif phase == "rejected":
        state = "rejected"
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


def _block_live_row(service, conn, round_row: dict, project_row: dict, block_id: UUID, proj_cfg: dict, block_cfgs: dict, *, include_recipients: bool = True) -> dict[str, Any]:
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
    next_action = {"kind": "configure", "label": "추적 설정"}

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

    # Choose the clearest next action for the operator
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
    latest_block_mail = _mail_delivery_info(service, conn, block_mails[0] if block_mails else None, include_recipients=include_recipients)

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
    is_admin = _is_tracking_admin(service)
    with service.db.connect() as conn:
        project_row = service.db.record(conn, project_id, kind="project")
        proj_cfg_row = _latest_one(service, conn, "tracking_project_config", project_id=project_id)
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
                "top_mail": _mail_delivery_info(service, conn, top_mail_row, include_recipients=is_admin),
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

        # Check current admin's VWP AREA_TRIAGE readiness (omitted for non-admin viewers)
        ai_model = None
        if is_admin:
            host = _host_db(service)
            ai_model = {"ready": False, "error": "AI_AUTHENTICATED_REQUEST_REQUIRED"}
            if host is not None:
                from oa.sanity_ai_connections import ConnectionStore
                _, selected, default = ConnectionStore(host).task_selection(service.access.actor.user_id, "AREA_TRIAGE")
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


def save_project_config(service, project_id: UUID, body: ProjectTrackingConfigInput, actor: str) -> dict[str, Any]:
    _require_admin(service)
    with service.db.connect() as conn:
        project = service.db.record(conn, project_id, kind="project")
        service.authorize(conn, project, write=True)
        service.lock(conn, ["tracking_project_config", str(project_id)])
        existing = _latest_one(service, conn, "tracking_project_config", project_id=project_id)
        if existing:
            if body.expected_row_version is not None and body.expected_row_version != existing["row_version"]:
                reject("Project tracking configuration changed; reload before saving.", "conflict", 409)
        elif body.expected_row_version is not None:
            reject("Expected row version requires an existing configuration.", "conflict", 409)

        host = _host_db(service)
        ai_context = existing["payload"].get("ai_context") if existing else {}
        retry_limit = existing["payload"].get("retry_limit", DEFAULT_AREA_RETRY_LIMIT) if existing else DEFAULT_AREA_RETRY_LIMIT
        if body.enabled:
            if host is not None:
                settings = TaskDefaults(host).current()
                retry_limit = settings["area_retry_limit"]
                ai_context = capture_context(service.access, tasks=("AREA_TRIAGE",), defaults=settings["defaults"], vwp_only=True)
                if ai_context["AREA_TRIAGE"].get("error"):
                    reject("Central VWP project comparison AI is unavailable for activation.", ai_context["AREA_TRIAGE"]["error"], 409)

        payload = {
            **body.model_dump(mode="python", exclude={"expected_row_version"}),
            "owner_user_id": actor,
            "ai_context": ai_context,
            "retry_limit": retry_limit,
        }
        fields: dict[str, Any] = {"project_id": project_id, "state": "active" if body.enabled else "paused"}
        if existing:
            fields.update(
                entity_id=existing["entity_id"],
                revision=existing["revision"] + 1,
                row_version=existing["row_version"] + 1,
                previous_id=existing["id"],
            )
        saved = service.db.insert_record(conn, "tracking_project_config", payload, actor, **fields)
        return {"id": saved["id"], "row_version": saved["row_version"], **{k: v for k, v in saved["payload"].items() if k != "ai_context"}}


def save_block_config(service, project_id: UUID, block_id: UUID, body: BlockTrackingConfigInput, actor: str) -> dict[str, Any]:
    _require_admin(service)
    with service.db.connect() as conn:
        block = service.db.record(conn, block_id, kind="block")
        if block.get("project_id") != project_id:
            reject("Block does not belong to the chosen project.", "scope_mismatch")
        service.authorize(conn, block, write=True)
        service.lock(conn, ["tracking_block_config", str(project_id), str(block_id)])
        existing = _latest_one(service, conn, "tracking_block_config", project_id=project_id, block_id=block_id)
        if existing:
            if body.expected_row_version is not None and body.expected_row_version != existing["row_version"]:
                reject("Block tracking configuration changed; reload before saving.", "conflict", 409)
        elif body.expected_row_version is not None:
            reject("Expected row version requires an existing configuration.", "conflict", 409)

        # Allow linking baseline block & version before any CSV exists; validate ownership cleanly.
        baseline_project_id = body.baseline_project_id
        if body.comparison_kind == "previous_project":
            if baseline_project_id is not None:
                service.designated_baseline(conn, project_id, baseline_project_id)
            if body.baseline_block_id is not None:
                bblock = service.db.record(conn, body.baseline_block_id, kind="block")
                if baseline_project_id and bblock["project_id"] != baseline_project_id:
                    reject("Baseline block does not belong to baseline project.", "scope_mismatch")
                baseline_project_id = bblock["project_id"]
                service.designated_baseline(conn, project_id, baseline_project_id)
        else:
            baseline_project_id = project_id
            if body.baseline_block_id is not None and body.baseline_block_id != block_id:
                reject("Previous-version tracking must compare within the same block.", "scope_mismatch")

        if body.baseline_version_id is not None:
            bver = service.db.record(conn, body.baseline_version_id, kind="version")
            if body.comparison_kind == "previous_project":
                if baseline_project_id and bver["project_id"] != baseline_project_id:
                    reject("Baseline version does not belong to baseline project.", "scope_mismatch")
                baseline_project_id = bver["project_id"]
                service.designated_baseline(conn, project_id, baseline_project_id)
            else:
                if bver["project_id"] != project_id:
                    reject("Previous-version baseline must belong to the current project.", "scope_mismatch")
            if body.baseline_block_id and bver.get("block_id") and bver["block_id"] != body.baseline_block_id:
                reject("Baseline version does not belong to the chosen baseline block.", "scope_mismatch")

        # Validate approved baseline executions and populate full scope fields for AreaAccess
        normalized_baselines = []
        for ref in body.approved_baselines:
            bu = service.ready_upload(conn, ref.upload_id)
            if baseline_project_id and bu["project_id"] != baseline_project_id:
                reject("Approved baseline upload belongs to another project.", "scope_mismatch")
            if body.baseline_block_id and bu["block_id"] != body.baseline_block_id:
                reject("Approved baseline upload belongs to another block.", "scope_mismatch")
            if body.baseline_version_id and bu["version_id"] != body.baseline_version_id:
                reject("Approved baseline upload belongs to another baseline version.", "scope_mismatch")
            normalized_baselines.append({
                "corner_id": ref.corner_id,
                "upload_id": bu["id"],
                "version_id": bu["version_id"],
                "project_id": bu["project_id"],
                "block_id": bu["block_id"],
                "approved_by": actor,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            })

        if body.approved_mapping_id is not None:
            mapping = service.db.record(conn, body.approved_mapping_id, kind="mapping")
            if str(mapping["payload"].get("current_block_id")) != str(block_id):
                reject("Approved mapping belongs to another block.", "scope_mismatch")

        proj_cfg_row = _latest_one(service, conn, "tracking_project_config", project_id=project_id)
        proj_cfg = proj_cfg_row["payload"] if proj_cfg_row else {}
        if proj_cfg.get("enabled") and body.enabled:
            eff = body.required_corners or [CornerSpec.model_validate(c) for c in (proj_cfg.get("default_required_corners") or [])]
            if not eff:
                reject("At least one required corner must be specified on the project or block.", "required_corner_missing")

        payload = {
            **body.model_dump(mode="python", exclude={"expected_row_version", "approved_baselines"}),
            "baseline_project_id": baseline_project_id,
            "approved_baselines": normalized_baselines,
        }
        fields: dict[str, Any] = {"project_id": project_id, "block_id": block_id, "state": "active" if body.enabled else "paused"}
        if existing:
            fields.update(
                entity_id=existing["entity_id"],
                revision=existing["revision"] + 1,
                row_version=existing["row_version"] + 1,
                previous_id=existing["id"],
            )
        saved = service.db.insert_record(conn, "tracking_block_config", payload, actor, **fields)

        # Enqueue DB continuation jobs for any slots of this block waiting on baseline/mapping approval.
        waiting_slots = [
            slot
            for slot in service.all_records(conn, "tracking_slot", project_id=project_id, block_id=block_id)
            if slot["payload"].get("status") in ("waiting_baseline", "mapping_review") and slot["payload"].get("latest_upload_id")
        ]
        for slot in waiting_slots:
            service.db.enqueue(
                conn, "compare", actor=actor, parent_id=slot["id"],
                payload={"reason": "block_config_updated", "config_id": saved["id"]},
                dedupe_key=digest(["tracking_slot_resume", str(slot["id"]), str(saved["id"])]),
                project_id=project_id, block_id=block_id,
            )

        return {"id": saved["id"], "block_id": block_id, "row_version": saved["row_version"], **saved["payload"]}


def save_round(service, project_id: UUID, body: RoundInput, actor: str) -> dict[str, Any]:
    _require_admin(service)
    with service.db.connect() as conn:
        project = service.db.record(conn, project_id, kind="project")
        service.authorize(conn, project, write=True)
        for bid in body.target_block_ids:
            block = service.db.record(conn, bid, kind="block")
            if block.get("project_id") != project_id:
                reject("Target block does not belong to the chosen project.", "scope_mismatch")

        proj_cfg_row = _latest_one(service, conn, "tracking_project_config", project_id=project_id)
        proj_cfg = proj_cfg_row["payload"] if proj_cfg_row else {}
        days = body.deadline_days if body.deadline_days is not None else int(proj_cfg.get("deadline_days") or DEFAULT_DEADLINE_DAYS)
        dtime = body.deadline_time or proj_cfg.get("deadline_time") or DEFAULT_DEADLINE_TIME
        dtz = body.deadline_timezone or proj_cfg.get("deadline_timezone") or DEFAULT_DEADLINE_TIMEZONE
        deadline_info = compute_deadline(body.netlist_arrived_at, days, dtime, dtz)

        rkey = round_dedupe_key(project_id, body.net_ver)
        service.lock(conn, ["tracking_round", str(project_id), body.net_ver])
        existing = existing_dedupe(service.db.raw if service.access is not None else service.db, conn, "tracking_round", rkey, lock=True)
        applied = {
            "deadline_days": days,
            "deadline_time": dtime,
            "deadline_timezone": dtz,
            "top_recipients": proj_cfg.get("top_recipients") or [],
            "default_required_corners": proj_cfg.get("default_required_corners") or [],
            "typical_spec": resolve_versioned_spec(proj_cfg.get("typical_specs") or [], body.net_ver),
            "power_target": resolve_versioned_spec(proj_cfg.get("power_targets") or [], body.net_ver),
        }
        if existing is not None:
            service.authorize(conn, existing, write=True)
            if body.expected_row_version is not None and body.expected_row_version != existing["row_version"]:
                reject("NET_VER round changed; reload before editing.", "conflict", 409)
            payload = {
                **existing["payload"],
                **deadline_info,
                "target_block_ids": list(body.target_block_ids),
                "applied_config_snapshot": applied,
            }
            saved = service.db.update_record(conn, existing["id"], actor, payload=payload)
        else:
            payload = {
                "net_ver": body.net_ver,
                **deadline_info,
                "target_block_ids": list(body.target_block_ids),
                "applied_config_snapshot": applied,
                "top_mail_state": "waiting",
                "top_mail_record_id": None,
                "top_mail_trigger": None,
                "top_mail_frozen_at": None,
            }
            saved = service.db.insert_record(
                conn, "tracking_round", payload, actor, state="active",
                project_id=project_id, dedupe_key=rkey,
            )

        # Ask VWP worker via DB queue to reconcile any already-arrived ready uploads for this round.
        service.db.enqueue(
            conn, "compare", actor=actor, parent_id=saved["id"],
            payload={"reason": "round_saved", "row_version": saved["row_version"]},
            dedupe_key=digest(["tracking_round_reconcile", str(saved["id"]), saved["row_version"]]),
            project_id=project_id,
        )
        return {"id": saved["id"], "row_version": saved["row_version"], **saved["payload"]}


def reconcile_round(service, round_id: UUID, actor: str) -> dict[str, Any]:
    _require_admin(service)
    with service.db.connect() as conn:
        round_row = service.db.record(conn, round_id, kind="tracking_round")
        service.authorize(conn, round_row, write=True)
        job = service.db.enqueue(
            conn, "compare", actor=actor, parent_id=round_id,
            payload={"reason": "manual_reconcile"},
            dedupe_key=digest(["tracking_round_manual_reconcile", str(round_id), str(uuid4())]),
            project_id=round_row["project_id"],
        )
        return {"round_id": round_id, "job": job}


def save_expectation(service, project_id: UUID, block_id: UUID, body: ExpectationInput, actor: str, *, entity_id: UUID | None = None) -> dict[str, Any]:
    _require_admin(service)
    with service.db.connect() as conn:
        block = service.db.record(conn, block_id, kind="block")
        if block.get("project_id") != project_id:
            reject("Block does not belong to the chosen project.", "scope_mismatch")
        service.authorize(conn, block, write=True)
        previous = None
        if entity_id is not None:
            service.lock(conn, ["tracking_expectation", str(entity_id)])
            previous = service.db.latest_record(conn, "tracking_expectation", entity_id, lock=True)
            if body.expected_row_version is not None and body.expected_row_version != previous["row_version"]:
                reject("Expectation changed; reload before editing.", "conflict", 409)
        payload = {
            **body.model_dump(mode="python", exclude={"expected_row_version"}),
            "approved_by": actor,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        fields: dict[str, Any] = {
            "project_id": project_id, "block_id": block_id,
            "state": "archived" if body.archived else "active",
        }
        if previous:
            fields.update(
                entity_id=previous["entity_id"],
                revision=previous["revision"] + 1,
                row_version=previous["row_version"] + 1,
                previous_id=previous["id"],
            )
        saved = service.db.insert_record(conn, "tracking_expectation", payload, actor, **fields)
        return {"id": saved["id"], "entity_id": saved["entity_id"], "row_version": saved["row_version"], **saved["payload"]}


def propose_expectations(service, project_id: UUID, block_id: UUID, body: ExpectationProposalInput, actor) -> dict[str, Any]:
    _require_admin(service)
    host = getattr(actor, "access", None)
    if actor.auth_method != "sso" or host is None:
        reject("SSO is required for AI expectation proposals.", "AI_AUTHENTICATED_REQUEST_REQUIRED", 403)
    with service.db.connect() as conn:
        block = service.db.record(conn, block_id, kind="block")
        if block.get("project_id") != project_id:
            reject("Block does not belong to the chosen project.", "scope_mismatch")
        service.authorize(conn, block, write=True)
        key = digest(["expected_change_proposal", actor.user_id, str(body.request_id)])
        advisory_lock(conn, key)
        old = existing_dedupe(service.db.raw if service.access is not None else service.db, conn, "expected_change_proposal", key)
        if old:
            service.authorize(conn, old, write=True)
            return {"id": old["id"], "state": old["state"]}
        settings = TaskDefaults(host.db).current()
        pin = capture_context(service.access, tasks=("AREA_TRIAGE",), defaults=settings["defaults"], vwp_only=True)
        if pin["AREA_TRIAGE"].get("error"):
            reject("Project comparison model is unavailable.", pin["AREA_TRIAGE"]["error"], 409)
        payload = {
            "effective_net_ver": body.effective_net_ver,
            "source_text": body.source_text,
            "source_note_id": body.source_note_id,
            "ai_context": pin,
            "retry_limit": settings["area_retry_limit"],
        }
        run = service.db.insert_record(
            conn, "expected_change_proposal", payload, actor.user_id, state="queued",
            project_id=project_id, block_id=block_id, dedupe_key=key,
        )
        service.db.enqueue(
            conn, "judge", actor=actor.user_id, parent_id=run["id"], payload={},
            project_id=project_id, block_id=block_id,
        )
        return {"id": run["id"], "state": run["state"]}


def approve_expectations(service, proposal_id: UUID, body: ExpectationApprovalInput, actor: str) -> dict[str, Any]:
    _require_admin(service)
    with service.db.connect() as conn:
        run = service.db.record(conn, proposal_id, kind="expected_change_proposal", lock=True)
        service.authorize(conn, run, write=True)
        if run["state"] != "completed":
            reject("Expectation proposal is not completed yet.", "proposal_not_ready", 409)
        choices = (run["payload"].get("result") or {}).get("expectations") or []
        selected = sorted(set(body.selected))
        if not selected or selected[0] < 0 or selected[-1] >= len(choices):
            reject("Unknown expectation choice.", "invalid_request")
        created = []
        for idx in selected:
            raw_item = choices[idx]
            exp_body = ExpectationInput(
                effective_net_ver=raw_item.get("effective_net_ver") or run["payload"].get("effective_net_ver"),
                target_paths=raw_item["target_paths"],
                scope=raw_item.get("scope") or "subtree",
                metric=raw_item["metric"],
                comparison_condition=raw_item.get("comparison_condition") or "any",
                direction=raw_item["direction"],
                min_pct=raw_item.get("min_pct"),
                max_pct=raw_item.get("max_pct"),
                quote=raw_item.get("quote") or "",
                source_note_id=run["payload"].get("source_note_id"),
            )
            payload = {
                **exp_body.model_dump(mode="python", exclude={"expected_row_version"}),
                "proposal_id": run["id"],
                "approved_by": actor,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            }
            saved = service.db.insert_record(
                conn, "tracking_expectation", payload, actor, state="active",
                project_id=run["project_id"], block_id=run["block_id"],
            )
            created.append({"id": saved["id"], **saved["payload"]})
        service.db.update_record(
            conn, proposal_id, actor,
            payload={**run["payload"], "approval": {"selected": selected, "approved_by": actor, "created_ids": [c["id"] for c in created]}},
        )
        return {"approved": created}


class PreviewInput(Input):
    net_ver: str | None = Field(default=None, max_length=200)


def preview_tracking(service, project_id: UUID, body: PreviewInput) -> dict[str, Any]:
    """Pre-activation preview: evaluate existing uploads & render block/TOP mail previews without sending."""
    _require_admin(service)
    data = overview(service, project_id)
    rounds = data["rounds"]
    chosen = next((r for r in rounds if r["net_ver"] == body.net_ver), None) if body.net_ver else (rounds[0] if rounds else None)
    if chosen is None:
        return {
            "round": None,
            "top_mail_preview": None,
            "block_mail_previews": [],
        }
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


def _render_report_text(kind: str, snapshot: dict[str, Any]) -> tuple[str, str]:
    if kind == "tracking_top_mail":
        return render_top_mail_text(snapshot)
    return render_block_mail_text(snapshot)


def project_reports(service, project_id: UUID) -> dict[str, Any]:
    """Read-only web view of sent TOP/block reports and live round summaries (no recipients for non-admins)."""
    data = overview(service, project_id)
    is_admin = _is_tracking_admin(service)
    with service.db.connect() as conn:
        top_rows = service.all_records(conn, "tracking_top_mail", project_id=project_id)
        block_rows = service.all_records(conn, "tracking_block_mail", project_id=project_id)
        all_rows = sorted(
            [*top_rows, *block_rows],
            key=lambda r: str(r.get("created_at") or ""),
            reverse=True,
        )
        rounds_by_id = {str(r["id"]): r for r in data["rounds"]}
        sent_reports = []
        for row in all_rows:
            snap = row.get("payload", {}).get("snapshot") or {}
            delivery = _mail_delivery_info(service, conn, row, include_recipients=is_admin)
            subject, body = _render_report_text(row["record_type"], snap)
            rid = str(row.get("parent_id") or "")
            round_info = rounds_by_id.get(rid)
            has_newer = False
            if round_info:
                if row["record_type"] == "tracking_top_mail":
                    has_newer = bool(round_info.get("has_post_top_revisions"))
                else:
                    blk = next((b for b in round_info["blocks"] if str(b["block_id"]) == str(row.get("block_id"))), None)
                    has_newer = bool(blk and blk.get("post_top_mail_revised"))
            sent_reports.append({
                "id": row["id"],
                "kind": row["record_type"],
                "round_id": row.get("parent_id"),
                "net_ver": snap.get("net_ver") or (round_info["net_ver"] if round_info else ""),
                "block_id": row.get("block_id"),
                "block_name": snap.get("block_name") or ("Full-chip · TOP 요약" if row["record_type"] == "tracking_top_mail" else ""),
                "is_full_chip": bool(snap.get("is_full_chip")) if row["record_type"] == "tracking_block_mail" else True,
                "revision_label": snap.get("revision_label") or "",
                "trigger": row.get("payload", {}).get("trigger") or snap.get("trigger") or "",
                "created_at": row.get("created_at"),
                "frozen_at": snap.get("frozen_at") or row.get("created_at"),
                "delivery": delivery,
                "has_newer_results": has_newer,
                "subject": subject,
                "body": body,
                "snapshot": snap,
            })

    live_reports = []
    for chosen in data["rounds"]:
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
        block_items = []
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
            block_items.append({
                "block_id": block["block_id"],
                "block_name": block["block_name"],
                "is_full_chip": block["is_full_chip"],
                "revision_label": block["revision_label"],
                "mail_sent": bool((block.get("block_mail") or {}).get("mail_id")),
                "subject": b_subj,
                "body": b_body,
                "snapshot": bsnap,
            })
        live_reports.append({
            "round_id": chosen["id"],
            "net_ver": chosen["net_ver"],
            "deadline_date": chosen.get("deadline_date"),
            "deadline_time": chosen.get("deadline_time"),
            "top_mail_sent": bool((chosen.get("top_mail") or {}).get("mail_id")),
            "top_report": {"subject": top_subject, "body": top_body, "snapshot": top_snapshot},
            "block_reports": block_items,
        })

    return {
        "project_id": project_id,
        "project_name": data["project_name"],
        "sent_reports": sent_reports,
        "live_reports": live_reports,
    }


def mail_snapshot(service, mail_id: UUID) -> dict[str, Any]:
    """Open a frozen mail snapshot (block or TOP) alongside pointers to the latest live status."""
    is_admin = _is_tracking_admin(service)
    with service.db.connect() as conn:
        row = service.db.record(conn, mail_id)
        if row["record_type"] not in ("tracking_block_mail", "tracking_top_mail"):
            reject("Record is not a tracking mail snapshot.", "not_found", 404)
        delivery = _mail_delivery_info(service, conn, row, include_recipients=is_admin)
        round_row = service.db.record(conn, row["parent_id"], kind="tracking_round")
        latest_slots = service.all_records(conn, "tracking_slot", parent_id=round_row["id"])
        has_newer = any(
            s["payload"].get("post_top_mail_revised")
            for s in latest_slots
            if row["record_type"] == "tracking_top_mail" or str(s["block_id"]) == str(row.get("block_id"))
        )
        snap = row["payload"].get("snapshot") or {}
        subject, body = _render_report_text(row["record_type"], snap)
        return {
            "id": row["id"],
            "kind": row["record_type"],
            "project_id": row["project_id"],
            "block_id": row.get("block_id"),
            "round_id": round_row["id"],
            "created_at": row.get("created_at"),
            "delivery": delivery,
            "subject": subject,
            "body": body,
            "snapshot": snap,
            "has_newer_results": has_newer,
        }
