"""OA NET_VER tracking configuration, rounds, reconcile, and expectation approval mutations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from common.area_power.analysis import advisory_lock, existing_dedupe
from common.area_power.jsonutil import digest
from common.area_power.tracking import (
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
    resolve_versioned_spec,
    round_dedupe_key,
)
from oa.sanity_ai_task_defaults import DEFAULT_AREA_RETRY_LIMIT, TaskDefaults
from ..ai_context import capture_context
from ..schemas import reject
from .overview import host_db, latest_one, require_admin


def save_project_config(service, project_id: UUID, body: ProjectTrackingConfigInput, actor: str) -> dict[str, Any]:
    require_admin(service)
    with service.db.connect() as conn:
        project = service.db.record(conn, project_id, kind="project")
        service.authorize(conn, project, write=True)
        service.lock(conn, ["tracking_project_config", str(project_id)])
        existing = latest_one(service, conn, "tracking_project_config", project_id=project_id)
        if existing:
            if body.expected_row_version is not None and body.expected_row_version != existing["row_version"]:
                reject("Project tracking configuration changed; reload before saving.", "conflict", 409)
        elif body.expected_row_version is not None:
            reject("Expected row version requires an existing configuration.", "conflict", 409)

        host = host_db(service)
        ai_context = existing["payload"].get("ai_context") if existing else {}
        retry_limit = existing["payload"].get("retry_limit", DEFAULT_AREA_RETRY_LIMIT) if existing else DEFAULT_AREA_RETRY_LIMIT
        if body.enabled and host is not None:
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
    require_admin(service)
    with service.db.connect() as conn:
        block = service.db.record(conn, block_id, kind="block")
        if block.get("project_id") != project_id:
            reject("Block does not belong to the chosen project.", "scope_mismatch")
        service.authorize(conn, block, write=True)
        service.lock(conn, ["tracking_block_config", str(project_id), str(block_id)])
        existing = latest_one(service, conn, "tracking_block_config", project_id=project_id, block_id=block_id)
        if existing:
            if body.expected_row_version is not None and body.expected_row_version != existing["row_version"]:
                reject("Block tracking configuration changed; reload before saving.", "conflict", 409)
        elif body.expected_row_version is not None:
            reject("Expected row version requires an existing configuration.", "conflict", 409)

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
            elif bver["project_id"] != project_id:
                reject("Previous-version baseline must belong to the current project.", "scope_mismatch")
            if body.baseline_block_id and bver.get("block_id") and bver["block_id"] != body.baseline_block_id:
                reject("Baseline version does not belong to the chosen baseline block.", "scope_mismatch")

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

        proj_cfg_row = latest_one(service, conn, "tracking_project_config", project_id=project_id)
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
    require_admin(service)
    with service.db.connect() as conn:
        project = service.db.record(conn, project_id, kind="project")
        service.authorize(conn, project, write=True)
        for bid in body.target_block_ids:
            block = service.db.record(conn, bid, kind="block")
            if block.get("project_id") != project_id:
                reject("Target block does not belong to the chosen project.", "scope_mismatch")

        proj_cfg_row = latest_one(service, conn, "tracking_project_config", project_id=project_id)
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

        service.db.enqueue(
            conn, "compare", actor=actor, parent_id=saved["id"],
            payload={"reason": "round_saved", "row_version": saved["row_version"]},
            dedupe_key=digest(["tracking_round_reconcile", str(saved["id"]), saved["row_version"]]),
            project_id=project_id,
        )
        return {"id": saved["id"], "row_version": saved["row_version"], **saved["payload"]}


def reconcile_round(service, round_id: UUID, actor: str) -> dict[str, Any]:
    require_admin(service)
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


def save_expectation(
    service, project_id: UUID, block_id: UUID, body: ExpectationInput, actor: str, *, entity_id: UUID | None = None
) -> dict[str, Any]:
    require_admin(service)
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
    require_admin(service)
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
    require_admin(service)
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
