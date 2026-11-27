"""Single-CSV Area/Power upload deletion with dependency preview and optional cascade."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.errors import InsufficientPrivilege

from common.area_power.analysis import observations as obs_mod
from common.area_power.layered_csv import is_power_layer_upload
from .schemas import reject


def _delete_available(db, conn) -> bool:
    schema = db.raw.settings.db_schema
    row = conn.execute(
        "SELECT has_table_privilege(current_user, quote_ident(%s)||'.di_area_power_csv', 'DELETE') "
        "AND has_table_privilege(current_user, quote_ident(%s)||'.di_area_power_tracking_record', 'DELETE') AS allowed",
        (schema, schema),
    ).fetchone()
    return bool(row and row["allowed"])


def _collect_dependencies(db, conn, upload: dict[str, Any]) -> dict[str, Any]:
    csv_tbl = db.table("di_area_power_csv")
    rec_tbl = db.table("di_area_power_tracking_record")
    uid = upload["id"]
    uid_str = str(uid)

    dep_layers = conn.execute(
        sql.SQL("SELECT id, execution_name, original_filename FROM {} WHERE input_contract->>'base_upload_id'=%s ORDER BY created_at DESC").format(csv_tbl),
        (uid_str,),
    ).fetchall()
    dep_layer_ids = [r["id"] for r in dep_layers]

    is_snap_source = conn.execute(
        sql.SQL("SELECT 1 FROM {} WHERE record_type='snapshot' AND upload_id=%s LIMIT 1").format(rec_tbl),
        (uid,),
    ).fetchone() is not None

    snapshot_siblings: list[dict[str, Any]] = []
    if is_snap_source and upload.get("snapshot_id") is not None:
        excluded = [uid, *dep_layer_ids]
        snapshot_siblings = conn.execute(
            sql.SQL("SELECT id, execution_name, original_filename FROM {} WHERE snapshot_id=%s AND id <> ALL(%s) ORDER BY created_at DESC").format(csv_tbl),
            (upload["snapshot_id"], excluded),
        ).fetchall()

    all_upload_ids = [uid, *dep_layer_ids, *[r["id"] for r in snapshot_siblings]]
    all_upload_strs = [str(x) for x in all_upload_ids]

    direct_rows = conn.execute(
        sql.SQL(
            "SELECT id, record_type FROM {} WHERE upload_id=ANY(%s) OR baseline_upload_id=ANY(%s) "
            "OR (record_type='comparison' AND ("
            "payload->'request'->>'current_upload_id'=ANY(%s) "
            "OR payload->'request'->>'baseline_upload_id'=ANY(%s) "
            "OR EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(payload->'request'->'power_pairs', '[]'::jsonb)) elem "
            "WHERE elem->>'current_upload_id'=ANY(%s) OR elem->>'baseline_upload_id'=ANY(%s)))) "
            "OR (record_type='tracking_slot' AND payload->>'upload_id'=ANY(%s))"
        ).format(rec_tbl),
        (all_upload_ids, all_upload_ids, all_upload_strs, all_upload_strs, all_upload_strs, all_upload_strs, all_upload_strs),
    ).fetchall()

    record_types: dict[Any, str] = {r["id"]: r["record_type"] for r in direct_rows}
    frontier = list(record_types)
    while frontier:
        children = conn.execute(
            sql.SQL("SELECT id, record_type FROM {} WHERE parent_id=ANY(%s) OR previous_id=ANY(%s)").format(rec_tbl),
            (frontier, frontier),
        ).fetchall()
        new_ids = []
        for child in children:
            if child["id"] not in record_types:
                record_types[child["id"]] = child["record_type"]
                new_ids.append(child["id"])
        frontier = new_ids

    comparisons = sum(1 for rtype in record_types.values() if rtype == "comparison")
    return {
        "dependent_power_layers": dep_layers,
        "snapshot_siblings": snapshot_siblings,
        "all_upload_ids": all_upload_ids,
        "all_record_ids": list(record_types),
        "comparisons": comparisons,
    }


def preview(service, identifier: UUID) -> dict[str, Any]:
    db = service.db
    with db.connect() as conn:
        row = db.upload(conn, identifier)
        service.authorize(conn, row, write=True)
        deps = _collect_dependencies(db, conn, row)
        can_del_priv = _delete_available(db, conn)

    dep_layers = len(deps["dependent_power_layers"])
    snap_siblings = len(deps["snapshot_siblings"])
    comparisons = deps["comparisons"]
    tracking_records = len(deps["all_record_ids"])
    standalone_unused = dep_layers == 0 and snap_siblings == 0 and comparisons == 0

    blockers: list[str] = []
    if not can_del_priv:
        blockers.append("upload_delete_not_configured")
    if dep_layers > 0:
        blockers.append("power_layers_depend_on_base")
    if snap_siblings > 0:
        blockers.append("snapshot_in_use")
    if comparisons > 0:
        blockers.append("upload_referenced_by_comparison")

    return {
        "upload_id": row["id"],
        "execution_name": row.get("execution_name"),
        "original_filename": row.get("original_filename"),
        "is_power_layer": is_power_layer_upload(row),
        "counts": {
            "dependent_power_layers": dep_layers,
            "snapshot_siblings": snap_siblings,
            "comparisons": comparisons,
            "tracking_records": tracking_records,
        },
        "is_standalone_unused": standalone_unused,
        "delete_available": can_del_priv,
        "can_delete": standalone_unused and can_del_priv,
        "can_force_delete": can_del_priv,
        "blockers": blockers,
    }


def remove(service, identifier: UUID, *, force: bool = False, reason: str = "") -> dict[str, Any]:
    db = service.db
    csv_tbl = db.table("di_area_power_csv")
    rec_tbl = db.table("di_area_power_tracking_record")
    with db.connect() as conn:
        row = db.upload(conn, identifier, lock=True)
        service.authorize(conn, row, write=True)
        if not _delete_available(db, conn):
            reject("Area/Power table DELETE permission is unavailable.", "upload_delete_not_configured", 503)
        deps = _collect_dependencies(db, conn, row)
        dep_layers = len(deps["dependent_power_layers"])
        snap_siblings = len(deps["snapshot_siblings"])
        comparisons = deps["comparisons"]
        if not force and (dep_layers > 0 or snap_siblings > 0 or comparisons > 0):
            reject(
                "This CSV is referenced by dependent Power layers or saved comparisons; enable cascade delete to remove them together.",
                "upload_in_use",
                409,
            )

        deleted_records = 0
        remaining = set(deps["all_record_ids"])
        try:
            while remaining:
                rem_list = list(remaining)
                leaf_rows = conn.execute(
                    sql.SQL(
                        "SELECT id FROM {} WHERE id=ANY(%s) "
                        "AND id NOT IN (SELECT parent_id FROM {} WHERE parent_id IS NOT NULL AND id=ANY(%s)) "
                        "AND id NOT IN (SELECT previous_id FROM {} WHERE previous_id IS NOT NULL AND id=ANY(%s))"
                    ).format(rec_tbl, rec_tbl, rec_tbl),
                    (rem_list, rem_list, rem_list),
                ).fetchall()
                leaves = [r["id"] for r in leaf_rows] or rem_list
                conn.execute(sql.SQL("DELETE FROM {} WHERE id=ANY(%s)").format(rec_tbl), (leaves,))
                deleted_records += len(leaves)
                remaining.difference_update(leaves)

            conn.execute(sql.SQL("DELETE FROM {} WHERE id=ANY(%s)").format(csv_tbl), (deps["all_upload_ids"],))
        except InsufficientPrivilege:
            reject("Area/Power table DELETE permission is unavailable.", "upload_delete_not_configured", 503)

    if deps["comparisons"] > 0:
        with obs_mod._VIEW_LOCK:
            obs_mod._VIEW_CACHE.clear()

    return {
        "deleted": True,
        "upload_id": identifier,
        "force": force,
        "reason": reason.strip(),
        "deleted_uploads": len(deps["all_upload_ids"]),
        "deleted_records": deleted_records,
    }
