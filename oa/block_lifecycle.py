"""Block-level safe deletion (Option B) and Kill Switch cascade deletion without DB DDL changes."""
from __future__ import annotations

from typing import Any

from psycopg import sql
from psycopg.errors import InsufficientPrivilege

from common.area_power.atlas_bindings import BINDING_PREFIX
from common.sanity_common import ReviewError
from common.sanity_db import lock_key
from oa.project_lifecycle import (
    REVIEW_RECORD_TYPES,
    _area_delete_available,
    force_delete_disabled_by_env,
    oa_delete_available,
)
from oa.sanity_workspace_core import WorkspaceCoreStore


def _find_area_block_id(area_db, aconn, block_id: str):
    row = aconn.execute(
        sql.SQL("SELECT id FROM {} WHERE record_type='block' AND payload->>'sanity_block_id'=%s LIMIT 1").format(
            area_db.table("di_area_power_tracking_record")
        ),
        (block_id,),
    ).fetchone()
    return row["id"] if row else None


def _area_block_counts(block_id: str) -> dict[str, Any]:
    try:
        from common.area_power.db import Database as AreaDatabase
        from common.area_power.errors import ConfigurationError, DomainError
    except Exception:
        return {"linked": False, "area_block_id": None, "uploads": 0, "records": 0, "external_refs": 0, "delete_available": True}
    try:
        area_db = AreaDatabase()
        with area_db.connect() as aconn:
            area_bid = _find_area_block_id(area_db, aconn, block_id)
            if area_bid is None:
                return {"linked": False, "area_block_id": None, "uploads": 0, "records": 0, "external_refs": 0, "delete_available": True}
            csv_tbl = area_db.table("di_area_power_csv")
            rec_tbl = area_db.table("di_area_power_tracking_record")
            uploads = aconn.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE block_id=%s").format(csv_tbl), (area_bid,)).fetchone()["n"]
            records = aconn.execute(
                sql.SQL("SELECT count(*) AS n FROM {} WHERE block_id=%s AND id <> %s").format(rec_tbl),
                (area_bid, area_bid),
            ).fetchone()["n"]
            ext_refs = aconn.execute(
                sql.SQL(
                    "SELECT count(*) AS n FROM {} WHERE block_id IS DISTINCT FROM %s "
                    "AND baseline_upload_id IN (SELECT id FROM {} WHERE block_id=%s)"
                ).format(rec_tbl, csv_tbl),
                (area_bid, area_bid),
            ).fetchone()["n"]
            can_del = _area_delete_available(area_db, aconn)
            return {
                "linked": True,
                "area_block_id": str(area_bid),
                "uploads": int(uploads),
                "records": int(records),
                "external_refs": int(ext_refs),
                "delete_available": can_del,
            }
    except (ConfigurationError, DomainError):
        return {"linked": False, "area_block_id": None, "uploads": 0, "records": 0, "external_refs": 0, "delete_available": True}


