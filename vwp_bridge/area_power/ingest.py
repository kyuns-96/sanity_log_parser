"""Validate outside transactions; atomically persist immutable upload executions."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from common.area_power.errors import DomainError
from common.area_power.jsonutil import digest
from common.area_power.models import FIXED_AREA_UNIT, UploadMetadata
from common.area_power.csv_input import parse_csv, structural_metadata, structural_rows
from common.area_power.layered_csv import (
    is_power_layer_upload,
    is_power_only_csv_bytes,
    parse_power_only_against_base,
    strip_to_power_only_csv,
)
from .tracking.slots import on_upload_failed, on_upload_ready


def _scope_matches(row: dict[str, Any], metadata: UploadMetadata) -> bool:
    return all(row.get(key) == getattr(metadata, key) for key in ("project_id", "version_id", "block_id"))


def _latest_ready_snapshot(db: Any, conn: Any, metadata: UploadMetadata) -> dict[str, Any] | None:
    rows = db.records(
        conn, "snapshot",
        project_id=metadata.project_id, version_id=metadata.version_id, block_id=metadata.block_id,
        state="ready", latest=True, limit=1,
    )
    return rows[0] if rows else None


def _find_duplicate_upload(
    db: Any,
    conn: Any,
    metadata: UploadMetadata,
    snapshot_id: UUID | None,
    raw_sha256_hex: str,
    parsed: Any,
) -> dict[str, Any] | None:
    if snapshot_id is None:
        return None
    rows = db.uploads(
        conn,
        project_id=metadata.project_id, version_id=metadata.version_id,
        block_id=metadata.block_id, snapshot_id=snapshot_id, state="ready", limit=200,
    )
    for row in rows:
        contract = row.get("input_contract") or {}
        existing_raw_sha = contract.get("raw_original_sha256") or (
            bytes(row["original_sha256"]).hex() if row.get("original_sha256") else None
        )
        if existing_raw_sha != raw_sha256_hex:
            continue
        if bytes(row["structure_digest"]) != bytes(parsed.structure_digest):
            continue
        if bytes(row["condition_key"]) != bytes(parsed.condition_key):
            continue
        existing_meta = contract.get("metadata") or {}
        if (existing_meta.get("eco_num") or "").strip() != (metadata.eco_num or "").strip():
            continue
        if existing_meta.get("design_stage") != metadata.design_stage:
            continue
        return row
    return None


def _validate_catalog(db: Any, conn: Any, metadata: UploadMetadata) -> None:
    project = db.record(conn, metadata.project_id, kind="project")
    version = db.record(conn, metadata.version_id, kind="version")
    block = db.record(conn, metadata.block_id, kind="block")
    for record in (project, version, block):
        if record.get("state") != "active":
            raise DomainError("Upload catalog references must be active.", code="invalid_scope")
        if record.get("entity_id") != record["id"] or record.get("revision") != 1:
            raise DomainError("Upload catalog references must identify catalog entities.", code="invalid_scope")
    if version.get("project_id") != metadata.project_id or block.get("project_id") != metadata.project_id:
        raise DomainError("Version and block must belong to the selected project.", code="invalid_scope")
    if version.get("version_id") != metadata.version_id or block.get("block_id") != metadata.block_id:
        raise DomainError("Catalog scope identifiers do not match.", code="invalid_scope")
    version_eco = (version.get("payload") or {}).get("eco_num")
    if version_eco is not None and str(version_eco).strip() and metadata.eco_num and metadata.eco_num.strip() != str(version_eco).strip():
        raise DomainError("Upload eco_num does not match the target version's eco_num.", code="eco_num_mismatch", status_code=422)


def _snapshot_source(snapshot: dict[str, Any], metadata: UploadMetadata) -> UUID:
    if snapshot.get("state") != "ready" or not _scope_matches(snapshot, metadata):
        raise DomainError("Snapshot is not ready or does not belong to the selected scope.", code="snapshot_conflict", status_code=409)
    if snapshot.get("snapshot_id") != snapshot["id"] or snapshot.get("entity_id") != snapshot["id"]:
        raise DomainError("Snapshot identity is inconsistent.", code="snapshot_conflict", status_code=409)
    try:
        source_id = UUID(str(snapshot["payload"]["source_upload_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError("Snapshot has no valid immutable structure source.", code="snapshot_conflict", status_code=409) from exc
    if source_id != snapshot.get("upload_id"):
        raise DomainError("Snapshot structure source is inconsistent.", code="snapshot_conflict", status_code=409)
    return source_id


def _load_snapshot_and_base(
    db: Any, metadata: UploadMetadata, explicit_snapshot_id: UUID | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    with db.connect() as conn:
        if explicit_snapshot_id is not None:
            observed = db.record(conn, explicit_snapshot_id, kind="snapshot")
        else:
            observed = _latest_ready_snapshot(db, conn, metadata)
            if observed is None:
                return None, None
        source_id = _snapshot_source(observed, metadata)
        source = db.upload(conn, source_id, include_blob=True)
    return observed, source


def _verify_snapshot_source_row(source: dict[str, Any], snapshot_id: UUID, metadata: UploadMetadata) -> UploadMetadata:
    if source.get("state") != "ready" or source.get("snapshot_id") != snapshot_id or not _scope_matches(source, metadata):
        raise DomainError("Snapshot source upload is not ready or has inconsistent scope.", code="snapshot_conflict", status_code=409)
    if is_power_layer_upload(source):
        raise DomainError("Snapshot source upload cannot be a power-only layer.", code="snapshot_conflict", status_code=409)
    try:
        return UploadMetadata.model_validate(source["input_contract"]["metadata"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError("Snapshot source input contract is unavailable.", code="snapshot_conflict", status_code=409) from exc


def ingest_upload(db: Any, settings: Any, data: bytes, filename: str, metadata: UploadMetadata, actor: str) -> dict[str, Any]:
    """Validate outside transactions; store Area once per snapshot and layer Power corners cleanly."""
    if not metadata.eco_num or not metadata.eco_num.strip():
        raise DomainError("eco_num is required for all Area/Power uploads.", code="eco_num_required", status_code=422)

    power_only_input = is_power_only_csv_bytes(data, settings.max_uncompressed_bytes)
    observed_snapshot: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    create_snapshot = False

    if power_only_input:
        observed_snapshot, source = _load_snapshot_and_base(db, metadata, metadata.snapshot_id)
        if observed_snapshot is None or source is None:
            raise DomainError(
                "Upload an Area-only (or full Area/Power) CSV for this version before uploading a Power-only CSV.",
                code="area_base_required",
                status_code=409,
            )
        snapshot_id = observed_snapshot["id"]
        base_metadata = _verify_snapshot_source_row(source, snapshot_id, metadata)
        try:
            parsed = parse_power_only_against_base(
                data, filename, metadata, source, base_metadata, settings.max_uncompressed_bytes,
            )
        except DomainError as exc:
            if exc.code == "invalid_csv":
                on_upload_failed(db, metadata, filename, exc)
            raise
        raw_sha256_hex = parsed.input_contract["raw_original_sha256"]
    else:
        try:
            parsed = parse_csv(data, filename, metadata, settings.max_uncompressed_bytes)
        except DomainError as exc:
            if exc.code == "invalid_csv":
                on_upload_failed(db, metadata, filename, exc)
            raise
        raw_sha256_hex = sha256(parsed.original_bytes).hexdigest()
        parsed.input_contract["raw_original_sha256"] = raw_sha256_hex
        parsed.input_contract["storage_mode"] = "standalone"

        if metadata.snapshot_id is not None:
            snapshot_id = metadata.snapshot_id
            observed_snapshot, source = _load_snapshot_and_base(db, metadata, snapshot_id)
            assert observed_snapshot is not None and source is not None
            original_metadata = _verify_snapshot_source_row(source, snapshot_id, metadata)
            existing = parse_csv(bytes(source["csv_gzip"]), source["original_filename"], original_metadata, settings.max_uncompressed_bytes)
            expected_metadata = structural_metadata(metadata)
            if structural_metadata(original_metadata) != expected_metadata:
                raise DomainError("Snapshot reuse requires identical structural metadata.", code="snapshot_conflict", status_code=409)
            if any(
                observed_snapshot["payload"].get(key) != value
                and not (key == "area_unit" and observed_snapshot["payload"].get(key) is None and value == FIXED_AREA_UNIT)
                for key, value in expected_metadata.items()
            ):
                raise DomainError("Snapshot structural metadata is inconsistent with its source.", code="snapshot_conflict", status_code=409)
            if existing.structure_digest != parsed.structure_digest or structural_rows(existing) != structural_rows(parsed):
                raise DomainError("Implementation structure changed; create a new snapshot.", code="snapshot_conflict", status_code=409)
            if bytes(source["original_sha256"]) != sha256(existing.original_bytes).digest():
                raise DomainError("Snapshot structure source checksum is inconsistent.", code="snapshot_conflict", status_code=409)
            if bytes(source["structure_digest"]) != existing.structure_digest or observed_snapshot["payload"].get("structure_digest") != existing.structure_digest.hex():
                raise DomainError("Snapshot structure digest is inconsistent with its source.", code="snapshot_conflict", status_code=409)
            del existing
            if parsed.has_power:
                _, thin_gzip = strip_to_power_only_csv(parsed.original_bytes)
                parsed = parse_power_only_against_base(
                    thin_gzip, filename, metadata, source, original_metadata,
                    settings.max_uncompressed_bytes, raw_original_sha256=raw_sha256_hex,
                )
        else:
            candidate_snap, candidate_src = _load_snapshot_and_base(db, metadata, None)
            reused = False
            if candidate_snap is not None and candidate_src is not None and not is_power_layer_upload(candidate_src):
                try:
                    base_metadata = _verify_snapshot_source_row(candidate_src, candidate_snap["id"], metadata)
                except DomainError:
                    base_metadata = None
                if (
                    base_metadata is not None
                    and base_metadata.hierarchy_separator == metadata.hierarchy_separator
                    and bytes(candidate_src["structure_digest"]) == bytes(parsed.structure_digest)
                ):
                    existing = parse_csv(bytes(candidate_src["csv_gzip"]), candidate_src["original_filename"], base_metadata, settings.max_uncompressed_bytes)
                    if (
                        bytes(candidate_src["original_sha256"]) == sha256(existing.original_bytes).digest()
                        and existing.structure_digest == parsed.structure_digest
                        and structural_rows(existing) == structural_rows(parsed)
                    ):
                        reused = True
                        observed_snapshot = candidate_snap
                        source = candidate_src
                        snapshot_id = candidate_snap["id"]
                        del existing
                        if parsed.has_power:
                            _, thin_gzip = strip_to_power_only_csv(parsed.original_bytes)
                            parsed = parse_power_only_against_base(
                                thin_gzip, filename, metadata, source, base_metadata,
                                settings.max_uncompressed_bytes, raw_original_sha256=raw_sha256_hex,
                            )
                    else:
                        del existing
            if not reused:
                snapshot_id = uuid4()
                create_snapshot = True

    upload_id = uuid4()
    scope = {"project_id": metadata.project_id, "version_id": metadata.version_id, "block_id": metadata.block_id, "snapshot_id": snapshot_id}
    with db.connect() as conn:
        _validate_catalog(db, conn, metadata)
        if observed_snapshot is not None and source is not None:
            locked = db.record(conn, snapshot_id, lock=True, kind="snapshot")
            source_id = _snapshot_source(locked, metadata)
            if locked.get("row_version") != observed_snapshot.get("row_version") or digest(locked["payload"]) != digest(observed_snapshot["payload"]):
                raise DomainError("Snapshot changed while validating the upload.", code="snapshot_conflict", status_code=409)
            current_source = db.upload(conn, source_id, lock=True)
            identity_keys = ("state", "project_id", "version_id", "block_id", "snapshot_id", "original_sha256", "structure_digest", "input_contract", "original_byte_length", "compressed_byte_length")
            if any(current_source.get(key) != source.get(key) for key in identity_keys):
                raise DomainError("Snapshot source changed while validating the upload.", code="snapshot_conflict", status_code=409)
            duplicate = _find_duplicate_upload(db, conn, metadata, snapshot_id, raw_sha256_hex, parsed)
            if duplicate is not None:
                return {"upload": duplicate, "snapshot_id": duplicate["snapshot_id"], "deduplicated": True}
        saved = db.insert_upload(
            conn, id=upload_id, **scope, execution_name=metadata.execution_name,
            original_filename=filename, uploaded_by=actor, **parsed.corner,
            condition_key=parsed.condition_key, conditions_complete=parsed.conditions_complete,
            analysis_conditions=parsed.conditions, structure_digest=parsed.structure_digest,
            input_contract=parsed.input_contract, root_summary=parsed.root_summary,
            encoding="utf-8-sig", csv_gzip=parsed.gzip_bytes,
            original_sha256=sha256(parsed.original_bytes).digest(),
            original_byte_length=len(parsed.original_bytes), compressed_byte_length=len(parsed.gzip_bytes),
            row_count=parsed.row_count, state="ready", validation_report=parsed.validation_report,
            error_detail=None, validated_at=datetime.now(timezone.utc),
        )
        if create_snapshot:
            db.insert_record(
                conn, "snapshot", {**structural_metadata(metadata), "source_upload_id": upload_id,
                    "structure_digest": parsed.structure_digest.hex(), "root_path": parsed.root_path,
                    "row_count": parsed.row_count}, actor,
                id=snapshot_id, entity_id=snapshot_id, revision=1, **scope,
                upload_id=upload_id, state="ready",
            )
    on_upload_ready(db, settings, saved)
    return {"upload": saved, "snapshot_id": snapshot_id, "deduplicated": False}
