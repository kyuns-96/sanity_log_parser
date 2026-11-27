"""TEMPORARY ARTIFACT FALLBACK (`di_area_power_tracking_record` -> `record_type='fp_artifact_tmp'`).

PURPOSE:
- Allows VWP-bridge and OA Floorplan Review to operate immediately BEFORE the DBA provisions
  `<schema>.di_design_artifact` in PostgreSQL, using only the already-provisioned
  `<schema>.di_area_power_tracking_record` table.
- Raw DEF/LEF/GDS/OAS files remain strictly forbidden (`reject_raw_def_or_lef` runs before this).

HOW IT SWITCHES / HOW TO DELETE LATER:
1. Automatic switch-over (`FLOORPLAN_TEMP_FALLBACK=auto`, default):
   - As soon as `<schema>.di_design_artifact` exists in PostgreSQL, new uploads automatically go
     to `di_design_artifact`, while any older `fp_artifact_tmp` rows remain readable (or can be
     migrated in place with identical UUIDs via `migrate_temp_artifacts_to_table(conn, schema)`).
   - Set `FLOORPLAN_TEMP_FALLBACK=0` to disable this fallback immediately without code edits.
2. Clean permanent removal (after `di_design_artifact` is provisioned):
   - Optional data migration (preserves all existing Floorplan versions/reviews/proposals):
       `python -c "... migrate_temp_artifacts_to_table(conn, schema)"`
   - Run `grep -rn "TEMP_ARTIFACT_FALLBACK" common/ vwp_bridge/ oa/` to remove the 4 tagged hooks
     in `vwp_bridge/area_power/floorplan_ingest.py`, `oa/floorplan/service.py`, and UI badge.
   - Delete this file (`common/floorplan/temp_artifact_fallback.py`).
"""
from __future__ import annotations

import base64
from hashlib import sha256
import os
from typing import Any
from uuid import UUID
import zlib

from psycopg import sql

from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest, dumps

TEMP_RECORD_TYPE = "fp_artifact_tmp"
MAX_TEMP_ARTIFACT_BYTES = 4 * 1024 * 1024  # 4 MiB uncompressed cap while riding JSONB


def is_temp_fallback_enabled() -> bool:
    """Return True unless explicitly disabled via FLOORPLAN_TEMP_FALLBACK=0/false/off."""
    raw = os.environ.get("FLOORPLAN_TEMP_FALLBACK", "auto").strip().lower()
    return raw not in ("0", "false", "off", "no", "disabled")


def should_write_temp_fallback(table_exists: bool) -> bool:
    """Write to `fp_artifact_tmp` when `di_design_artifact` is absent (or forced via =force/1)."""
    raw = os.environ.get("FLOORPLAN_TEMP_FALLBACK", "auto").strip().lower()
    if raw in ("0", "false", "off", "no", "disabled"):
        return False
    if raw in ("1", "true", "force", "only"):
        return True
    return not table_exists


def _compress_b64(data: bytes | None) -> str | None:
    if not data:
        return None
    return base64.b64encode(zlib.compress(data, level=6)).decode("ascii")


def _decompress_b64(encoded: str | None) -> bytes:
    if not encoded:
        return b""
    return zlib.decompress(base64.b64decode(encoded.encode("ascii")))


