"""Shared integrity checks, markers, and revisioned readers for immutable WS_CONFIG rows."""
from __future__ import annotations

from typing import Any, Iterable

from common.sanity_common import ReviewError
from oa.sanity_workspace_core import WorkspaceCoreStore, record_id

WS_CONFIG_NULL_COLUMNS = (
    "project", "run_id", "bundle_id", "review_unit_id", "review_revision", "request_id",
    "entity_key", "classification_revision", "payload_bytes", "payload_sha256",
    "workspace_project_ref", "workspace_block_ref", "workspace_version_ref",
    "workspace_review_report_ref", "workspace_review_version_ref", "workspace_review_event_ref",
    "workspace_pipeline_report_ref", "workspace_pipeline_version_ref",
    "workspace_pipeline_event_ref", "workspace_pipeline_cluster_ref",
)


def verify_ws_config_row(
    row: dict[str, Any],
    expected_key: str | None = None,
    *,
    error_code: str,
    message: str = "Invalid immutable WS_CONFIG record",
) -> dict[str, Any]:
    """Validate immutable physical columns and deterministic UUID of a WS_CONFIG row."""
    key = row.get("workspace_key") if expected_key is None else expected_key
    valid = (
        isinstance(key, str)
        and row.get("workspace_key") == key
        and row.get("record_id") == record_id("WS_CONFIG", key)
        and row.get("state") == {}
        and row.get("version") == 0
        and ("status" not in row or row["status"] == "RECORDED")
        and row.get("created_at") == row.get("updated_at")
        and all(row.get(col) is None for col in WS_CONFIG_NULL_COLUMNS)
        and isinstance(row.get("payload"), dict)
    )
    if not valid:
        raise ReviewError(error_code, message)
    return row["payload"]


def read_ws_config_marker(
    db,
    key: str,
    *,
    namespace: str,
    expected_id: str,
    required_keys: Iterable[str],
    error_code: str,
) -> dict[str, Any] | None:
    """Read and verify a single-write WS_CONFIG marker (delete / resume / unknown recovery)."""
    row = db.execute("SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (key,)).fetchone()
    if not row:
        return None
    value = verify_ws_config_row(row, key, error_code=error_code)
    required = set(required_keys)
    if not (required <= set(value) and value.get("namespace") == namespace and value.get("id") == expected_id):
        raise ReviewError(error_code, "Invalid WS_CONFIG marker payload")
    return value


def read_latest_revisioned_ws_config(
    db,
    prefix: str,
    *,
    error_code: str,
    max_revision: int = 9_999_999_999,
) -> tuple[dict[str, Any], dict[str, Any]] | tuple[None, None]:
    """Fetch the highest-key WS_CONFIG row under ``prefix`` and verify its 10-digit revision key."""
    row = db.execute(
        "SELECT * FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s "
        "ORDER BY workspace_key DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    if row is None:
        return None, None
    value = row.get("payload")
    revision = value.get("revision") if isinstance(value, dict) else None
    if not (type(revision) is int and 1 <= revision <= max_revision):
        raise ReviewError(error_code, "Invalid WS_CONFIG revision")
    verify_ws_config_row(row, prefix + f"{revision:010d}", error_code=error_code)
    return row, value


def write_revisioned_ws_config(db, prefix: str, revision: int, payload: dict[str, Any]) -> None:
    WorkspaceCoreStore(db).put("WS_CONFIG", prefix + f"{revision:010d}", payload)
