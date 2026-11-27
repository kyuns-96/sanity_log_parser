"""OA Floorplan Review domain service: versions, metrics join, checklist, Designer sandbox, Confluence."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field

from common.area_power.analysis import advisory_lock, existing_dedupe
from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest, dumps
from common.area_power.layered_csv import load_base_upload, read_layered_hierarchy
from common.floorplan import (
    ConfluenceAttachment,
    ConfluenceCreateRequest,
    ConfluenceNotConfigured,
    ConfluencePublishError,
    DatapathFlow,
    FloorplanScene,
    MacroOverride,
    ReviewPin,
    build_confluence_storage_xhtml,
    build_standalone_review_html,
    check_placement_collisions,
    export_delta_csv,
    export_icc2_tcl,
    export_innovus_tcl,
    is_macro_modified,
    parse_confluence_parent_config,
    reject_raw_def_or_lef,
    render_scene_png,
    render_scene_svg,
)
from common.floorplan.temp_artifact_fallback import (  # [TEMP_ARTIFACT_FALLBACK]
    is_temp_fallback_enabled,
    should_write_temp_fallback,
    temp_get_artifact,
    temp_list_version_artifacts,
)
from oa.integration.corporate_confluence import create_confluence_publisher

KST = timezone(timedelta(hours=9))

DEFAULT_TOP_CHECKLIST: list[dict[str, Any]] = [
    {"id": "top_subblock_aspect", "title": "Sub-block 위치 · 크기 · 종횡비 적정성", "description": "각 Sub-block 경계와 Aspect Ratio, 인접 블록 배치가 아키텍처 의도와 일치하는지 확인", "active": True},
    {"id": "top_hard_ip", "title": "Hard IP / PHY / PLL 배치 및 Keepout", "description": "아날로그/고속 IP 위치, Bump/IO 거리 및 주변 노이즈 격리 확인", "active": True},
    {"id": "top_channel_feedthru", "title": "블록 간 라우팅 채널 · Feedthrough 여유", "description": "협소 채널 구간 및 상위 글로벌 버스 통과 경로 병목 여부 확인", "active": True},
    {"id": "top_pin_align", "title": "Sub-block 포트 / TOP IO 정렬", "description": "인접 블록 간 인터페이스 핀 대면 정렬 및 우회 배선 유무 확인", "active": True},
    {"id": "top_area_lvt", "title": "TOP · Sub-block Area 및 LVT 비율 균형", "description": "특정 블록의 과도한 면적 증가 또는 SLVT/LVT 편중 여부 확인", "active": True},
]

DEFAULT_SUBBLOCK_CHECKLIST: list[dict[str, Any]] = [
    {"id": "sub_macro_halo", "title": "Memory / Macro 배치 · 방향(Pin-1) · Halo/Channel", "description": "SRAM 핀 방향이 채널을 향하는지, 매크로 간 스탠다드셀 채널 폭이 충분한지 확인", "active": True},
    {"id": "sub_bist_cohesion", "title": "Memory BIST Group 응집도 검토", "description": "동일 BIST 그룹 메모리가 과도하게 분산되어 BIST 배선 혼잡을 유발하지 않는지 확인", "active": True},
    {"id": "sub_datapath_flow", "title": "Data-path 흐름 정합성 (In → Logic → Memory → Out)", "description": "데이터 흐름 순서에 맞게 포트·연산 모듈·메모리가 배치되어 역류/꼬임이 없는지 확인", "active": True},
    {"id": "sub_inst_locality", "title": "주요 Sub-instance 밀집도 및 물리 위치", "description": "핵심 하위 인스턴스가 의도한 구역에 응집되어 있는지 확인", "active": True},
    {"id": "sub_metrics_health", "title": "Area · LVT Ratio · Drive Strength 분포 건전성", "description": "SRAM/CTS 비중, SLVT+LVT 비율, sub-1x 및 D4+/D8+ 구동 셀 분포 이상 유무 확인", "active": True},
]


class ChecklistTemplateItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=600)
    active: bool = True


class SaveChecklistTemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_items: list[ChecklistTemplateItemInput] = Field(min_length=1, max_length=50)
    subblock_items: list[ChecklistTemplateItemInput] = Field(min_length=1, max_length=50)


class ChecklistVerdictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    verdict: Literal["PENDING", "PASS", "WARN", "ACTION", "N/A"] = "PENDING"
    comment: str = Field(default="", max_length=1000)


class SaveFloorplanReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overall_verdict: Literal["PENDING", "PASS", "WARN", "ACTION"] = "PENDING"
    summary_note: str = Field(default="", max_length=4000)
    checklist: list[ChecklistVerdictInput] = Field(default_factory=list, max_length=80)
    review_pins: list[ReviewPin] = Field(default_factory=list, max_length=200)
    datapaths: list[DatapathFlow] = Field(default_factory=list, max_length=30)


class SavePlacementProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=2000)
    overrides: list[MacroOverride] = Field(min_length=1, max_length=4000)


class SaveConfluenceConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: UUID | None = None
    space_key: str = Field(default="", max_length=120)
    parent_url_or_id: str = Field(default="", max_length=1000)


class UploadWebAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_kind: str = Field(default="screenshot", max_length=40)
    view_name: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=240)
    caption: str = Field(default="", max_length=1000)
    filename: str = Field(min_length=1, max_length=255)
    mime_type: Literal["image/png", "image/webp", "image/jpeg", "image/svg+xml"] = "image/png"
    content_base64: str = Field(min_length=4, max_length=24_000_000)


class ConfluencePublishOptionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_overview: bool = True
    include_bist: bool = True
    include_datapath: bool = False
    proposal_id: UUID | None = None
    extra_artifact_ids: list[UUID] = Field(default_factory=list, max_length=10)
    title_suffix: str = Field(default="", max_length=80)


def _artifact_table_exists(conn: Any, schema: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"{schema}.di_design_artifact",)).fetchone()
    return bool(row and row["ok"])


def _load_checklist_template(service: Any, conn: Any, project_id: UUID) -> dict[str, list[dict[str, Any]]]:
    rows = service.db.records(conn, "fp_checklist_template", project_id=project_id, latest=True, limit=1)
    if rows:
        p = rows[0]["payload"]
        return {
            "top_items": list(p.get("top_items") or DEFAULT_TOP_CHECKLIST),
            "subblock_items": list(p.get("subblock_items") or DEFAULT_SUBBLOCK_CHECKLIST),
        }
    return {
        "top_items": [dict(x) for x in DEFAULT_TOP_CHECKLIST],
        "subblock_items": [dict(x) for x in DEFAULT_SUBBLOCK_CHECKLIST],
    }


def _extract_upload_metrics(
    service: Any,
    conn: Any,
    upload_row: dict[str, Any] | None,
    unit_area: float | None = None,
) -> dict[str, Any] | None:
    if not upload_row:
        return None
    root_metrics = ((upload_row.get("root_summary") or {}).get("metrics") or {})

    def _pick(src: dict[str, Any]) -> dict[str, Any]:
        tot_area = src.get("total_area")
        logic_area = src.get("logic_area")
        gc = None
        if unit_area and unit_area > 0 and logic_area is not None:
            try:
                gc = round(float(logic_area) / unit_area)
            except (ValueError, TypeError):
                gc = None
        return {
            "area": {
                "total_area": tot_area,
                "logic_area": logic_area,
                "sram_area": src.get("sram_area"),
                "cts_area": src.get("cts_area"),
                "cts_area_pct": src.get("cts_area_pct"),
                "io_area": src.get("io_area"),
                "others_area": src.get("others_area"),
                "total_cells": src.get("total_cells"),
                "sram_cells": src.get("sram_cells"),
                "gate_count": gc,
            },
            "vth": {
                "vt_slvt_pct": src.get("vt_slvt_pct"),
                "vt_lvt_pct": src.get("vt_lvt_pct"),
                "vt_rvt_pct": src.get("vt_rvt_pct"),
                "vt_hvt_pct": src.get("vt_hvt_pct"),
                "vt_slvt_llp_pct": src.get("vt_slvt_llp_pct"),
                "vt_lvt_llp_pct": src.get("vt_lvt_llp_pct"),
            },
            "drive": {
                "drv_sub1x_pct": src.get("drv_sub1x_pct"),
                "drv_d1_d3_pct": src.get("drv_d1_d3_pct"),
                "drv_d4_plus_pct": src.get("drv_d4_plus_pct"),
                "bins": {
                    k: src.get(f"drv_{k}_pct")
                    for k in ("D0P5", "D0P7", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D10", "D12", "D14", "D16", "other")
                    if src.get(f"drv_{k}_pct") is not None
                },
            },
        }

    hierarchy_items: list[dict[str, Any]] = []
    try:
        blob_row = service.db.upload(conn, upload_row["id"], include_blob=True)
        base_row = load_base_upload(service.db, conn, blob_row) if is_layered_needed(blob_row) else None
        _, items = read_layered_hierarchy(blob_row, service.db.raw.settings.max_uncompressed_bytes, base_upload=base_row)
        for item in items:
            if item.get("depth", 0) <= 3:
                m = item.get("metrics") or {}
                hierarchy_items.append({
                    "path": item.get("path") or "",
                    "name": item.get("name") or "",
                    "depth": item.get("depth") or 0,
                    **_pick(m),
                })
    except Exception:
        hierarchy_items = []

    return {
        "upload_id": str(upload_row["id"]),
        "version_id": str(upload_row["version_id"]),
        "original_filename": upload_row.get("original_filename"),
        "process_label": upload_row.get("process_label"),
        "root": _pick(root_metrics),
        "instances": hierarchy_items[:300],
    }


def is_layered_needed(upload_row: dict[str, Any]) -> bool:
    contract = upload_row.get("input_contract") or {}
    return contract.get("storage_mode") == "power_layer"


def get_workspace_overview(service: Any, project_id: UUID, block_id: UUID | None = None, pat_status: dict | None = None) -> dict[str, Any]:
    with service.db.connect() as conn:
        project = service.db.record(conn, project_id, kind="project")
        table_ready = _artifact_table_exists(conn, service.db.raw.settings.db_schema)
        filters: dict[str, Any] = {"project_id": project_id}
        if block_id:
            filters["block_id"] = block_id
        fp_rows = service.all_records(conn, "fp_version", **filters)
        fp_rows.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)

        review_by_fp: dict[str, dict[str, Any]] = {}
        for rv in service.all_records(conn, "fp_review", **filters, latest=True):
            if rv.get("parent_id"):
                review_by_fp[str(rv["parent_id"])] = rv["payload"]

        proj_conf_rows = service.db.records(conn, "fp_confluence_config", project_id=project_id, latest=True, limit=50)
        proj_conf = next((r["payload"] for r in proj_conf_rows if r.get("block_id") is None), None)
        block_confs = {
            str(r["block_id"]): r["payload"]
            for r in proj_conf_rows
            if r.get("block_id") is not None
        }

        checklist_tpl = _load_checklist_template(service, conn, project_id)
        block_uploads = service.db.uploads(conn, project_id=project_id, **({"block_id": block_id} if block_id else {}), state="ready", limit=100)

        versions_out = []
        for row in fp_rows:
            if row.get("state") == "archived":
                continue
            p = row["payload"]
            rev = review_by_fp.get(str(row["id"])) or {}
            pins = rev.get("review_pins") or []
            action_open = sum(1 for pin in pins if pin.get("severity") == "ACTION" and not pin.get("resolved"))
            versions_out.append({
                "id": str(row["id"]),
                "project_id": str(row["project_id"]),
                "block_id": str(row["block_id"]),
                "fp_ver": p.get("fp_ver") or "",
                "prev_fp_ver": p.get("prev_fp_ver"),
                "scope_kind": p.get("scope_kind") or "subblock",
                "title": p.get("title") or p.get("fp_ver") or "",
                "design_name": p.get("design_name") or "",
                "master_artifact_id": p.get("master_artifact_id"),
                "linked_net_ver": p.get("linked_net_ver"),
                "linked_revision": p.get("linked_revision"),
                "linked_layout": p.get("linked_layout"),
                "linked_eco_num": p.get("linked_eco_num"),
                "linked_area_upload_id": p.get("linked_area_upload_id"),
                "stats": p.get("stats") or {},
                "overall_verdict": rev.get("overall_verdict") or "PENDING",
                "open_action_pins": action_open,
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            })

        return {
            "project_id": str(project["id"]),
            "project_name": (project.get("payload") or {}).get("name") or "",
            "artifact_table_ready": table_ready or is_temp_fallback_enabled(),
            "storage_mode": "di_design_artifact" if table_ready else ("temp_tracking_fallback" if is_temp_fallback_enabled() else "unavailable"),  # [TEMP_ARTIFACT_FALLBACK]
            "versions": versions_out,
            "checklist_template": checklist_tpl,
            "project_confluence": proj_conf or {"space_key": "", "parent_page_id": "", "raw_url": ""},
            "block_confluence": block_confs,
            "available_area_uploads": [
                {
                    "id": str(u["id"]),
                    "block_id": str(u["block_id"]),
                    "version_id": str(u["version_id"]),
                    "original_filename": u.get("original_filename") or "",
                    "process_label": u.get("process_label") or "",
                    "created_at": u.get("created_at"),
                }
                for u in block_uploads
            ],
            "confluence_pat": pat_status or {"registered": False, "revision": 0, "updated_at": None},
        }


def get_version_detail(
    service: Any,
    fp_version_id: UUID,
    *,
    compare_fp_version_id: UUID | None = None,
    area_upload_id: UUID | None = None,
    unit_area: float | None = None,
) -> dict[str, Any]:
    schema = service.db.raw.settings.db_schema
    tbl = sql.Identifier(schema, "di_design_artifact")
    with service.db.connect() as conn:
        fp_row = service.db.record(conn, fp_version_id, kind="fp_version")
        p = fp_row["payload"]
        table_ready = _artifact_table_exists(conn, schema)

        scene: dict[str, Any] | None = None
        attachments: list[dict[str, Any]] = []
        version_tag = f"fp:{p['fp_ver']}"
        art_rows: list[dict[str, Any]] = []
        if table_ready and not should_write_temp_fallback(True):
            art_rows = list(
                conn.execute(
                    sql.SQL(
                        "SELECT id, artifact_kind, view_name, title, caption, mime_type, "
                        "width_px, height_px, byte_length, metadata, created_at "
                        "FROM {} WHERE project_id=%s AND block_id=%s AND version_id=%s AND state='active' "
                        "ORDER BY created_at DESC"
                    ).format(tbl),
                    (p["native_project_id"], p["native_block_id"], version_tag),
                ).fetchall()
            )
        # [TEMP_ARTIFACT_FALLBACK] Merge/fallback from di_area_power_tracking_record when di_design_artifact has no rows yet
        seen_ids = {str(r["id"]) for r in art_rows}
        for tmp_row in temp_list_version_artifacts(
            conn,
            schema,
            native_project_id=p["native_project_id"],
            native_block_id=p["native_block_id"],
            version_tag=version_tag,
            area_project_id=fp_row["project_id"],
            area_block_id=fp_row["block_id"],
        ):
            if str(tmp_row["id"]) not in seen_ids:
                art_rows.append(tmp_row)

        for a in art_rows:
            if a["artifact_kind"] == "floorplan" and a["view_name"] == "vector_master" and scene is None:
                scene = a["metadata"]
            else:
                attachments.append({
                    "id": str(a["id"]),
                    "artifact_kind": a["artifact_kind"],
                    "view_name": a["view_name"] or "",
                    "title": a["title"] or a["view_name"] or "",
                    "caption": a["caption"] or "",
                    "mime_type": a["mime_type"],
                    "byte_length": a["byte_length"],
                    "created_at": a["created_at"],
                })

        # Compare Floorplan version scene if requested
        compare_scene: dict[str, Any] | None = None
        if compare_fp_version_id:
            cmp_row = service.db.record(conn, compare_fp_version_id, kind="fp_version")
            cp = cmp_row["payload"]
            if cp.get("master_artifact_id"):
                cmp_aid = UUID(str(cp["master_artifact_id"]))
                crow = conn.execute(sql.SQL("SELECT metadata FROM {} WHERE id=%s").format(tbl), (cmp_aid,)).fetchone() if table_ready else None
                if crow:
                    compare_scene = crow["metadata"]
                else:
                    # [TEMP_ARTIFACT_FALLBACK]
                    t_cmp = temp_get_artifact(conn, schema, cmp_aid, include_bytes=False)
                    if t_cmp:
                        compare_scene = t_cmp["metadata"]

        # Review + carried-over unresolved ACTION items from prev_fp_ver
        rev_rows = service.db.records(conn, "fp_review", parent_id=fp_version_id, latest=True, limit=1)
        review = rev_rows[0]["payload"] if rev_rows else None
        carried_pins: list[dict[str, Any]] = []
        carried_actions: list[dict[str, Any]] = []
        prev_ver_label = p.get("prev_fp_ver")
        if prev_ver_label:
            prev_candidates = [
                r for r in service.all_records(conn, "fp_version", project_id=fp_row["project_id"], block_id=fp_row["block_id"])
                if (r.get("payload") or {}).get("fp_ver") == prev_ver_label
            ]
            if prev_candidates:
                prev_revs = service.db.records(conn, "fp_review", parent_id=prev_candidates[0]["id"], latest=True, limit=1)
                if prev_revs:
                    prp = prev_revs[0]["payload"]
                    for pin in prp.get("review_pins") or []:
                        if pin.get("severity") in ("ACTION", "WARN") and not pin.get("resolved"):
                            carried_pins.append({**pin, "carried_from_fp_ver": prev_ver_label})
                    for ci in prp.get("checklist") or []:
                        if ci.get("verdict") in ("ACTION", "WARN"):
                            carried_actions.append({**ci, "carried_from_fp_ver": prev_ver_label})

        # Proposals
        prop_rows = service.all_records(conn, "fp_proposal", parent_id=fp_version_id, latest=True)
        prop_rows.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
        proposals = [
            {
                "id": str(r["id"]),
                "entity_id": str(r["entity_id"]),
                "row_version": r["row_version"],
                "created_by": r.get("created_by") or "",
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
                **r["payload"],
            }
            for r in prop_rows
            if r.get("state") != "archived"
        ]

        # Linked Area/Power metrics
        target_uid = area_upload_id or (UUID(str(p["linked_area_upload_id"])) if p.get("linked_area_upload_id") else None)
        upload_row = None
        if target_uid:
            try:
                upload_row = service.db.upload(conn, target_uid)
            except Exception:
                upload_row = None
        if upload_row is None:
            fallback_ups = service.db.uploads(conn, project_id=fp_row["project_id"], block_id=fp_row["block_id"], state="ready", limit=1)
            upload_row = fallback_ups[0] if fallback_ups else None
        metrics = _extract_upload_metrics(service, conn, upload_row, unit_area=unit_area)

        # Publish history
        pub_rows = service.all_records(conn, "fp_confluence_publish", parent_id=fp_version_id)
        pub_rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        publishes = [{"id": str(r["id"]), "created_at": r.get("created_at"), "created_by": r.get("created_by"), **r["payload"]} for r in pub_rows]

        return {
            "fp_version": {"id": str(fp_row["id"]), "project_id": str(fp_row["project_id"]), "block_id": str(fp_row["block_id"]), **p},
            "scene": scene,
            "compare_scene": compare_scene,
            "attachments": attachments,
            "review": review or {
                "overall_verdict": "PENDING",
                "summary_note": "",
                "checklist": [],
                "review_pins": carried_pins,
                "datapaths": [],
            },
            "carried_over": {"prev_fp_ver": prev_ver_label, "pins": carried_pins, "checklist_issues": carried_actions},
            "proposals": proposals,
            "metrics": metrics,
            "publishes": publishes,
        }


def read_artifact_binary(service: Any, artifact_id: UUID, *, thumbnail: bool = False) -> dict[str, Any]:
    schema = service.db.raw.settings.db_schema
    tbl = sql.Identifier(schema, "di_design_artifact")
    with service.db.connect() as conn:
        row = None
        if _artifact_table_exists(conn, schema):
            row = conn.execute(
                sql.SQL(
                    "SELECT area_project_id, mime_type, sha256, artifact_bytes, thumbnail_bytes "
                    "FROM {} WHERE id=%s AND state='active'"
                ).format(tbl),
                (artifact_id,),
            ).fetchone()
        if not row:
            # [TEMP_ARTIFACT_FALLBACK]
            t_row = temp_get_artifact(conn, schema, artifact_id, include_bytes=True)
            if t_row and t_row.get("state") == "active":
                row = t_row
        if not row:
            raise DomainError("Artifact not found.", code="not_found", status_code=404)
        if row["area_project_id"]:
            proj = service.db.record(conn, row["area_project_id"], kind="project")
            service.authorize(conn, proj, write=False)
        if thumbnail and row["thumbnail_bytes"]:
            return {"bytes": bytes(row["thumbnail_bytes"]), "mime_type": "image/png", "etag": bytes(row["sha256"]).hex() + "-thumb"}
        return {"bytes": bytes(row["artifact_bytes"]), "mime_type": row["mime_type"], "etag": bytes(row["sha256"]).hex()}


def save_checklist_template(service: Any, project_id: UUID, body: SaveChecklistTemplateInput, actor: str) -> dict[str, Any]:
    with service.db.connect() as conn:
        proj = service.db.record(conn, project_id, kind="project")
        service.authorize(conn, proj, write=True)
        service.lock(conn, ["fp_checklist_template", str(project_id)])
        existing = service.db.records(conn, "fp_checklist_template", project_id=project_id, latest=True, limit=1)
        payload = body.model_dump(mode="python")
        fields: dict[str, Any] = {"project_id": project_id, "state": "active"}
        if existing:
            prev = existing[0]
            fields.update(entity_id=prev["entity_id"], revision=prev["revision"] + 1, row_version=prev["row_version"] + 1, previous_id=prev["id"])
        saved = service.db.insert_record(conn, "fp_checklist_template", payload, actor, **fields)
        return saved["payload"]


def save_review(service: Any, fp_version_id: UUID, body: SaveFloorplanReviewInput, actor: str) -> dict[str, Any]:
    with service.db.connect() as conn:
        fp_row = service.db.record(conn, fp_version_id, kind="fp_version")
        proj = service.db.record(conn, fp_row["project_id"], kind="project")
        service.authorize(conn, proj, write=True)
        service.lock(conn, ["fp_review", str(fp_version_id)])
        existing = service.db.records(conn, "fp_review", parent_id=fp_version_id, latest=True, limit=1)
        payload = body.model_dump(mode="python")
        fields: dict[str, Any] = {
            "parent_id": fp_version_id,
            "project_id": fp_row["project_id"],
            "block_id": fp_row["block_id"],
            "state": body.overall_verdict.lower(),
        }
        if existing:
            prev = existing[0]
            fields.update(entity_id=prev["entity_id"], revision=prev["revision"] + 1, row_version=prev["row_version"] + 1, previous_id=prev["id"])
        saved = service.db.insert_record(conn, "fp_review", payload, actor, **fields)
        return {"id": str(saved["id"]), "row_version": saved["row_version"], **saved["payload"]}


def _load_master_scene(conn: Any, schema: str, fp_row: dict[str, Any]) -> FloorplanScene:
    p = fp_row["payload"]
    if not p.get("master_artifact_id"):
        raise DomainError("No vector floorplan scene is attached to this version.", code="scene_not_found", status_code=404)
    aid = UUID(str(p["master_artifact_id"]))
    row = None
    if _artifact_table_exists(conn, schema):
        tbl = sql.Identifier(schema, "di_design_artifact")
        row = conn.execute(sql.SQL("SELECT metadata FROM {} WHERE id=%s").format(tbl), (aid,)).fetchone()
    if not row:
        # [TEMP_ARTIFACT_FALLBACK]
        row = temp_get_artifact(conn, schema, aid, include_bytes=False)
    if not row or not row["metadata"]:
        raise DomainError("Master floorplan scene metadata not found.", code="scene_not_found", status_code=404)
    return FloorplanScene.model_validate(row["metadata"])


def save_proposal(service: Any, fp_version_id: UUID, body: SavePlacementProposalInput, actor: str) -> dict[str, Any]:
    schema = service.db.raw.settings.db_schema
    with service.db.connect() as conn:
        fp_row = service.db.record(conn, fp_version_id, kind="fp_version")
        proj = service.db.record(conn, fp_row["project_id"], kind="project")
        service.authorize(conn, proj, write=True)
        scene = _load_master_scene(conn, schema, fp_row)
        collisions = check_placement_collisions(scene, body.overrides)
        orig_by_name = {m.inst_name: m for m in scene.macros}
        changed_count = sum(
            1 for ov in body.overrides
            if ov.inst_name in orig_by_name and is_macro_modified(orig_by_name[ov.inst_name], ov)
        )
        payload = {
            "title": body.title.strip(),
            "note": body.note,
            "overrides": [ov.model_dump(mode="python") for ov in body.overrides],
            "changed_count": changed_count,
            "out_of_bounds_count": len(collisions["out_of_bounds"]),
            "overlap_count": len(collisions["overlaps"]),
            "collisions": collisions,
        }
        fields: dict[str, Any] = {
            "parent_id": fp_version_id,
            "project_id": fp_row["project_id"],
            "block_id": fp_row["block_id"],
            "state": "active",
        }
        if body.proposal_id:
            prev = service.db.record(conn, body.proposal_id, kind="fp_proposal")
            latest_prev = service.db.latest_record(conn, "fp_proposal", prev["entity_id"], lock=True)
            fields.update(
                entity_id=latest_prev["entity_id"],
                revision=latest_prev["revision"] + 1,
                row_version=latest_prev["row_version"] + 1,
                previous_id=latest_prev["id"],
            )
        saved = service.db.insert_record(conn, "fp_proposal", payload, actor, **fields)
        return {"id": str(saved["id"]), "entity_id": str(saved["entity_id"]), "row_version": saved["row_version"], **saved["payload"]}


def export_proposal_script(
    service: Any,
    proposal_id: UUID,
    *,
    fmt: Literal["innovus", "icc2", "csv"] = "innovus",
    only_changed: bool = False,
) -> tuple[str, str, str]:
    schema = service.db.raw.settings.db_schema
    with service.db.connect() as conn:
        prop = service.db.record(conn, proposal_id, kind="fp_proposal")
        proj = service.db.record(conn, prop["project_id"], kind="project")
        service.authorize(conn, proj, write=False)
        fp_row = service.db.record(conn, prop["parent_id"], kind="fp_version")
        scene = _load_master_scene(conn, schema, fp_row)
        overrides = [MacroOverride.model_validate(item) for item in (prop["payload"].get("overrides") or [])]
        title = prop["payload"].get("title") or "proposal"
        fp_ver = fp_row["payload"].get("fp_ver") or "fp"
        safe_base = f"{scene.design_name}_{fp_ver}"
        if fmt == "innovus":
            return (export_innovus_tcl(scene, overrides, proposal_title=title, only_changed=only_changed), f"{safe_base}_innovus.tcl", "text/plain; charset=utf-8")
        if fmt == "icc2":
            return (export_icc2_tcl(scene, overrides, proposal_title=title, only_changed=only_changed), f"{safe_base}_icc2.tcl", "text/plain; charset=utf-8")
        return (export_delta_csv(scene, overrides), f"{safe_base}_delta.csv", "text/csv; charset=utf-8")


def upload_web_attachment(service: Any, fp_version_id: UUID, body: UploadWebAttachmentInput, actor: str) -> dict[str, Any]:
    try:
        raw_bytes = base64.b64decode(body.content_base64, validate=True)
    except Exception as exc:
        raise DomainError("Invalid base64 image payload.", code="invalid_image", status_code=422) from exc
    reject_raw_def_or_lef(raw_bytes, body.filename)
    if not raw_bytes or len(raw_bytes) > 16 * 1024 * 1024:
        raise DomainError("Image size must be between 1 byte and 16 MiB.", code="file_too_large", status_code=413)

    schema = service.db.raw.settings.db_schema
    with service.db.connect() as conn:
        fp_row = service.db.record(conn, fp_version_id, kind="fp_version")
        proj = service.db.record(conn, fp_row["project_id"], kind="project")
        service.authorize(conn, proj, write=True)
        if not _artifact_table_exists(conn, schema) and not is_temp_fallback_enabled():  # [TEMP_ARTIFACT_FALLBACK]
            raise DomainError("di_design_artifact table is not provisioned in PostgreSQL yet.", code="table_not_provisioned", status_code=503)
        p = fp_row["payload"]
        from vwp_bridge.area_power.floorplan_ingest import insert_design_artifact_row
        art = insert_design_artifact_row(
            conn,
            schema,
            artifact_id=uuid4(),
            project_id=p["native_project_id"],
            block_id=p["native_block_id"],
            fp_ver=p["fp_ver"],
            net_ver=p.get("linked_net_ver"),
            revision=p.get("linked_revision"),
            eco_num=p.get("linked_eco_num"),
            layout=p.get("linked_layout"),
            area_project_id=fp_row["project_id"],
            area_block_id=fp_row["block_id"],
            area_version_id=UUID(str(p["linked_area_version_id"])) if p.get("linked_area_version_id") else None,
            area_upload_id=UUID(str(p["linked_area_upload_id"])) if p.get("linked_area_upload_id") else None,
            artifact_kind=body.artifact_kind,
            view_name=body.view_name.strip(),
            title=body.title.strip() or body.view_name.strip(),
            caption=body.caption.strip(),
            metadata={"fp_ver": p["fp_ver"]},
            original_filename=body.filename.strip(),
            mime_type=body.mime_type,
            width_px=None,
            height_px=None,
            artifact_bytes=raw_bytes,
            thumbnail_bytes=raw_bytes if (body.mime_type != "image/svg+xml" and len(raw_bytes) <= 262144) else None,
            uploaded_by=actor,
        )
    return {"artifact_id": str(art["id"]), "deduplicated": art["deduplicated"], "byte_length": art["byte_length"]}


def save_confluence_config(service: Any, project_id: UUID, body: SaveConfluenceConfigInput, actor: str) -> dict[str, Any]:
    space_key, parent_page_id = parse_confluence_parent_config(body.parent_url_or_id, body.space_key)
    if body.parent_url_or_id.strip() and not parent_page_id:
        raise DomainError(
            "Provide a numeric parent pageId or a Confluence URL containing ?pageId=... or /pages/<id> (GET lookup is disabled under Create-Only policy).",
            code="invalid_confluence_target",
            status_code=422,
        )
    with service.db.connect() as conn:
        proj = service.db.record(conn, project_id, kind="project")
        service.authorize(conn, proj, write=True)
        if body.block_id:
            blk = service.db.record(conn, body.block_id, kind="block")
            if blk["project_id"] != project_id:
                raise DomainError("Block does not belong to project.", code="scope_mismatch", status_code=422)
        ckey = digest(["fp_confluence_config", str(project_id), str(body.block_id or "project")])
        advisory_lock(conn, ckey)
        existing = existing_dedupe(service.db.raw, conn, "fp_confluence_config", ckey, lock=True)
        payload = {
            "space_key": space_key,
            "parent_page_id": parent_page_id,
            "raw_url": body.parent_url_or_id.strip(),
        }
        if existing:
            saved = service.db.update_record(conn, existing["id"], actor, payload=payload)
        else:
            saved = service.db.insert_record(
                conn, "fp_confluence_config", payload, actor,
                state="active", project_id=project_id, block_id=body.block_id, dedupe_key=ckey,
            )
        return saved["payload"]


def _build_confluence_package(
    service: Any,
    conn: Any,
    fp_row: dict[str, Any],
    opts: ConfluencePublishOptionsInput,
    reviewer: str,
) -> dict[str, Any]:
    schema = service.db.raw.settings.db_schema
    proj = service.db.record(conn, fp_row["project_id"], kind="project")
    blk = service.db.record(conn, fp_row["block_id"], kind="block")
    proj_name = (proj.get("payload") or {}).get("name") or "Project"
    blk_name = (blk.get("payload") or {}).get("name") or "Block"
    p = fp_row["payload"]
    is_top = p.get("scope_kind") == "top" or blk_name.strip() == proj_name.strip()

    # Resolve block override -> project default Confluence parent
    conf_rows = service.db.records(conn, "fp_confluence_config", project_id=fp_row["project_id"], latest=True, limit=50)
    blk_conf = next((r["payload"] for r in conf_rows if str(r.get("block_id")) == str(fp_row["block_id"]) and (r["payload"].get("parent_page_id"))), None)
    proj_conf = next((r["payload"] for r in conf_rows if r.get("block_id") is None and (r["payload"].get("parent_page_id"))), None)
    effective_conf = blk_conf or proj_conf or {"space_key": "", "parent_page_id": "", "raw_url": ""}

    # Determine next local Rev.N (Create-Only never calls GET on Confluence)
    prior_pubs = service.all_records(conn, "fp_confluence_publish", parent_id=fp_row["id"])
    publish_rev = len(prior_pubs) + 1
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    suffix = f" · {opts.title_suffix.strip()}" if opts.title_suffix.strip() else ""
    title = f"[{proj_name}][{blk_name}] Floorplan Review - {p['fp_ver']}{suffix} (Rev.{publish_rev} · {now_kst})"

    rev_rows = service.db.records(conn, "fp_review", parent_id=fp_row["id"], latest=True, limit=1)
    rev_payload = rev_rows[0]["payload"] if rev_rows else {}
    pins_models = [ReviewPin.model_validate(pin) for pin in (rev_payload.get("review_pins") or [])]
    dp_models = [DatapathFlow.model_validate(dp) for dp in (rev_payload.get("datapaths") or [])]

    prop_summary = None
    prop_overrides = None
    if opts.proposal_id:
        prop_row = service.db.record(conn, opts.proposal_id, kind="fp_proposal")
        prop_summary = prop_row["payload"]
        prop_overrides = [MacroOverride.model_validate(ov) for ov in (prop_summary.get("overrides") or [])]

    attachments: list[ConfluenceAttachment] = []
    attachment_specs: list[tuple[str, str]] = []
    inline_svgs: list[tuple[str, str]] = []

    table_ready = _artifact_table_exists(conn, schema)
    if p.get("master_artifact_id") and (table_ready or is_temp_fallback_enabled()):  # [TEMP_ARTIFACT_FALLBACK]
        scene = _load_master_scene(conn, schema, fp_row)
        if opts.include_overview:
            png = render_scene_png(scene, mode="overview", review_pins=pins_models)
            attachments.append(ConfluenceAttachment("fp_01_overview.png", "image/png", png, "Floorplan Overview"))
            attachment_specs.append(("fp_01_overview.png", f"기본 Floorplan 배치 및 리뷰 핀 ({p['fp_ver']})"))
            inline_svgs.append((f"기본 Floorplan 배치 ({p['fp_ver']})", render_scene_svg(scene, mode="overview", review_pins=pins_models).decode("utf-8")))
        if opts.include_bist and scene.bist_summary:
            png = render_scene_png(scene, mode="bist")
            attachments.append(ConfluenceAttachment("fp_02_bist_groups.png", "image/png", png, "Memory BIST Groups"))
            attachment_specs.append(("fp_02_bist_groups.png", "Memory BIST Group 색상 분포"))
            inline_svgs.append(("Memory BIST Group 분포", render_scene_svg(scene, mode="bist").decode("utf-8")))
        if opts.include_datapath and dp_models:
            png = render_scene_png(scene, mode="overview", datapaths=dp_models)
            attachments.append(ConfluenceAttachment("fp_03_datapath.png", "image/png", png, "Data-path Flow"))
            attachment_specs.append(("fp_03_datapath.png", "Data-path 흐름 검토"))
            inline_svgs.append(("Data-path 흐름 검토", render_scene_svg(scene, mode="overview", datapaths=dp_models).decode("utf-8")))
        if prop_overrides and prop_summary:
            png = render_scene_png(scene, mode="bist" if scene.bist_summary else "overview", placement_overrides=prop_overrides)
            attachments.append(ConfluenceAttachment("fp_04_designer_proposal.png", "image/png", png, "Designer Proposal"))
            attachment_specs.append(("fp_04_designer_proposal.png", f"Designer 제안 배치안: {prop_summary.get('title')}"))
            inline_svgs.append((f"Designer 제안 배치안: {prop_summary.get('title')}", render_scene_svg(scene, mode="overview", placement_overrides=prop_overrides).decode("utf-8")))

    if opts.extra_artifact_ids and (table_ready or is_temp_fallback_enabled()):  # [TEMP_ARTIFACT_FALLBACK]
        tbl = sql.Identifier(schema, "di_design_artifact")
        for idx, aid in enumerate(opts.extra_artifact_ids, start=1):
            arow = (
                conn.execute(
                    sql.SQL("SELECT title, view_name, mime_type, artifact_bytes FROM {} WHERE id=%s AND state='active'").format(tbl),
                    (aid,),
                ).fetchone()
                if table_ready
                else None
            )
            if not arow:
                # [TEMP_ARTIFACT_FALLBACK]
                t_extra = temp_get_artifact(conn, schema, aid, include_bytes=True)
                if t_extra and t_extra.get("state") == "active":
                    arow = t_extra
            if arow:
                ext = ".png" if arow["mime_type"] == "image/png" else (".webp" if arow["mime_type"] == "image/webp" else ".jpg")
                fname = f"fp_extra_{idx:02d}{ext}"
                cap = arow["title"] or arow["view_name"] or fname
                attachments.append(ConfluenceAttachment(fname, arow["mime_type"], bytes(arow["artifact_bytes"]), cap))
                attachment_specs.append((fname, f"보조 뷰: {cap}"))

    # Metrics from linked Area CSV
    upload_row = None
    if p.get("linked_area_upload_id"):
        try:
            upload_row = service.db.upload(conn, UUID(str(p["linked_area_upload_id"])))
        except Exception:
            upload_row = None
    metrics_full = _extract_upload_metrics(service, conn, upload_row)
    root_metrics = metrics_full["root"] if metrics_full else None

    dashboard_url = getattr(getattr(service.db, "settings", None), "dashboard_url", None)
    dash_link = f"{dashboard_url.rstrip('/')}/?page=floorplan" if dashboard_url else None

    xhtml = build_confluence_storage_xhtml(
        project_name=proj_name,
        block_name=blk_name,
        is_top=is_top,
        fp_ver=p["fp_ver"],
        prev_fp_ver=p.get("prev_fp_ver"),
        linked_net_ver=p.get("linked_net_ver"),
        overall_verdict=rev_payload.get("overall_verdict") or "PENDING",
        summary_note=rev_payload.get("summary_note") or "",
        reviewer=reviewer,
        published_at_display=now_kst,
        publish_rev=publish_rev,
        dashboard_link=dash_link,
        metrics=root_metrics,
        checklist_rows=rev_payload.get("checklist") or [],
        review_pins=rev_payload.get("review_pins") or [],
        bist_summary=p.get("bist_summary") or [],
        proposal_summary=prop_summary,
        attachment_specs=attachment_specs,
    )
    standalone_html = build_standalone_review_html(title=title, body_xhtml=xhtml, inline_svgs=inline_svgs)
    return {
        "title": title,
        "publish_rev": publish_rev,
        "space_key": effective_conf.get("space_key") or "",
        "parent_page_id": effective_conf.get("parent_page_id") or "",
        "storage_xhtml": xhtml,
        "standalone_html": standalone_html,
        "attachments": attachments,
        "total_attachment_bytes": sum(len(a.content_bytes) for a in attachments),
    }


def preview_confluence_publish(service: Any, fp_version_id: UUID, opts: ConfluencePublishOptionsInput, reviewer: str) -> dict[str, Any]:
    with service.db.connect() as conn:
        fp_row = service.db.record(conn, fp_version_id, kind="fp_version")
        proj = service.db.record(conn, fp_row["project_id"], kind="project")
        service.authorize(conn, proj, write=False)
        pkg = _build_confluence_package(service, conn, fp_row, opts, reviewer)
        return {
            "title": pkg["title"],
            "publish_rev": pkg["publish_rev"],
            "space_key": pkg["space_key"],
            "parent_page_id": pkg["parent_page_id"],
            "attachment_count": len(pkg["attachments"]),
            "total_attachment_bytes": pkg["total_attachment_bytes"],
            "attachments": [{"filename": a.filename, "byte_length": len(a.content_bytes), "comment": a.comment} for a in pkg["attachments"]],
            "storage_xhtml": pkg["storage_xhtml"],
            "standalone_html": pkg["standalone_html"],
        }


def execute_confluence_publish(
    service: Any,
    fp_version_id: UUID,
    opts: ConfluencePublishOptionsInput,
    *,
    personal_pat: str,
    actor_user_id: str,
    reviewer: str,
) -> dict[str, Any]:
    with service.db.connect() as conn:
        fp_row = service.db.record(conn, fp_version_id, kind="fp_version")
        proj = service.db.record(conn, fp_row["project_id"], kind="project")
        service.authorize(conn, proj, write=True)
        pkg = _build_confluence_package(service, conn, fp_row, opts, reviewer)

    if not pkg["space_key"] or not pkg["parent_page_id"]:
        raise DomainError(
            "Configure the project or block Confluence Space Key and Parent Page ID before publishing.",
            code="confluence_target_missing",
            status_code=409,
        )

    publisher = create_confluence_publisher()
    req = ConfluenceCreateRequest(
        space_key=pkg["space_key"],
        parent_page_id=pkg["parent_page_id"],
        title=pkg["title"],
        storage_xhtml=pkg["storage_xhtml"],
        attachments=tuple(pkg["attachments"]),
        personal_pat=personal_pat,
        actor_user_id=actor_user_id,
    )
    receipt = publisher.create_review_page(req)

    with service.db.connect() as conn:
        saved = service.db.insert_record(
            conn,
            "fp_confluence_publish",
            {
                "publish_rev": pkg["publish_rev"],
                "title": pkg["title"],
                "space_key": pkg["space_key"],
                "parent_page_id": pkg["parent_page_id"],
                "page_id": receipt.page_id,
                "page_url": receipt.page_url,
                "attachment_count": len(receipt.uploaded_attachments),
                "total_attachment_bytes": pkg["total_attachment_bytes"],
            },
            actor_user_id,
            parent_id=fp_version_id,
            project_id=fp_row["project_id"],
            block_id=fp_row["block_id"],
            state="published",
        )
    return {"id": str(saved["id"]), **saved["payload"]}