def _purge_area_block(block_id: str) -> dict[str, int]:
    try:
        from common.area_power.db import Database as AreaDatabase
        from common.area_power.errors import ConfigurationError, DomainError
    except Exception:
        return {"uploads": 0, "records": 0}
    try:
        area_db = AreaDatabase()
        with area_db.connect() as aconn:
            area_bid = _find_area_block_id(area_db, aconn, block_id)
            if area_bid is None:
                return {"uploads": 0, "records": 0}
            if not _area_delete_available(area_db, aconn):
                raise ReviewError("DATA_DELETE_NOT_CONFIGURED", "Area/Power table DELETE permission is unavailable")
            csv_tbl = area_db.table("di_area_power_csv")
            rec_tbl = area_db.table("di_area_power_tracking_record")

            upload_ids = [
                r["id"]
                for r in aconn.execute(sql.SQL("SELECT id FROM {} WHERE block_id=%s").format(csv_tbl), (area_bid,)).fetchall()
            ]
            upload_strs = [str(x) for x in upload_ids]
            target_ids = {
                r["id"]
                for r in aconn.execute(
                    sql.SQL(
                        "SELECT id FROM {} WHERE block_id=%s OR id=%s "
                        "OR upload_id=ANY(%s) OR baseline_upload_id=ANY(%s) "
                        "OR (record_type='comparison' AND ("
                        "payload->'request'->>'current_upload_id'=ANY(%s) "
                        "OR payload->'request'->>'baseline_upload_id'=ANY(%s)))"
                    ).format(rec_tbl),
                    (area_bid, area_bid, upload_ids, upload_ids, upload_strs, upload_strs),
                ).fetchall()
            }
            frontier = list(target_ids)
            while frontier:
                children = aconn.execute(
                    sql.SQL("SELECT id FROM {} WHERE parent_id=ANY(%s) OR previous_id=ANY(%s)").format(rec_tbl),
                    (frontier, frontier),
                ).fetchall()
                new_ids = [r["id"] for r in children if r["id"] not in target_ids]
                target_ids.update(new_ids)
                frontier = new_ids

            deleted_records = 0
            remaining = set(target_ids)
            while remaining:
                rem_list = list(remaining)
                leaf_rows = aconn.execute(
                    sql.SQL(
                        "SELECT id FROM {} WHERE id=ANY(%s) "
                        "AND id NOT IN (SELECT parent_id FROM {} WHERE parent_id IS NOT NULL AND id=ANY(%s)) "
                        "AND id NOT IN (SELECT previous_id FROM {} WHERE previous_id IS NOT NULL AND id=ANY(%s))"
                    ).format(rec_tbl, rec_tbl, rec_tbl),
                    (rem_list, rem_list, rem_list),
                ).fetchall()
                leaves = [r["id"] for r in leaf_rows] or rem_list
                aconn.execute(sql.SQL("DELETE FROM {} WHERE id=ANY(%s)").format(rec_tbl), (leaves,))
                deleted_records += len(leaves)
                remaining.difference_update(leaves)

            deleted_uploads = 0
            if upload_ids:
                aconn.execute(sql.SQL("DELETE FROM {} WHERE id=ANY(%s)").format(csv_tbl), (upload_ids,))
                deleted_uploads = len(upload_ids)
            return {"uploads": deleted_uploads, "records": deleted_records}
    except InsufficientPrivilege as exc:
        raise ReviewError("DATA_DELETE_NOT_CONFIGURED", "Area/Power table DELETE permission is unavailable") from exc
    except (ConfigurationError, DomainError):
        return {"uploads": 0, "records": 0}


