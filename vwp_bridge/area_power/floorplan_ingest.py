"""VWP-bridge Floorplan ingestion into di_design_artifact + di_area_power_tracking_record.

Strictly blocks raw DEF/LEF files and persists only validated SVG/image artifacts + FloorplanScene metadata.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field

from common.area_power.analysis import advisory_lock, existing_dedupe
from common.area_power.atlas_bindings import (
    active_area_catalog_rows,
    resolve_area_only_project_and_block,
    resolve_bound_area_project_and_block,
    resolve_oa_project_and_block,
)
from common.area_power.errors import ConfigurationError, DomainError
from common.area_power.jsonutil import digest, dumps
from common.floorplan import FloorplanScene, reject_raw_def_or_lef, render_scene_png
from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_db import Database as HostDatabase
from .catalog import resolve_catalog

ALLOWED_ARTIFACT_KINDS = (
    "floorplan", "placement", "congestion", "clock_tree", "routing",
    "power_map", "ir_drop", "timing_map", "density_map", "drc_map", "screenshot", "other",
)
ALLOWED_MIMES = ("image/svg+xml", "image/png", "image/webp", "image/jpeg", "application/pdf")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


class FloorplanIngestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(min_length=1, max_length=200)
    block: str = Field(min_length=1, max_length=200)
    fp_ver: str = Field(min_length=1, max_length=120)
    prev_fp_ver: str | None = Field(default=None, max_length=120)
    scope_kind: str = Field(default="subblock", pattern="^(top|subblock)$")
    title: str = Field(default="", max_length=240)
    net_ver: str | None = Field(default=None, max_length=120)
    revision: str | None = Field(default=None, max_length=200)
    layout: str | None = Field(default=None, pattern="^(PRE|POST)$")
    eco_num: str | None = Field(default=None, max_length=120)
    scene: FloorplanScene


class FloorplanAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(min_length=1, max_length=200)
    block: str = Field(min_length=1, max_length=200)
    fp_ver: str = Field(min_length=1, max_length=120)
    artifact_kind: str = Field(default="screenshot")
    view_name: str = Field(min_length=1, max_length=120)
    mime_type: str = Field(default="image/png")
    title: str = Field(default="", max_length=240)
    caption: str = Field(default="", max_length=1000)
    net_ver: str | None = Field(default=None, max_length=120)
    revision: str | None = Field(default=None, max_length=200)
    layout: str | None = Field(default=None, pattern="^(PRE|POST)$")
    eco_num: str | None = Field(default=None, max_length=120)


def _oa_db_configured() -> bool:
    try:
        env = load_environment()
    except ReviewError:
        return False
    return bool(env.get("SANITY_OA_DSN") or env.get("SANITY_DB_HOST"))


def resolve_floorplan_scope(
    db: Any,
    *,
    project: str,
    block: str,
    net_ver: str | None = None,
    revision: str | None = None,
    layout: str | None = None,
    eco_num: str | None = None,
) -> dict[str, Any]:
    """Resolve native OA project/block keys + Area/Power UUIDs + optional latest Area CSV upload."""
    area_projects, area_blocks, _ = active_area_catalog_rows(db)
    if _oa_db_configured():
        try:
            with HostDatabase("oa") as host_db, host_db.transaction(readonly=True):
                ws_proj, ws_blk = resolve_oa_project_and_block(host_db, project, block)
                native_pid, native_bid = ws_proj["workspace_key"], ws_blk["workspace_key"]
            area_proj, area_blk = resolve_bound_area_project_and_block(
                area_projects, area_blocks, native_pid=native_pid, native_bid=native_bid,
                project_label=project, block_label=block,
            )
        except ReviewError as exc:
            raise ConfigurationError("Host OA catalog could not be queried.") from exc
    else:
        area_proj, area_blk = resolve_area_only_project_and_block(area_projects, area_blocks, project=project, block=block)
        native_pid = str((area_proj.get("payload") or {}).get("sanity_project_id") or area_proj["id"])
        native_bid = str((area_blk.get("payload") or {}).get("sanity_block_id") or area_blk["id"])

    area_version_id: UUID | None = None
    area_upload_id: UUID | None = None
    if net_ver:
        try:
            cat = resolve_catalog(
                db, project=project, block=block, net_ver=net_ver,
                revision=revision, layout=layout, eco_num=eco_num,
            )
            if cat.get("version_id"):
                area_version_id = UUID(str(cat["version_id"]))
        except DomainError:
            area_version_id = None

    with db.connect() as conn:
        filters: dict[str, Any] = {"project_id": area_proj["id"], "block_id": area_blk["id"], "state": "ready", "limit": 20}
        if area_version_id:
            filters["version_id"] = area_version_id
        ups = db.uploads(conn, **filters)
        if ups:
            area_upload_id = ups[0]["id"]
            if area_version_id is None:
                area_version_id = ups[0]["version_id"]

    return {
        "native_project_id": native_pid,
        "native_block_id": native_bid,
        "area_project_id": area_proj["id"],
        "area_block_id": area_blk["id"],
        "area_version_id": area_version_id,
        "area_upload_id": area_upload_id,
    }


def insert_design_artifact_row(
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
    digest_bytes = sha256(artifact_bytes).digest()
    version_tag = f"fp:{fp_ver}"
    tbl = sql.Identifier(schema, "di_design_artifact")

    # Check existing active duplicate on (project_id, block_id, version_id, artifact_kind, view_name, sha256)
    existing = conn.execute(
        sql.SQL(
            "SELECT id, sha256, byte_length, created_at FROM {} "
            "WHERE project_id=%s AND block_id=%s AND COALESCE(version_id,'')=%s "
            "AND artifact_kind=%s AND COALESCE(view_name,'')=%s AND sha256=%s AND state='active' LIMIT 1"
        ).format(tbl),
        (project_id, block_id, version_tag, artifact_kind, view_name, digest_bytes),
    ).fetchone()
    if existing:
        return {"id": existing["id"], "deduplicated": True, "byte_length": existing["byte_length"]}

    # Archive superseded vector_master for the same fp_ver + view_name when re-uploading an updated scene
    if view_name == "vector_master" and artifact_kind == "floorplan":
        conn.execute(
            sql.SQL(
                "UPDATE {} SET state='archived', updated_at=CURRENT_TIMESTAMP "
                "WHERE project_id=%s AND block_id=%s AND version_id=%s AND artifact_kind='floorplan' "
                "AND view_name='vector_master' AND state='active'"
            ).format(tbl),
            (project_id, block_id, version_tag),
        )

    conn.execute(
        sql.SQL(
            "INSERT INTO {} ("
            "id, project_id, block_id, version_id, net_ver, revision, eco_num, layout, "
            "area_project_id, area_block_id, area_version_id, area_snapshot_id, area_upload_id, "
            "artifact_kind, view_name, title, caption, tags, metadata, "
            "original_filename, mime_type, width_px, height_px, sha256, byte_length, "
            "artifact_bytes, thumbnail_bytes, state, uploaded_by"
            ") VALUES ("
            "%s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, NULL, %s, "
            "%s, %s, %s, %s, '[]'::jsonb, %s::jsonb, "
            "%s, %s, %s, %s, %s, %s, "
            "%s, %s, 'active', %s"
            ")"
        ).format(tbl),
        (
            artifact_id, project_id, block_id, version_tag, net_ver, revision, eco_num, layout,
            area_project_id, area_block_id, area_version_id, area_upload_id,
            artifact_kind, view_name, title or None, caption or None, dumps(metadata),
            original_filename, mime_type, width_px, height_px, digest_bytes, len(artifact_bytes),
            artifact_bytes, thumbnail_bytes, uploaded_by,
        ),
    )
    return {"id": artifact_id, "deduplicated": False, "byte_length": len(artifact_bytes)}


def ingest_floorplan(
    db: Any,
    settings: Any,
    svg_bytes: bytes,
    filename: str,
    body: FloorplanIngestInput,
    actor: str,
) -> dict[str, Any]:
    reject_raw_def_or_lef(svg_bytes, filename)
    if not svg_bytes or len(svg_bytes) > MAX_ARTIFACT_BYTES:
        raise DomainError("Floorplan SVG size must be between 1 byte and 16 MiB.", code="file_too_large", status_code=413)
    if not svg_bytes.lstrip().startswith((b"<svg", b"<?xml")):
        raise DomainError("Master floorplan artifact must be valid SVG XML.", code="invalid_svg", status_code=422)

    scope = resolve_floorplan_scope(
        db,
        project=body.project,
        block=body.block,
        net_ver=body.net_ver,
        revision=body.revision,
        layout=body.layout,
        eco_num=body.eco_num,
    )
    thumb_png = render_scene_png(body.scene, mode="overview", max_dim=360)
    if len(thumb_png) > 262144:
        thumb_png = None  # type: ignore[assignment]

    artifact_id = uuid4()
    fp_key = digest(["fp_version", str(scope["area_project_id"]), str(scope["area_block_id"]), body.fp_ver])
    scene_payload = body.scene.model_dump(mode="python")

    with db.connect() as conn:
        advisory_lock(conn, fp_key)
        art = insert_design_artifact_row(
            conn,
            settings.db_schema,
            artifact_id=artifact_id,
            project_id=scope["native_project_id"],
            block_id=scope["native_block_id"],
            fp_ver=body.fp_ver,
            net_ver=body.net_ver,
            revision=body.revision,
            eco_num=body.eco_num,
            layout=body.layout,
            area_project_id=scope["area_project_id"],
            area_block_id=scope["area_block_id"],
            area_version_id=scope["area_version_id"],
            area_upload_id=scope["area_upload_id"],
            artifact_kind="floorplan",
            view_name="vector_master",
            title=body.title or f"{body.scene.design_name} ({body.fp_ver})",
            caption="",
            metadata=scene_payload,
            original_filename=filename,
            mime_type="image/svg+xml",
            width_px=1600,
            height_px=1200,
            artifact_bytes=svg_bytes,
            thumbnail_bytes=thumb_png,
            uploaded_by=actor,
        )
        existing_fp = existing_dedupe(db, conn, "fp_version", fp_key, lock=True)
        rec_payload = {
            "fp_ver": body.fp_ver,
            "prev_fp_ver": body.prev_fp_ver,
            "scope_kind": body.scope_kind,
            "title": body.title or f"{body.scene.design_name} · {body.fp_ver}",
            "design_name": body.scene.design_name,
            "native_project_id": scope["native_project_id"],
            "native_block_id": scope["native_block_id"],
            "master_artifact_id": str(art["id"]),
            "linked_net_ver": body.net_ver,
            "linked_revision": body.revision,
            "linked_layout": body.layout,
            "linked_eco_num": body.eco_num,
            "linked_area_version_id": str(scope["area_version_id"]) if scope["area_version_id"] else None,
            "linked_area_upload_id": str(scope["area_upload_id"]) if scope["area_upload_id"] else None,
            "stats": body.scene.stats,
            "bist_summary": [b.model_dump(mode="python") for b in body.scene.bist_summary],
        }
        if existing_fp is not None:
            saved_fp = db.update_record(
                conn, existing_fp["id"], actor,
                version_id=scope["area_version_id"],
                upload_id=scope["area_upload_id"],
                payload=rec_payload,
            )
        else:
            saved_fp = db.insert_record(
                conn, "fp_version", rec_payload, actor,
                state="active",
                project_id=scope["area_project_id"],
                block_id=scope["area_block_id"],
                version_id=scope["area_version_id"],
                upload_id=scope["area_upload_id"],
                dedupe_key=fp_key,
            )

    return {
        "fp_version_id": saved_fp["id"],
        "fp_ver": body.fp_ver,
        "artifact_id": art["id"],
        "deduplicated": art["deduplicated"],
        "linked_area_upload_id": scope["area_upload_id"],
    }


def ingest_floorplan_attachment(
    db: Any,
    settings: Any,
    img_bytes: bytes,
    filename: str,
    body: FloorplanAttachmentInput,
    actor: str,
) -> dict[str, Any]:
    reject_raw_def_or_lef(img_bytes, filename)
    if body.artifact_kind not in ALLOWED_ARTIFACT_KINDS:
        raise DomainError("Unsupported artifact_kind.", code="invalid_kind", status_code=422)
    if body.mime_type not in ALLOWED_MIMES:
        raise DomainError("Unsupported mime_type.", code="invalid_mime", status_code=422)
    if not img_bytes or len(img_bytes) > MAX_ARTIFACT_BYTES:
        raise DomainError("Attachment size must be between 1 byte and 16 MiB.", code="file_too_large", status_code=413)

    scope = resolve_floorplan_scope(
        db,
        project=body.project,
        block=body.block,
        net_ver=body.net_ver,
        revision=body.revision,
        layout=body.layout,
        eco_num=body.eco_num,
    )
    artifact_id = uuid4()
    with db.connect() as conn:
        art = insert_design_artifact_row(
            conn,
            settings.db_schema,
            artifact_id=artifact_id,
            project_id=scope["native_project_id"],
            block_id=scope["native_block_id"],
            fp_ver=body.fp_ver,
            net_ver=body.net_ver,
            revision=body.revision,
            eco_num=body.eco_num,
            layout=body.layout,
            area_project_id=scope["area_project_id"],
            area_block_id=scope["area_block_id"],
            area_version_id=scope["area_version_id"],
            area_upload_id=scope["area_upload_id"],
            artifact_kind=body.artifact_kind,
            view_name=body.view_name,
            title=body.title or body.view_name,
            caption=body.caption,
            metadata={"fp_ver": body.fp_ver},
            original_filename=filename,
            mime_type=body.mime_type,
            width_px=None,
            height_px=None,
            artifact_bytes=img_bytes,
            thumbnail_bytes=img_bytes if (body.mime_type in ("image/png", "image/webp", "image/jpeg") and len(img_bytes) <= 262144) else None,
            uploaded_by=actor,
        )
    return {"artifact_id": art["id"], "fp_ver": body.fp_ver, "view_name": body.view_name, "deduplicated": art["deduplicated"]}
