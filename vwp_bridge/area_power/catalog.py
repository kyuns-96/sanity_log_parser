"""Read-only lookup of pre-registered, OA-bound Area/Power catalog UUIDs.

Never creates, mutates, binds, or fuzzy-matches catalog records. Returns exact
project/block/version UUIDs only when a single active catalog scope exists and
passes the same Area catalog contract checked at upload time.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import sql

from common.area_power.errors import ConfigurationError, DomainError
from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_db import Database as HostDatabase

BINDING_PREFIX = "design-atlas-v1.binding."
ALLOWED_LAYOUTS = frozenset({"PRE", "POST"})


def _clean(value: str | None, field: str, *, required: bool = False, max_len: int = 1000) -> str | None:
    if value is None:
        if required:
            raise DomainError(f"{field} is required.", code="invalid_request")
        return None
    if not isinstance(value, str):
        raise DomainError(f"{field} must be a string.", code="invalid_request")
    text = value.strip()
    if not text:
        if required:
            raise DomainError(f"{field} must not be blank.", code="invalid_request")
        return None
    if len(text) > max_len or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise DomainError(f"{field} contains invalid characters or exceeds length limit.", code="invalid_request")
    return text


def _oa_db_configured() -> bool:
    try:
        env = load_environment()
    except ReviewError:
        return False
    return bool(env.get("SANITY_OA_DSN") or env.get("SANITY_DB_HOST"))


def _active_area_catalog(db) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    with db.connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(sql.SQL(
            "SELECT id, record_type, entity_id, revision, state, project_id, block_id, version_id, payload "
            "FROM {} WHERE record_type IN ('project', 'block', 'version') "
            "AND state = 'active' AND revision = 1 AND entity_id = id ORDER BY id"
        ).format(db.table("di_area_power_tracking_record"))).fetchall()
    projects = [r for r in rows if r["record_type"] == "project"]
    blocks = [r for r in rows if r["record_type"] == "block" and r.get("block_id") == r["id"]]
    versions = [r for r in rows if r["record_type"] == "version" and r.get("version_id") == r["id"]]
    return projects, blocks, versions


def _format_candidates(rows: list[dict[str, Any]]) -> str:
    labels = []
    for row in rows[:5]:
        p = row.get("payload") or {}
        parts = [p.get("net_ver"), p.get("revision"), p.get("layout"), p.get("source_namespace")]
        text = " / ".join(str(part) for part in parts if part)
        labels.append(text or str(row.get("workspace_key") or row.get("id")))
    suffix = ", ..." if len(rows) > 5 else ""
    return ", ".join(labels) + suffix


def _resolve_with_oa(
    db,
    host_db,
    *,
    project: str,
    block: str,
    version: str | None,
    net_ver: str | None,
    revision: str | None,
    layout: str | None,
    source_namespace: str | None,
) -> dict[str, Any]:
    area_projects, area_blocks, area_versions = _active_area_catalog(db)
    with host_db.transaction(readonly=True):
        ws_projects = host_db.execute(
            "SELECT record_id, workspace_key, payload FROM {oa} WHERE record_type='WS_PROJECT'"
        ).fetchall()
        matched_projects = [
            row for row in ws_projects
            if row["workspace_key"] == project or (row["payload"] or {}).get("name") == project
        ]
        if not matched_projects:
            raise DomainError(
                f"Project '{project}' is not registered in OA. Register it in the OA dashboard first.",
                code="project_not_found", status_code=404,
            )
        if len(matched_projects) > 1:
            raise DomainError(
                f"Multiple projects match '{project}'. Specify the exact native project ID.",
                code="catalog_ambiguous", status_code=409,
            )
        ws_project = matched_projects[0]
        native_pid = ws_project["workspace_key"]
        bound_projects = [r for r in area_projects if (r["payload"] or {}).get("sanity_project_id") == native_pid]
        if not bound_projects:
            raise DomainError(
                f"Project '{project}' exists in OA, but its Area/Power scope is not prepared yet. Prepare it in OA first.",
                code="project_not_bound", status_code=404,
            )
        if len(bound_projects) > 1:
            raise DomainError("Project has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
        area_project = bound_projects[0]

        ws_blocks = host_db.execute(
            "SELECT record_id, workspace_key, payload FROM {oa} WHERE record_type='WS_BLOCK' AND workspace_project_ref=%s",
            (ws_project["record_id"],),
        ).fetchall()
        matched_blocks = [
            row for row in ws_blocks
            if row["workspace_key"] == block or (row["payload"] or {}).get("name") == block
        ]
        if not matched_blocks:
            raise DomainError(
                f"Block '{block}' is not registered under project '{project}'. Register it in OA first.",
                code="block_not_found", status_code=404,
            )
        if len(matched_blocks) > 1:
            raise DomainError(
                f"Multiple blocks match '{block}' under project '{project}'. Specify the exact native block ID.",
                code="catalog_ambiguous", status_code=409,
            )
        ws_block = matched_blocks[0]
        native_bid = ws_block["workspace_key"]
        bound_blocks = [
            r for r in area_blocks
            if (r["payload"] or {}).get("sanity_block_id") == native_bid and r.get("project_id") == area_project["id"]
        ]
        if not bound_blocks:
            raise DomainError(
                f"Block '{block}' exists in OA, but its Area/Power scope is not prepared yet. Prepare it in OA first.",
                code="block_not_bound", status_code=404,
            )
        if len(bound_blocks) > 1:
            raise DomainError("Block has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
        area_block = bound_blocks[0]

        ws_versions = host_db.execute(
            "SELECT record_id, workspace_key, payload FROM {oa} "
            "WHERE record_type='WS_VERSION' AND workspace_project_ref=%s AND workspace_block_ref=%s",
            (ws_project["record_id"], ws_block["record_id"]),
        ).fetchall()
        matched_versions = []
        for row in ws_versions:
            payload = row["payload"] or {}
            if version is not None and row["workspace_key"] != version:
                continue
            if net_ver is not None and payload.get("net_ver") != net_ver:
                continue
            if revision is not None and payload.get("revision") != revision:
                continue
            if layout is not None and payload.get("layout") != layout:
                continue
            if source_namespace is not None and payload.get("source_namespace") != source_namespace:
                continue
            matched_versions.append(row)
        if not matched_versions:
            raise DomainError(
                "No registered version matches the specified project/block/version criteria. Register it in OA first.",
                code="version_not_found", status_code=404,
            )
        if len(matched_versions) > 1:
            raise DomainError(
                f"Matched {len(matched_versions)} versions ({_format_candidates(matched_versions)}). "
                "Narrow with --revision, --layout, --source-namespace, or --version.",
                code="catalog_ambiguous", status_code=409,
            )
        ws_version = matched_versions[0]
        native_vid = ws_version["workspace_key"]
        binding_row = host_db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
            (BINDING_PREFIX + native_vid,),
        ).fetchone()
    if binding_row is None:
        raise DomainError(
            "Version exists in OA, but its Area/Power scope is not prepared yet. Prepare it in OA first.",
            code="version_not_bound", status_code=404,
        )
    binding = binding_row["payload"] or {}
    if (
        binding.get("namespace") != "design-atlas-v1"
        or binding.get("project_id") != native_pid
        or binding.get("block_id") != native_bid
        or binding.get("version_id") != native_vid
        or binding.get("area_project_id") != str(area_project["id"])
        or binding.get("area_block_id") != str(area_block["id"])
    ):
        raise DomainError("Version has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
    try:
        area_vid = UUID(str(binding.get("area_version_id")))
    except (ValueError, TypeError):
        raise DomainError("Version has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409) from None
    area_version = next(
        (r for r in area_versions if r["id"] == area_vid and r.get("project_id") == area_project["id"]
         and r.get("block_id") in (None, area_block["id"])),
        None,
    )
    if area_version is None:
        raise DomainError("Version binding points to an inactive or missing Area version.", code="area_binding_conflict", status_code=409)

    vp = ws_version["payload"] or {}
    return {
        "found": True,
        "project_id": area_project["id"],
        "block_id": area_block["id"],
        "version_id": area_version["id"],
        "project": {"id": area_project["id"], "native_id": native_pid, "name": (ws_project["payload"] or {}).get("name")},
        "block": {"id": area_block["id"], "native_id": native_bid, "name": (ws_block["payload"] or {}).get("name")},
        "version": {
            "id": area_version["id"], "native_id": native_vid,
            "net_ver": vp.get("net_ver"), "revision": vp.get("revision"),
            "layout": vp.get("layout"), "source_namespace": vp.get("source_namespace"),
        },
    }


def _resolve_area_only(
    db,
    *,
    project: str,
    block: str,
    version: str | None,
    net_ver: str | None,
    revision: str | None,
    layout: str | None,
    source_namespace: str | None,
) -> dict[str, Any]:
    area_projects, area_blocks, area_versions = _active_area_catalog(db)
    matched_projects = [
        r for r in area_projects
        if (r["payload"] or {}).get("name") == project or (r["payload"] or {}).get("sanity_project_id") == project
    ]
    if not matched_projects:
        raise DomainError(f"Project '{project}' is not registered in the catalog.", code="project_not_found", status_code=404)
    if len(matched_projects) > 1:
        raise DomainError(f"Multiple projects match '{project}'.", code="catalog_ambiguous", status_code=409)
    area_project = matched_projects[0]

    matched_blocks = [
        r for r in area_blocks
        if r.get("project_id") == area_project["id"]
        and ((r["payload"] or {}).get("name") == block or (r["payload"] or {}).get("sanity_block_id") == block)
    ]
    if not matched_blocks:
        raise DomainError(f"Block '{block}' is not registered under project '{project}'.", code="block_not_found", status_code=404)
    if len(matched_blocks) > 1:
        raise DomainError(f"Multiple blocks match '{block}'.", code="catalog_ambiguous", status_code=409)
    area_block = matched_blocks[0]

    matched_versions = []
    for row in area_versions:
        if row.get("project_id") != area_project["id"] or row.get("block_id") not in (None, area_block["id"]):
            continue
        p = row["payload"] or {}
        if version is not None and p.get("name") != version and p.get("sanity_version_id") != version:
            continue
        if net_ver is not None and p.get("net_ver") != net_ver:
            continue
        if revision is not None and p.get("revision") != revision:
            continue
        if layout is not None and p.get("layout") != layout:
            continue
        if source_namespace is not None and p.get("source_namespace") != source_namespace:
            continue
        matched_versions.append(row)
    if not matched_versions:
        raise DomainError("No registered version matches the specified criteria.", code="version_not_found", status_code=404)
    if len(matched_versions) > 1:
        raise DomainError(
            f"Matched {len(matched_versions)} versions ({_format_candidates(matched_versions)}). "
            "Narrow with --revision, --layout, --source-namespace, or --version.",
            code="catalog_ambiguous", status_code=409,
        )
    area_version = matched_versions[0]
    vp = area_version["payload"] or {}
    return {
        "found": True,
        "project_id": area_project["id"],
        "block_id": area_block["id"],
        "version_id": area_version["id"],
        "project": {"id": area_project["id"], "native_id": (area_project["payload"] or {}).get("sanity_project_id"), "name": (area_project["payload"] or {}).get("name")},
        "block": {"id": area_block["id"], "native_id": (area_block["payload"] or {}).get("sanity_block_id"), "name": (area_block["payload"] or {}).get("name")},
        "version": {
            "id": area_version["id"], "native_id": vp.get("sanity_version_id"),
            "net_ver": vp.get("net_ver"), "revision": vp.get("revision"),
            "layout": vp.get("layout"), "source_namespace": vp.get("source_namespace"),
            "name": vp.get("name"),
        },
    }


def resolve_catalog(
    db,
    *,
    project: str,
    block: str,
    version: str | None = None,
    net_ver: str | None = None,
    revision: str | None = None,
    layout: str | None = None,
    source_namespace: str | None = None,
    host_db_factory=None,
) -> dict[str, Any]:
    """Resolve pre-registered, OA-bound catalog UUIDs strictly read-only."""
    project = _clean(project, "project", required=True, max_len=200)
    block = _clean(block, "block", required=True, max_len=200)
    version = _clean(version, "version", max_len=200)
    net_ver = _clean(net_ver, "net_ver", max_len=1000)
    revision = _clean(revision, "revision", max_len=1000)
    layout = _clean(layout, "layout", max_len=16)
    source_namespace = _clean(source_namespace, "source_namespace", max_len=1000)
    if version is None and net_ver is None:
        raise DomainError("Provide at least --net-ver or --version to resolve a catalog version.", code="invalid_request")
    if layout is not None and layout not in ALLOWED_LAYOUTS:
        raise DomainError("layout must be PRE or POST.", code="invalid_request")

    params = dict(
        project=project, block=block, version=version, net_ver=net_ver,
        revision=revision, layout=layout, source_namespace=source_namespace,
    )
    if host_db_factory is not None or _oa_db_configured():
        factory = host_db_factory or (lambda: HostDatabase("oa"))
        try:
            with factory() as host_db:
                return _resolve_with_oa(db, host_db, **params)
        except DomainError:
            raise
        except ReviewError:
            raise ConfigurationError("OA catalog database is unavailable.") from None
    return _resolve_area_only(db, **params)