def _resolve_block(db, project_id: str, block_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    ws = WorkspaceCoreStore(db)
    project_row = ws.get("WS_PROJECT", project_id)
    block_row = ws.get("WS_BLOCK", block_id)
    if block_row["workspace_project_ref"] != project_row["record_id"]:
        raise ReviewError("NOT_FOUND", "Block does not belong to the selected project")
    return project_row, block_row


def block_delete_preview(db, project_id: str, block_id: str, *, area_enabled: bool = False) -> dict[str, Any]:
    _, block_row = _resolve_block(db, project_id, block_id)
    bref = block_row["record_id"]
    block_name = (block_row["payload"] or {}).get("name") or block_id

    counts_rows = db.execute(
        "SELECT record_type, count(*) AS n FROM {oa} WHERE workspace_block_ref=%s GROUP BY record_type",
        (bref,),
    ).fetchall()
    by_type = {r["record_type"]: int(r["n"]) for r in counts_rows}
    report_refs = [
        r["record_id"]
        for r in db.execute(
            "SELECT record_id FROM {oa} WHERE record_type='WS_REPORT' AND workspace_block_ref=%s", (bref,)
        ).fetchall()
    ]
    reviews = 0
    if report_refs:
        reviews = int(db.execute(
            "SELECT count(*) AS n FROM {oa} WHERE record_type=ANY(%s) "
            "AND (workspace_review_report_ref=ANY(%s) OR workspace_pipeline_report_ref=ANY(%s))",
            (list(REVIEW_RECORD_TYPES), report_refs, report_refs),
        ).fetchone()["n"])

    versions = by_type.get("WS_VERSION", 0)
    reports = by_type.get("WS_REPORT", 0)
    submissions = by_type.get("WS_SUBMISSION", 0)
    area = _area_block_counts(block_id) if area_enabled else {
        "linked": False, "area_block_id": None, "uploads": 0, "records": 0, "external_refs": 0, "delete_available": True,
    }

    is_empty = (versions + reports + submissions + reviews + area["uploads"] + area["records"]) == 0
    oa_del = oa_delete_available(db)
    delete_available = oa_del and (area["delete_available"] if area["linked"] else True)
    kill_switch_allowed = not force_delete_disabled_by_env()

    blockers: list[str] = []
    if not delete_available:
        blockers.append("DATA_DELETE_NOT_CONFIGURED")
    if not is_empty:
        blockers.append("BLOCK_HAS_DATA")
    if not kill_switch_allowed:
        blockers.append("FORCE_DELETE_DISABLED")

    return {
        "project_id": project_id,
        "block_id": block_id,
        "name": block_name,
        "counts": {
            "versions": versions,
            "reports": reports,
            "submissions": submissions,
            "reviews": reviews,
            "area_uploads": area["uploads"],
            "area_records": area["records"],
            "external_area_refs": area["external_refs"],
        },
        "is_empty": is_empty,
        "delete_available": delete_available,
        "kill_switch_allowed": kill_switch_allowed,
        "can_delete_empty": is_empty and delete_available,
        "can_force_delete": delete_available and kill_switch_allowed,
        "blockers": blockers,
    }


def delete_block(
    db,
    actor,
    project_id: str,
    block_id: str,
    *,
    force: bool = False,
    confirm_name: str = "",
    reason: str = "",
    access_store=None,
    area_enabled: bool = False,
) -> dict[str, Any]:
    reason = reason.strip() if isinstance(reason, str) else ""
    confirm_name = confirm_name.strip() if isinstance(confirm_name, str) else ""
    if len(reason) > 1000:
        raise ReviewError("INVALID_REQUEST", "Reason is too long")

    db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-catalog"),))
    preview = block_delete_preview(db, project_id, block_id, area_enabled=area_enabled)
    if confirm_name != preview["name"]:
        raise ReviewError("CONFIRMATION_MISMATCH", "입력한 블록 이름이 일치하지 않습니다.")
    if not preview["delete_available"]:
        raise ReviewError("DATA_DELETE_NOT_CONFIGURED", "DB 계정에 DELETE 권한이 없어 블록을 완전 삭제할 수 없습니다.")
    if not force and not preview["is_empty"]:
        raise ReviewError(
            "BLOCK_HAS_DATA",
            "하위 데이터가 있는 블록은 일반 삭제할 수 없습니다. 강제 삭제(Kill Switch)를 활성화해 주세요.",
        )
    if force:
        if not preview["kill_switch_allowed"]:
            raise ReviewError("FORCE_DELETE_DISABLED", "서버 설정(ATLAS_DISABLE_FORCE_PROJECT_DELETE)으로 강제 삭제가 비활성화되어 있습니다.")
        if not reason:
            raise ReviewError("INVALID_REQUEST", "강제 삭제 사유를 입력해 주세요.")

    _, block_row = _resolve_block(db, project_id, block_id)
    bref = block_row["record_id"]

    version_rows = db.execute("SELECT record_id, workspace_key FROM {oa} WHERE record_type='WS_VERSION' AND workspace_block_ref=%s", (bref,)).fetchall()
    report_rows = db.execute("SELECT record_id, workspace_key FROM {oa} WHERE record_type='WS_REPORT' AND workspace_block_ref=%s", (bref,)).fetchall()
    version_refs = [r["record_id"] for r in version_rows]
    version_ids = [r["workspace_key"] for r in version_rows]
    report_refs = [r["record_id"] for r in report_rows]
    report_ids = [r["workspace_key"] for r in report_rows]

    area_deleted = _purge_area_block(block_id) if area_enabled else {"uploads": 0, "records": 0}

    # Append BLOCK_DELETE event before deleting WS_BLOCK so _validate_registry() passes cleanly.
    if access_store is not None:
        access_store.record_block_deleted(actor, project_id, block_id, force=force, reason=reason)

    try:
        if report_ids:
            db.execute(
                "DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND ("
                "(payload->>'namespace'='ai-evidence-choice-v1' AND payload->>'report_id'=ANY(%s)) OR "
                "(payload->>'namespace' IN ('oa-ai-v1','oa-ai-submission-v1') AND payload->'data'->'command'->>'report_id'=ANY(%s)))",
                (report_ids, report_ids),
            )
            db.execute(
                "DELETE FROM {oa} WHERE record_type=ANY(%s) "
                "AND (workspace_review_report_ref=ANY(%s) OR workspace_pipeline_report_ref=ANY(%s))",
                (list(REVIEW_RECORD_TYPES), report_refs, report_refs),
            )
        db.execute(
            "DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s AND payload->>'block_id'=%s",
            (BINDING_PREFIX + "%", block_id),
        )
        if version_ids:
            db.execute(
                "DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=ANY(%s)",
                (["atlas.version." + vid for vid in version_ids],),
            )
        db.execute(
            "DELETE FROM {oa} WHERE record_id=%s OR workspace_block_ref=%s OR workspace_version_ref=ANY(%s)",
            (bref, bref, version_refs),
        )
    except InsufficientPrivilege as exc:
        raise ReviewError("DATA_DELETE_NOT_CONFIGURED", "DB 계정에 DELETE 권한이 없어 블록을 완전 삭제할 수 없습니다.") from exc

    return {
        "status": "DELETED",
        "project_id": project_id,
        "block_id": block_id,
        "name": preview["name"],
        "force": force,
        "deleted_counts": {
            **preview["counts"],
            "area_uploads": area_deleted["uploads"],
            "area_records": area_deleted["records"],
        },
    }
