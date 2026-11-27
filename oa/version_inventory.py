"""Project-wide version and data inventory aggregation (Sanity reports + Area/Power CSVs)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import sql

from oa.atlas_area_sync import AreaAdapters
from oa.project_lifecycle import effective_project_info
from oa.sanity_workspace_sources import CURRENT_SOURCES_SQL


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _format_num(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value).strip()


def _corner_label(upload: dict[str, Any]) -> tuple[str, bool]:
    contract = upload.get("input_contract") or {}
    parts = [
        (upload.get("process_label") or "").strip(),
        f"{_format_num(upload.get('voltage_v'))}V" if upload.get("voltage_v") is not None else "",
        f"{_format_num(upload.get('temp_c'))}°C" if upload.get("temp_c") is not None else "",
        (upload.get("pex") or "").strip(),
    ]
    joined = " / ".join(p for p in parts if p)
    is_area_only = contract.get("has_power") is False or not joined
    return (joined or "Area 전용"), is_area_only


def _load_area_uploads_by_version(store, project_id: str, blocks: list[dict[str, Any]], versions: list[dict[str, Any]]) -> tuple[str | None, dict[str, str], dict[str, str], dict[str, list[dict[str, Any]]]]:
    try:
        from common.area_power.db import UPLOAD_METADATA
        from common.area_power.errors import ConfigurationError, DomainError
    except Exception:
        return None, {}, {}, {}
    try:
        adapters = AreaAdapters(store)
        bindings = adapters.bindings()
        with adapters.db.connect() as aconn:
            aconn.execute("SET TRANSACTION READ ONLY")
            rows = adapters.rows(aconn)
            p_row = adapters.match(rows, "project", project_id)
            if p_row is None:
                return None, {}, {}, {}
            area_pid = p_row["id"]
            area_block_by_native: dict[str, str] = {}
            for blk in blocks:
                b_row = adapters.match(rows, "block", blk["workspace_key"])
                if b_row is not None and b_row["project_id"] == area_pid:
                    area_block_by_native[blk["workspace_key"]] = str(b_row["id"])
            area_version_by_native: dict[str, str] = {}
            for ver in versions:
                vid = ver["workspace_key"]
                v_row = adapters.version_row(rows, bindings, vid)
                if v_row is not None and v_row["project_id"] == area_pid:
                    area_version_by_native[vid] = str(v_row["id"])

            csv_tbl = adapters.db.table("di_area_power_csv")
            cols = sql.SQL(", ").join(map(sql.Identifier, UPLOAD_METADATA))
            upload_rows = aconn.execute(
                sql.SQL("SELECT {} FROM {} WHERE project_id=%s ORDER BY created_at DESC, id DESC").format(cols, csv_tbl),
                (area_pid,),
            ).fetchall()
    except (ConfigurationError, DomainError):
        return None, {}, {}, {}

    by_area_ver: dict[str, list[dict[str, Any]]] = {}
    for row in upload_rows:
        avid = str(row["version_id"])
        contract = row.get("input_contract") or {}
        meta = contract.get("metadata") if isinstance(contract.get("metadata"), dict) else {}
        label, is_area_only = _corner_label(row)
        storage_mode = contract.get("storage_mode") or "standalone"
        by_area_ver.setdefault(avid, []).append({
            "id": str(row["id"]),
            "state": row["state"],
            "execution_name": row.get("execution_name"),
            "original_filename": row.get("original_filename"),
            "uploaded_by": row.get("uploaded_by"),
            "process_label": row.get("process_label"),
            "voltage_v": _format_num(row.get("voltage_v")) or None,
            "temp_c": _format_num(row.get("temp_c")) or None,
            "pex": row.get("pex"),
            "corner_label": label,
            "is_area_only": is_area_only,
            "is_power_layer": storage_mode == "power_layer",
            "storage_mode": storage_mode,
            "has_power": contract.get("has_power", not is_area_only),
            "eco_num": meta.get("eco_num"),
            "row_count": row.get("row_count"),
            "original_byte_length": row.get("original_byte_length"),
            "compressed_byte_length": row.get("compressed_byte_length"),
            "snapshot_id": str(row["snapshot_id"]) if row.get("snapshot_id") else None,
            "created_at": _iso(row.get("created_at")),
        })
    return str(area_pid), area_block_by_native, area_version_by_native, by_area_ver


def build_version_inventory(store, project_id: str, *, block_id: str | None = None, area_enabled: bool = False) -> dict[str, Any]:
    project_row = store.project(project_id)
    info = effective_project_info(store.db, project_row)
    pref = project_row["record_id"]

    if block_id:
        blocks = store.db.execute(
            "SELECT * FROM {oa} WHERE record_type='WS_BLOCK' AND workspace_project_ref=%s AND workspace_key=%s ORDER BY workspace_key",
            (pref, block_id),
        ).fetchall()
    else:
        blocks = store.db.execute(
            "SELECT * FROM {oa} WHERE record_type='WS_BLOCK' AND workspace_project_ref=%s ORDER BY workspace_key",
            (pref,),
        ).fetchall()

    block_by_ref = {b["record_id"]: b for b in blocks}
    block_refs = list(block_by_ref)

    por_by_block: dict[str, dict[str, Any]] = {}
    if block_refs:
        for row in store.db.execute(
            "SELECT workspace_key, version, state FROM {oa} WHERE record_type='WS_POR' AND workspace_block_ref=ANY(%s)",
            (block_refs,),
        ).fetchall():
            st = row["state"] or {}
            por_by_block[row["workspace_key"]] = {
                "revision": int(row["version"]),
                "version_id": st.get("version_id"),
                "assigned_by": st.get("assigned_by"),
                "assigned_at": st.get("assigned_at"),
            }

    versions = (
        store.db.execute(
            "SELECT * FROM {oa} WHERE record_type='WS_VERSION' AND workspace_block_ref=ANY(%s) "
            "ORDER BY created_at DESC, workspace_key DESC",
            (block_refs,),
        ).fetchall()
        if block_refs
        else []
    )
    version_refs = [v["record_id"] for v in versions]

    sanity_by_ver_ref: dict[Any, dict[str, Any]] = {}
    if version_refs:
        agg_rows = store.db.execute(
            "SELECT workspace_version_ref, "
            "count(*) AS total, "
            "count(*) FILTER (WHERE payload->>'check_status'='PASS') AS pass_cnt, "
            "count(*) FILTER (WHERE payload->>'check_status'='WARN') AS warn_cnt, "
            "count(*) FILTER (WHERE payload->>'check_status'='FAIL') AS fail_cnt, "
            "count(*) FILTER (WHERE payload->>'parse_status'='MISSING') AS missing_cnt, "
            "max(created_at) AS latest_at "
            "FROM {oa} WHERE record_type='WS_REPORT' AND workspace_version_ref=ANY(%s) AND " + CURRENT_SOURCES_SQL + " "
            "GROUP BY workspace_version_ref",
            (version_refs,),
        ).fetchall()
        for row in agg_rows:
            sanity_by_ver_ref[row["workspace_version_ref"]] = {
                "total": int(row["total"]),
                "pass": int(row["pass_cnt"]),
                "warn": int(row["warn_cnt"]),
                "fail": int(row["fail_cnt"]),
                "missing": int(row["missing_cnt"]),
                "latest_at": _iso(row["latest_at"]),
            }

    area_pid, area_block_map, area_ver_map, area_uploads_by_ver = (
        _load_area_uploads_by_version(store, project_id, blocks, versions)
        if area_enabled
        else (None, {}, {}, {})
    )

    writable_projects = None if store.local else {g["project_id"] for g in store.access.snapshot(store.actor.user_id)["grants"]}
    can_write = writable_projects is None or project_id in writable_projects

    items: list[dict[str, Any]] = []
    por_blocks_set: set[str] = set()
    with_reports = 0
    with_area = 0
    with_power = 0
    empty_versions = 0

    for ver in versions:
        vp = ver["payload"] or {}
        vid = ver["workspace_key"]
        blk = block_by_ref.get(ver["workspace_block_ref"])
        if blk is None:
            continue
        bid = blk["workspace_key"]
        bname = (blk["payload"] or {}).get("name") or bid
        por = por_by_block.get(bid) or {"revision": 0, "version_id": None}
        is_por = por.get("version_id") == vid
        if is_por:
            por_blocks_set.add(bid)

        sanity = sanity_by_ver_ref.get(ver["record_id"]) or {
            "total": 0, "pass": 0, "warn": 0, "fail": 0, "missing": 0, "latest_at": None,
        }

        avid = area_ver_map.get(vid)
        uploads = area_uploads_by_ver.get(avid, []) if avid else []
        ready_uploads = [u for u in uploads if u["state"] == "ready"]
        area_base_cnt = sum(1 for u in ready_uploads if not u["is_power_layer"])
        power_layer_cnt = sum(1 for u in ready_uploads if u["is_power_layer"])
        failed_cnt = sum(1 for u in uploads if u["state"] == "failed")
        has_area_val = area_base_cnt > 0 or len(ready_uploads) > 0
        has_power_val = any(u["has_power"] for u in ready_uploads)

        corner_map: dict[str, dict[str, Any]] = {}
        for u in ready_uploads:
            clabel = u["corner_label"]
            entry = corner_map.get(clabel)
            if entry is None:
                corner_map[clabel] = {
                    "label": clabel,
                    "is_area_only": u["is_area_only"],
                    "is_power_layer": u["is_power_layer"],
                    "count": 1,
                    "latest_upload_id": u["id"],
                    "latest_created_at": u["created_at"],
                }
            else:
                entry["count"] += 1
        corners = sorted(corner_map.values(), key=lambda c: (not c["is_area_only"], c["label"]))
        area_latest = uploads[0]["created_at"] if uploads else None

        if sanity["total"] > 0:
            with_reports += 1
        if has_area_val:
            with_area += 1
        if has_power_val:
            with_power += 1
        if sanity["total"] == 0 and not uploads:
            empty_versions += 1

        candidates_ts = [ts for ts in (sanity["latest_at"], area_latest, _iso(ver.get("created_at"))) if ts]
        latest_activity = max(candidates_ts) if candidates_ts else None

        items.append({
            "block_id": bid,
            "block_name": bname,
            "block_area_id": area_block_map.get(bid),
            "version_id": vid,
            "area_version_id": avid,
            "net_ver": vp.get("net_ver") or "",
            "revision": vp.get("revision") or "",
            "layout": vp.get("layout") or "",
            "eco_num": vp.get("eco_num"),
            "is_por": is_por,
            "por_revision": por["revision"],
            "created_at": _iso(ver.get("created_at")),
            "sanity": sanity,
            "area_power": {
                "has_area": has_area_val,
                "has_power": has_power_val,
                "area_base_count": area_base_cnt,
                "power_layer_count": power_layer_cnt,
                "failed_count": failed_cnt,
                "total_uploads": len(uploads),
                "latest_at": area_latest,
                "corners": corners,
                "uploads": uploads,
            },
            "latest_activity_at": latest_activity,
        })

    items.sort(key=lambda x: (x["block_name"], x["net_ver"], x["revision"], x["eco_num"] or ""), reverse=True)
    return {
        "project": {
            "id": project_id,
            "name": info["name"],
            "description": info["description"],
            "process": info["process"],
            "can_write": can_write,
            "can_manage": store.can_manage,
            "area_id": area_pid,
        },
        "area_enabled": area_enabled,
        "summary": {
            "total_blocks": len(blocks),
            "total_versions": len(items),
            "por_count": len(por_blocks_set),
            "with_reports": with_reports,
            "with_area": with_area,
            "with_power": with_power,
            "empty_versions": empty_versions,
        },
        "items": items,
    }
