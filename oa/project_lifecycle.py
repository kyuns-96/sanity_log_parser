"""Project edit, hide/unhide, and safe/force (Kill Switch) deletion without DB DDL changes."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any
from uuid import uuid4

from psycopg import sql
from psycopg.errors import InsufficientPrivilege

from common.area_power.atlas_bindings import BINDING_PREFIX
from common.sanity_common import ReviewError
from common.sanity_db import lock_key
from oa.sanity_workspace_core import WorkspaceCoreStore, record_id

PMETA_PREFIX = "atlas.pmeta."
PHIDE_PREFIX = "atlas.phide."
PSTAT_PREFIXES = ("atlas.project-status.", "atlas.pstat.")
REVIEW_RECORD_TYPES = (
    "WS_REVIEW", "WS_REVIEW_EVENT", "WS_REVIEW_PROPOSAL",
    "WS_CLUSTER", "WS_CLUSTER_EVENT",
    "WS_GROUP_REVIEW", "WS_GROUP_REVIEW_EVENT", "WS_GROUP_REVIEW_PROPOSAL",
)


def force_delete_disabled_by_env() -> bool:
    raw = os.environ.get("ATLAS_DISABLE_FORCE_PROJECT_DELETE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def oa_delete_available(db) -> bool:
    row = db.execute(
        "SELECT has_table_privilege(current_user, quote_ident(%s)||'.sanity_oa_data', 'DELETE') AS allowed",
        (db.schema,),
    ).fetchone()
    return bool(row and row["allowed"])


def _area_delete_available(area_db, aconn) -> bool:
    row = aconn.execute(
        "SELECT has_table_privilege(current_user, quote_ident(%s)||'.di_area_power_tracking_record', 'DELETE') "
        "AND has_table_privilege(current_user, quote_ident(%s)||'.di_area_power_csv', 'DELETE') AS allowed",
        (area_db.settings.db_schema, area_db.settings.db_schema),
    ).fetchone()
    return bool(row and row["allowed"])


def project_meta_overrides(db) -> dict[str, dict[str, Any]]:
    rows = db.execute(
        "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
        "ORDER BY COALESCE((payload->>'revision')::bigint, 0) ASC, created_at ASC, record_id ASC",
        (PMETA_PREFIX + "%",),
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        p = row["payload"] or {}
        pid = p.get("project_id")
        if pid:
            result[pid] = p
    return result


def project_hidden_overrides(db) -> dict[str, dict[str, Any]]:
    rows = db.execute(
        "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
        "ORDER BY COALESCE((payload->>'revision')::bigint, 0) ASC, created_at ASC, record_id ASC",
        (PHIDE_PREFIX + "%",),
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        p = row["payload"] or {}
        pid = p.get("project_id")
        if pid and isinstance(p.get("hidden"), bool):
            result[pid] = p
    return result


def effective_project_info(db, project_row: dict[str, Any]) -> dict[str, Any]:
    pid = project_row["workspace_key"]
    base = project_row["payload"] or {}
    meta = project_meta_overrides(db).get(pid)
    hide = project_hidden_overrides(db).get(pid)
    ws = WorkspaceCoreStore(db)
    process = meta.get("process") if meta and "process" in meta else ws.initial_process(pid)
    return {
        "id": pid,
        "name": meta["name"] if meta and meta.get("name") else base.get("name", pid),
        "description": meta["description"] if meta and "description" in meta else base.get("description", ""),
        "process": process,
        "hidden": bool(hide["hidden"]) if hide else False,
        "hidden_reason": (hide or {}).get("reason") or "",
    }


def apply_project_overlay(db, projects: list[dict[str, Any]], *, include_hidden: bool = False) -> list[dict[str, Any]]:
    meta_map = project_meta_overrides(db)
    hide_map = project_hidden_overrides(db)
    out: list[dict[str, Any]] = []
    for item in projects:
        pid = item["id"]
        meta = meta_map.get(pid)
        hide = hide_map.get(pid)
        is_hidden = bool(hide["hidden"]) if hide else False
        if is_hidden and not include_hidden:
            continue
        merged = dict(item)
        if meta:
            if meta.get("name"):
                merged["name"] = meta["name"]
            if "description" in meta:
                merged["description"] = meta["description"]
            if "process" in meta:
                merged["process"] = meta["process"]
        merged["hidden"] = is_hidden
        if is_hidden and hide and hide.get("reason"):
            merged["hidden_reason"] = hide["reason"]
        out.append(merged)
    return out


def _sync_area_project_metadata(project_id: str, name: str, process: str | None, actor_id: str) -> bool:
    try:
        from common.area_power.db import Database as AreaDatabase
        from common.area_power.errors import ConfigurationError, DomainError
    except Exception:
        return False
    try:
        area_db = AreaDatabase()
        with area_db.connect() as aconn:
            row = aconn.execute(
                sql.SQL("SELECT * FROM {} WHERE record_type='project' AND payload->>'sanity_project_id'=%s LIMIT 1 FOR UPDATE").format(
                    area_db.table("di_area_power_tracking_record")
                ),
                (project_id,),
            ).fetchone()
            if row is None:
                return False
            payload = {**(row["payload"] or {}), "name": name}
            if process is not None:
                payload["process"] = process
            elif "process" in payload:
                payload["process"] = None
            area_db.update_record(aconn, row["id"], actor_id, expected_row_version=row["row_version"], payload=payload)
            return True
    except (ConfigurationError, DomainError):
        return False


def update_project(
    db,
    actor_id: str,
    project_id: str,
    *,
    name: str,
    description: str = "",
    process: str | None = None,
    area_enabled: bool = False,
) -> dict[str, Any]:
    name = name.strip() if isinstance(name, str) else ""
    description = description.strip() if isinstance(description, str) else ""
    if not name or len(name) > 100 or any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ReviewError("INVALID_REQUEST", "Invalid project name")
    if len(description) > 1000 or any(ord(ch) < 32 or ord(ch) == 127 for ch in description):
        raise ReviewError("INVALID_REQUEST", "Invalid project description")

    ws = WorkspaceCoreStore(db)
    db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-catalog"),))
    row = ws.get("WS_PROJECT", project_id)
    if process is not None:
        ws.require_process(process)

    meta_map = project_meta_overrides(db)
    for other in ws.rows("WS_PROJECT"):
        other_id = other["workspace_key"]
        if other_id == project_id:
            continue
        other_meta = meta_map.get(other_id)
        other_name = (other_meta or {}).get("name") or (other["payload"] or {}).get("name")
        if other_name == name:
            raise ReviewError("REQUEST_CONFLICT", "Another project already uses this name")

    existing_revs = db.execute(
        "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s AND payload->>'project_id'=%s",
        (PMETA_PREFIX + "%", project_id),
    ).fetchall()
    next_rev = max((int((r["payload"] or {}).get("revision") or 0) for r in existing_revs), default=0) + 1
    ws.put("WS_CONFIG", PMETA_PREFIX + uuid4().hex, {
        "project_id": project_id,
        "name": name,
        "description": description,
        "process": process,
        "revision": next_rev,
        "updated_by": actor_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    area_synced = _sync_area_project_metadata(project_id, name, process, actor_id) if area_enabled else False
    return {
        "status": "UPDATED",
        "project_id": project_id,
        "name": name,
        "description": description,
        "process": process,
        "revision": next_rev,
        "area_synced": area_synced,
    }


def set_project_hidden(
    db,
    actor_id: str,
    project_id: str,
    *,
    hidden: bool,
    reason: str = "",
) -> dict[str, Any]:
    reason = reason.strip() if isinstance(reason, str) else ""
    if len(reason) > 500:
        raise ReviewError("INVALID_REQUEST", "Reason is too long")
    ws = WorkspaceCoreStore(db)
    db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-catalog"),))
    ws.get("WS_PROJECT", project_id)
    existing_revs = db.execute(
        "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s AND payload->>'project_id'=%s",
        (PHIDE_PREFIX + "%", project_id),
    ).fetchall()
    next_rev = max((int((r["payload"] or {}).get("revision") or 0) for r in existing_revs), default=0) + 1
    ws.put("WS_CONFIG", PHIDE_PREFIX + uuid4().hex, {
        "project_id": project_id,
        "hidden": bool(hidden),
        "reason": reason,
        "revision": next_rev,
        "updated_by": actor_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "status": "HIDDEN" if hidden else "RESTORED",
        "project_id": project_id,
        "hidden": bool(hidden),
        "reason": reason,
        "revision": next_rev,
    }


def _find_area_project_id(area_db, aconn, project_id: str):
    row = aconn.execute(
        sql.SQL("SELECT id FROM {} WHERE record_type='project' AND payload->>'sanity_project_id'=%s LIMIT 1").format(
            area_db.table("di_area_power_tracking_record")
        ),
        (project_id,),
    ).fetchone()
    return row["id"] if row else None


def _area_counts(project_id: str) -> dict[str, Any]:
    try:
        from common.area_power.db import Database as AreaDatabase
        from common.area_power.errors import ConfigurationError, DomainError
    except Exception:
        return {"linked": False, "area_project_id": None, "uploads": 0, "records": 0, "external_refs": 0, "delete_available": True}
    try:
        area_db = AreaDatabase()
        with area_db.connect() as aconn:
            area_pid = _find_area_project_id(area_db, aconn, project_id)
            if area_pid is None:
                return {"linked": False, "area_project_id": None, "uploads": 0, "records": 0, "external_refs": 0, "delete_available": True}
            csv_tbl = area_db.table("di_area_power_csv")
            rec_tbl = area_db.table("di_area_power_tracking_record")
            uploads = aconn.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE project_id=%s").format(csv_tbl), (area_pid,)).fetchone()["n"]
            records = aconn.execute(
                sql.SQL("SELECT count(*) AS n FROM {} WHERE project_id=%s AND id <> %s").format(rec_tbl),
                (area_pid, area_pid),
            ).fetchone()["n"]
            ext_upload_refs = aconn.execute(
                sql.SQL(
                    "SELECT count(*) AS n FROM {} WHERE project_id IS DISTINCT FROM %s "
                    "AND (baseline_upload_id IN (SELECT id FROM {} WHERE project_id=%s) "
                    "OR payload->>'baseline_project_id'=%s)"
                ).format(rec_tbl, csv_tbl),
                (area_pid, area_pid, str(area_pid)),
            ).fetchone()["n"]
            can_del = _area_delete_available(area_db, aconn)
            return {
                "linked": True,
                "area_project_id": str(area_pid),
                "uploads": int(uploads),
                "records": int(records),
                "external_refs": int(ext_upload_refs),
                "delete_available": can_del,
            }
    except (ConfigurationError, DomainError):
        return {"linked": False, "area_project_id": None, "uploads": 0, "records": 0, "external_refs": 0, "delete_available": True}


def project_delete_preview(db, project_id: str, *, area_enabled: bool = False) -> dict[str, Any]:
    ws = WorkspaceCoreStore(db)
    project_row = ws.get("WS_PROJECT", project_id)
    info = effective_project_info(db, project_row)
    pref = project_row["record_id"]

    counts_rows = db.execute(
        "SELECT record_type, count(*) AS n FROM {oa} WHERE workspace_project_ref=%s GROUP BY record_type",
        (pref,),
    ).fetchall()
    by_type = {r["record_type"]: int(r["n"]) for r in counts_rows}
    report_refs = [
        r["record_id"]
        for r in db.execute(
            "SELECT record_id FROM {oa} WHERE record_type='WS_REPORT' AND workspace_project_ref=%s", (pref,)
        ).fetchall()
    ]
    reviews = 0
    if report_refs:
        reviews = int(db.execute(
            "SELECT count(*) AS n FROM {oa} WHERE record_type=ANY(%s) "
            "AND (workspace_review_report_ref=ANY(%s) OR workspace_pipeline_report_ref=ANY(%s))",
            (list(REVIEW_RECORD_TYPES), report_refs, report_refs),
        ).fetchone()["n"])

    blocks = by_type.get("WS_BLOCK", 0)
    versions = by_type.get("WS_VERSION", 0)
    reports = by_type.get("WS_REPORT", 0)
    submissions = by_type.get("WS_SUBMISSION", 0)
    area = _area_counts(project_id) if area_enabled else {"linked": False, "area_project_id": None, "uploads": 0, "records": 0, "external_refs": 0, "delete_available": True}

    is_empty = (blocks + versions + reports + submissions + reviews + area["uploads"] + area["records"]) == 0
    oa_del = oa_delete_available(db)
    delete_available = oa_del and (area["delete_available"] if area["linked"] else True)
    kill_switch_allowed = not force_delete_disabled_by_env()

    blockers: list[str] = []
    if not delete_available:
        blockers.append("DATA_DELETE_NOT_CONFIGURED")
    if not is_empty:
        blockers.append("PROJECT_HAS_DATA")
    if not kill_switch_allowed:
        blockers.append("FORCE_DELETE_DISABLED")

    return {
        "project_id": project_id,
        "name": info["name"],
        "hidden": info["hidden"],
        "counts": {
            "blocks": blocks,
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


def _purge_area_project(project_id: str, actor_id: str) -> dict[str, int]:
    try:
        from common.area_power.db import Database as AreaDatabase
        from common.area_power.errors import ConfigurationError, DomainError
    except Exception:
        return {"uploads": 0, "records": 0}
    try:
        area_db = AreaDatabase()
        with area_db.connect() as aconn:
            area_pid = _find_area_project_id(area_db, aconn, project_id)
            if area_pid is None:
                return {"uploads": 0, "records": 0}
            if not _area_delete_available(area_db, aconn):
                raise ReviewError("DATA_DELETE_NOT_CONFIGURED", "Area/Power table DELETE permission is unavailable")
            csv_tbl = area_db.table("di_area_power_csv")
            rec_tbl = area_db.table("di_area_power_tracking_record")

            # Clear baseline_project_id references on other projects cleanly.
            other_projs = aconn.execute(
                sql.SQL("SELECT id, row_version, payload FROM {} WHERE record_type='project' AND id <> %s AND payload->>'baseline_project_id'=%s FOR UPDATE").format(rec_tbl),
                (area_pid, str(area_pid)),
            ).fetchall()
            for proj in other_projs:
                new_payload = {**(proj["payload"] or {}), "baseline_project_id": None}
                area_db.update_record(aconn, proj["id"], actor_id, expected_row_version=proj["row_version"], payload=new_payload)

            upload_ids = [
                r["id"]
                for r in aconn.execute(sql.SQL("SELECT id FROM {} WHERE project_id=%s").format(csv_tbl), (area_pid,)).fetchall()
            ]
            target_ids = {
                r["id"]
                for r in aconn.execute(
                    sql.SQL(
                        "SELECT id FROM {} WHERE project_id=%s OR id=%s OR upload_id=ANY(%s) OR baseline_upload_id=ANY(%s)"
                    ).format(rec_tbl),
                    (area_pid, area_pid, upload_ids, upload_ids),
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
            # Delete leaves first so non-deferrable self-referencing FKs never fail.
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


def delete_project(
    db,
    actor,
    project_id: str,
    *,
    force: bool = False,
    confirm_name: str = "",
    reason: str = "",
    access_store=None,
    area_enabled: bool = False,
) -> dict[str, Any]:
    actor_id = actor.user_id if hasattr(actor, "user_id") else str(actor)
    reason = reason.strip() if isinstance(reason, str) else ""
    confirm_name = confirm_name.strip() if isinstance(confirm_name, str) else ""
    if len(reason) > 1000:
        raise ReviewError("INVALID_REQUEST", "Reason is too long")

    db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-catalog"),))
    preview = project_delete_preview(db, project_id, area_enabled=area_enabled)
    if confirm_name != preview["name"]:
        raise ReviewError("CONFIRMATION_MISMATCH", "입력한 프로젝트 이름이 일치하지 않습니다.")
    if not preview["delete_available"]:
        raise ReviewError("DATA_DELETE_NOT_CONFIGURED", "DB 계정에 DELETE 권한이 없어 완전 삭제할 수 없습니다. 숨김 기능을 사용해 주세요.")
    if not force and not preview["is_empty"]:
        raise ReviewError(
            "PROJECT_HAS_DATA",
            "하위 데이터가 있는 프로젝트는 일반 삭제할 수 없습니다. 숨김 처리하거나 강제 삭제(Kill Switch)를 활성화해 주세요.",
        )
    if force:
        if not preview["kill_switch_allowed"]:
            raise ReviewError("FORCE_DELETE_DISABLED", "서버 설정(ATLAS_DISABLE_FORCE_PROJECT_DELETE)으로 강제 삭제가 비활성화되어 있습니다.")
        if not reason:
            raise ReviewError("INVALID_REQUEST", "강제 삭제 사유를 입력해 주세요.")

    ws = WorkspaceCoreStore(db)
    project_row = ws.get("WS_PROJECT", project_id)
    pref = project_row["record_id"]

    block_rows = db.execute("SELECT record_id, workspace_key FROM {oa} WHERE record_type='WS_BLOCK' AND workspace_project_ref=%s", (pref,)).fetchall()
    version_rows = db.execute("SELECT record_id, workspace_key FROM {oa} WHERE record_type='WS_VERSION' AND workspace_project_ref=%s", (pref,)).fetchall()
    report_rows = db.execute("SELECT record_id, workspace_key FROM {oa} WHERE record_type='WS_REPORT' AND workspace_project_ref=%s", (pref,)).fetchall()
    block_refs = [r["record_id"] for r in block_rows]
    version_refs = [r["record_id"] for r in version_rows]
    version_ids = [r["workspace_key"] for r in version_rows]
    report_refs = [r["record_id"] for r in report_rows]
    report_ids = [r["workspace_key"] for r in report_rows]

    area_deleted = _purge_area_project(project_id, actor_id) if area_enabled else {"uploads": 0, "records": 0}

    # Append PROJECT_DELETE event while WS_PROJECT is still present so _validate_registry() passes,
    # then physically delete WS_PROJECT and children in the same transaction.
    if access_store is not None:
        access_store.record_project_deleted(actor, project_id, force=force, reason=reason)

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
        # Clean project-scoped WS_CONFIG records (never touch access.event.* audit log).
        db.execute(
            "DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND ("
            "workspace_key=%s "
            "OR (workspace_key LIKE %s AND payload->>'project_id'=%s) "
            "OR (workspace_key LIKE %s AND payload->>'project_id'=%s) "
            "OR (workspace_key LIKE %s AND payload->>'project_id'=%s) "
            "OR (workspace_key LIKE %s AND payload->>'project_id'=%s) "
            "OR (workspace_key LIKE %s AND payload->>'project_id'=%s))",
            (
                "atlas.project-process." + str(pref),
                PMETA_PREFIX + "%", project_id,
                PHIDE_PREFIX + "%", project_id,
                PSTAT_PREFIXES[0] + "%", project_id,
                PSTAT_PREFIXES[1] + "%", project_id,
                BINDING_PREFIX + "%", project_id,
            ),
        )
        if version_ids:
            db.execute(
                "DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=ANY(%s)",
                (["atlas.version." + vid for vid in version_ids],),
            )
        # Delete child rows + WS_PROJECT in a single statement so self-referencing FKs resolve atomically.
        db.execute(
            "DELETE FROM {oa} WHERE record_id=%s OR workspace_project_ref=%s "
            "OR workspace_block_ref=ANY(%s) OR workspace_version_ref=ANY(%s)",
            (pref, pref, block_refs, version_refs),
        )
    except InsufficientPrivilege as exc:
        raise ReviewError("DATA_DELETE_NOT_CONFIGURED", "DB 계정에 DELETE 권한이 없어 완전 삭제할 수 없습니다.") from exc

    return {
        "status": "DELETED",
        "project_id": project_id,
        "name": preview["name"],
        "force": force,
        "deleted_counts": {
            **preview["counts"],
            "area_uploads": area_deleted["uploads"],
            "area_records": area_deleted["records"],
        },
    }