def temp_insert_artifact(
    conn: Any,
    schema: str,
    *,
    artifact_id: UUID,
    project_id: str,
    block_id: str,
    fp_ver: str,
    net_ver: str | None,
    revision: str | None,
    eco_num: str | None,
    layout: str | None,
    area_project_id: UUID | None,
    area_block_id: UUID | None,
    area_version_id: UUID | None,
    area_upload_id: UUID | None,
    artifact_kind: str,
    view_name: str,
    title: str,
    caption: str,
    metadata: dict[str, Any],
    original_filename: str,
    mime_type: str,
    width_px: int | None,
    height_px: int | None,
    artifact_bytes: bytes,
    thumbnail_bytes: bytes | None,
    uploaded_by: str,
) -> dict[str, Any]:
    if len(artifact_bytes) > MAX_TEMP_ARTIFACT_BYTES:
        raise DomainError(
            "Temporary DB fallback mode caps Floorplan SVG/attachments at 4 MiB until di_design_artifact is created.",
            code="file_too_large",
            status_code=413,
        )
    actor = (uploaded_by or "").strip() or "system"
    digest_hex = sha256(artifact_bytes).hexdigest()
    version_tag = f"fp:{fp_ver}"
    dedupe_key = digest([TEMP_RECORD_TYPE, project_id, block_id, version_tag, artifact_kind, view_name, digest_hex])
    tr_tbl = sql.Identifier(schema, "di_area_power_tracking_record")

    existing = conn.execute(
        sql.SQL("SELECT id, state, payload FROM {} WHERE record_type=%s AND dedupe_key=%s FOR UPDATE").format(tr_tbl),
        (TEMP_RECORD_TYPE, dedupe_key),
    ).fetchone()

    if existing and existing["state"] == "active":
        return {
            "id": existing["id"],
            "deduplicated": True,
            "byte_length": int((existing["payload"] or {}).get("byte_length") or len(artifact_bytes)),
            "storage_mode": "temp_tracking_fallback",
        }

    if view_name == "vector_master" and artifact_kind == "floorplan":
        conn.execute(
            sql.SQL(
                "UPDATE {} SET state='archived', row_version=row_version+1, updated_at=CURRENT_TIMESTAMP, updated_by=%s "
                "WHERE record_type=%s AND state='active' "
                "AND payload->>'native_project_id'=%s AND payload->>'native_block_id'=%s "
                "AND payload->>'version_id'=%s AND payload->>'artifact_kind'='floorplan' "
                "AND payload->>'view_name'='vector_master'"
            ).format(tr_tbl),
            (actor, TEMP_RECORD_TYPE, project_id, block_id, version_tag),
        )

    if existing:
        conn.execute(
            sql.SQL(
                "UPDATE {} SET state='active', row_version=row_version+1, updated_at=CURRENT_TIMESTAMP, updated_by=%s WHERE id=%s"
            ).format(tr_tbl),
            (actor, existing["id"]),
        )
        return {
            "id": existing["id"],
            "deduplicated": True,
            "byte_length": int((existing["payload"] or {}).get("byte_length") or len(artifact_bytes)),
            "storage_mode": "temp_tracking_fallback",
        }

    payload = {
        "native_project_id": project_id,
        "native_block_id": block_id,
        "version_id": version_tag,
        "fp_ver": fp_ver,
        "net_ver": net_ver,
        "revision": revision,
        "eco_num": eco_num,
        "layout": layout,
        "artifact_kind": artifact_kind,
        "view_name": view_name,
        "title": title,
        "caption": caption,
        "metadata": metadata,
        "original_filename": original_filename,
        "mime_type": mime_type,
        "width_px": width_px,
        "height_px": height_px,
        "sha256_hex": digest_hex,
        "byte_length": len(artifact_bytes),
        "artifact_zlib_b64": _compress_b64(artifact_bytes),
        "thumbnail_zlib_b64": _compress_b64(thumbnail_bytes),
    }

    conn.execute(
        sql.SQL(
            "INSERT INTO {} ("
            "id, record_type, entity_id, revision, payload_schema_version, row_version, "
            "project_id, version_id, block_id, upload_id, dedupe_key, state, payload, created_by, updated_by"
            ") VALUES (%s, %s, %s, 1, 1, 1, %s, %s, %s, %s, %s, 'active', %s::jsonb, %s, %s)"
        ).format(tr_tbl),
        (
            artifact_id,
            TEMP_RECORD_TYPE,
            artifact_id,
            area_project_id,
            area_version_id,
            area_block_id,
            area_upload_id,
            dedupe_key,
            dumps(payload),
            actor,
            actor,
        ),
    )
    return {
        "id": artifact_id,
        "deduplicated": False,
        "byte_length": len(artifact_bytes),
        "storage_mode": "temp_tracking_fallback",
    }


