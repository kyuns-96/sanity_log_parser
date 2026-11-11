"""Validate outside transactions; atomically persist immutable upload executions."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from area_power.common.errors import DomainError
from area_power.common.jsonutil import digest
from area_power.common.models import UploadMetadata
from area_power.vwp_server.csv_input import parse_csv, structural_metadata, structural_rows


def _scope_matches(row: dict[str, Any], metadata: UploadMetadata) -> bool:
    return all(row.get(key) == getattr(metadata, key) for key in ("project_id", "version_id", "block_id"))


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


def ingest_upload(db: Any, settings: Any, data: bytes, filename: str, metadata: UploadMetadata, actor: str) -> dict[str, Any]:
    """Every invocation creates an independent execution; no automatic selection."""
    parsed = parse_csv(data, filename, metadata, settings.max_uncompressed_bytes)
    upload_id = uuid4()
    snapshot_id = metadata.snapshot_id or uuid4()
    source = None
    observed_snapshot = None
    if metadata.snapshot_id is not None:
        # Read only, without a snapshot row lock. Release the connection before
        # decompressing/parsing or comparing the complete existing structure.
        with db.connect() as conn:
            observed_snapshot = db.record(conn, snapshot_id, kind="snapshot")
            source_id = _snapshot_source(observed_snapshot, metadata)
            source = db.upload(conn, source_id, include_blob=True)
        if source.get("state") != "ready" or source.get("snapshot_id") != snapshot_id or not _scope_matches(source, metadata):
            raise DomainError("Snapshot source upload is not ready or has inconsistent scope.", code="snapshot_conflict", status_code=409)
        try:
            original_metadata = UploadMetadata.model_validate(source["input_contract"]["metadata"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError("Snapshot source input contract is unavailable.", code="snapshot_conflict", status_code=409) from exc
        existing = parse_csv(bytes(source["csv_gzip"]), source["original_filename"], original_metadata, settings.max_uncompressed_bytes)
        expected_metadata = structural_metadata(metadata)
        if structural_metadata(original_metadata) != expected_metadata:
            raise DomainError("Snapshot reuse requires identical structural metadata.", code="snapshot_conflict", status_code=409)
        if any(observed_snapshot["payload"].get(key) != value for key, value in expected_metadata.items()):
            raise DomainError("Snapshot structural metadata is inconsistent with its source.", code="snapshot_conflict", status_code=409)
        # Digest is an accelerator/integrity check, never the sole equality test.
        if existing.structure_digest != parsed.structure_digest or structural_rows(existing) != structural_rows(parsed):
            raise DomainError("Implementation structure changed; create a new snapshot.", code="snapshot_conflict", status_code=409)
        if bytes(source["original_sha256"]) != sha256(existing.original_bytes).digest():
            raise DomainError("Snapshot structure source checksum is inconsistent.", code="snapshot_conflict", status_code=409)
        if bytes(source["structure_digest"]) != existing.structure_digest or observed_snapshot["payload"].get("structure_digest") != existing.structure_digest.hex():
            raise DomainError("Snapshot structure digest is inconsistent with its source.", code="snapshot_conflict", status_code=409)
        del existing
    scope = {"project_id": metadata.project_id, "version_id": metadata.version_id, "block_id": metadata.block_id, "snapshot_id": snapshot_id}
    with db.connect() as conn:
        _validate_catalog(db, conn, metadata)
        if observed_snapshot is not None:
            locked = db.record(conn, snapshot_id, lock=True, kind="snapshot")
            source_id = _snapshot_source(locked, metadata)
            if locked.get("row_version") != observed_snapshot.get("row_version") or digest(locked["payload"]) != digest(observed_snapshot["payload"]):
                raise DomainError("Snapshot changed while validating the upload.", code="snapshot_conflict", status_code=409)
            # Immutable ready originals are never rewritten. Check lightweight
            # identity/contract metadata at commit, without fetching the blob again.
            current_source = db.upload(conn, source_id, lock=True)
            identity_keys = ("state", "project_id", "version_id", "block_id", "snapshot_id", "original_sha256", "structure_digest", "input_contract", "original_byte_length", "compressed_byte_length")
            if any(current_source.get(key) != source.get(key) for key in identity_keys):
                raise DomainError("Snapshot source changed while validating the upload.", code="snapshot_conflict", status_code=409)
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
        if metadata.snapshot_id is None:
            db.insert_record(
                conn, "snapshot", {**structural_metadata(metadata), "source_upload_id": upload_id,
                    "structure_digest": parsed.structure_digest.hex(), "root_path": parsed.root_path,
                    "row_count": parsed.row_count}, actor,
                id=snapshot_id, entity_id=snapshot_id, revision=1, **scope,
                upload_id=upload_id, state="ready",
            )
    return {"upload": saved, "snapshot_id": snapshot_id}