def temp_list_version_artifacts(
    conn: Any,
    schema: str,
    *,
    native_project_id: str,
    native_block_id: str,
    version_tag: str,
    area_project_id: UUID | None = None,
    area_block_id: UUID | None = None,
) -> list[dict[str, Any]]:
    if not is_temp_fallback_enabled():
        return []
    tr_tbl = sql.Identifier(schema, "di_area_power_tracking_record")
    if area_project_id and area_block_id:
        rows = conn.execute(
            sql.SQL(
                "SELECT id, payload, created_at FROM {} "
                "WHERE record_type=%s AND project_id=%s AND block_id=%s AND state='active' "
                "AND payload->>'version_id'=%s ORDER BY created_at DESC"
            ).format(tr_tbl),
            (TEMP_RECORD_TYPE, area_project_id, area_block_id, version_tag),
        ).fetchall()
    else:
        rows = conn.execute(
            sql.SQL(
                "SELECT id, payload, created_at FROM {} "
                "WHERE record_type=%s AND state='active' "
                "AND payload->>'native_project_id'=%s AND payload->>'native_block_id'=%s "
                "AND payload->>'version_id'=%s ORDER BY created_at DESC"
            ).format(tr_tbl),
            (TEMP_RECORD_TYPE, native_project_id, native_block_id, version_tag),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        p = r["payload"] or {}
        out.append({
            "id": r["id"],
            "artifact_kind": p.get("artifact_kind") or "floorplan",
            "view_name": p.get("view_name") or "",
            "title": p.get("title") or "",
            "caption": p.get("caption") or "",
            "mime_type": p.get("mime_type") or "image/svg+xml",
            "width_px": p.get("width_px"),
            "height_px": p.get("height_px"),
            "byte_length": int(p.get("byte_length") or 0),
            "metadata": p.get("metadata") or {},
            "created_at": r["created_at"],
        })
    return out


def temp_get_artifact(
    conn: Any,
    schema: str,
    artifact_id: UUID,
    *,
    include_bytes: bool = False,
) -> dict[str, Any] | None:
    if not is_temp_fallback_enabled():
        return None
    tr_tbl = sql.Identifier(schema, "di_area_power_tracking_record")
    row = conn.execute(
        sql.SQL("SELECT id, project_id, state, payload, created_at FROM {} WHERE id=%s AND record_type=%s").format(tr_tbl),
        (artifact_id, TEMP_RECORD_TYPE),
    ).fetchone()
    if not row:
        return None
    p = row["payload"] or {}
    sha_hex = str(p.get("sha256_hex") or "")
    res: dict[str, Any] = {
        "id": row["id"],
        "state": row["state"],
        "area_project_id": row["project_id"],
        "title": p.get("title") or "",
        "view_name": p.get("view_name") or "",
        "mime_type": p.get("mime_type") or "image/svg+xml",
        "sha256": bytes.fromhex(sha_hex) if len(sha_hex) == 64 else b"",
        "metadata": p.get("metadata") or {},
    }
    if include_bytes:
        res["artifact_bytes"] = _decompress_b64(p.get("artifact_zlib_b64"))
        thumb_b64 = p.get("thumbnail_zlib_b64")
        res["thumbnail_bytes"] = _decompress_b64(thumb_b64) if thumb_b64 else None
    return res


def migrate_temp_artifacts_to_table(conn: Any, schema: str, *, purge_after_migrate: bool = False) -> int:
    """One-shot helper to copy `fp_artifact_tmp` rows into `di_design_artifact` with identical UUIDs."""
    tr_tbl = sql.Identifier(schema, "di_area_power_tracking_record")
    art_tbl = sql.Identifier(schema, "di_design_artifact")
    rows = conn.execute(
        sql.SQL("SELECT * FROM {} WHERE record_type=%s ORDER BY created_at ASC").format(tr_tbl),
        (TEMP_RECORD_TYPE,),
    ).fetchall()
    migrated = 0
    for r in rows:
        p = r["payload"] or {}
        raw_bytes = _decompress_b64(p.get("artifact_zlib_b64"))
        if not raw_bytes:
            continue
        thumb_b64 = p.get("thumbnail_zlib_b64")
        thumb_bytes = _decompress_b64(thumb_b64) if thumb_b64 else None
        digest_bytes = sha256(raw_bytes).digest()
        exists = conn.execute(sql.SQL("SELECT 1 FROM {} WHERE id=%s").format(art_tbl), (r["id"],)).fetchone()
        if not exists:
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} ("
                    "id, project_id, block_id, version_id, net_ver, revision, eco_num, layout, "
                    "area_project_id, area_block_id, area_version_id, area_snapshot_id, area_upload_id, "
                    "artifact_kind, view_name, title, caption, tags, metadata, "
                    "original_filename, mime_type, width_px, height_px, sha256, byte_length, "
                    "artifact_bytes, thumbnail_bytes, state, uploaded_by, created_at, updated_at"
                    ") VALUES ("
                    "%s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, NULL, %s, "
                    "%s, %s, %s, %s, '[]'::jsonb, %s::jsonb, "
                    "%s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s"
                    ")"
                ).format(art_tbl),
                (
                    r["id"],
                    p.get("native_project_id") or str(r["project_id"] or ""),
                    p.get("native_block_id") or str(r["block_id"] or ""),
                    p.get("version_id"),
                    p.get("net_ver"),
                    p.get("revision"),
                    p.get("eco_num"),
                    p.get("layout"),
                    r["project_id"],
                    r["block_id"],
                    r["version_id"],
                    r["upload_id"],
                    p.get("artifact_kind") or "floorplan",
                    (p.get("view_name") or "").strip() or None,
                    (p.get("title") or "").strip() or None,
                    (p.get("caption") or "").strip() or None,
                    dumps(p.get("metadata") or {}),
                    p.get("original_filename") or "floorplan.svg",
                    p.get("mime_type") or "image/svg+xml",
                    p.get("width_px"),
                    p.get("height_px"),
                    digest_bytes,
                    len(raw_bytes),
                    raw_bytes,
                    thumb_bytes,
                    r["state"] if r["state"] in ("active", "archived") else "active",
                    r["created_by"],
                    r["created_at"],
                    r["updated_at"],
                ),
            )
            migrated += 1
    if purge_after_migrate and rows:
        conn.execute(sql.SQL("DELETE FROM {} WHERE record_type=%s").format(tr_tbl), (TEMP_RECORD_TYPE,))
    return migrated
