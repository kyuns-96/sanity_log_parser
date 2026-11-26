"""Catalog UUID lookup and pre-bound version registration entrypoints for VWP Area/Power."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from common.area_power.atlas_bindings import (
    ALLOWED_LAYOUTS,
    BINDING_NAMESPACE,
    BINDING_PREFIX,
    DEFAULT_SOURCE_NAMESPACE,
    VERSION_META_PREFIX,
    active_area_catalog_rows,
    clean_catalog_field,
    format_version_candidates,
    match_area_version_list,
    match_oa_version_list,
    resolve_area_only_project_and_block,
    resolve_bound_area_project_and_block,
    resolve_oa_project_and_block,
)
from common.area_power.errors import ConfigurationError, DomainError
from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_db import Database as HostDatabase
from .catalog_register import VWP_CATALOG_ACTOR, register_version


def _oa_db_configured() -> bool:
    try:
        env = load_environment()
    except ReviewError:
        return False
    return bool(env.get("SANITY_OA_DSN") or env.get("SANITY_DB_HOST"))


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
    eco_num: str | None,
    source_namespace: str | None,
) -> dict[str, Any]:
    area_projects, area_blocks, area_versions = active_area_catalog_rows(db)
    with host_db.transaction(readonly=True):
        ws_project, ws_block = resolve_oa_project_and_block(host_db, project, block)
        native_pid, native_bid = ws_project["workspace_key"], ws_block["workspace_key"]
        area_project, area_block = resolve_bound_area_project_and_block(
            area_projects, area_blocks,
            native_pid=native_pid, native_bid=native_bid,
            project_label=project, block_label=block,
        )

        ws_versions = host_db.execute(
            "SELECT record_id, workspace_key, payload FROM {oa} "
            "WHERE record_type='WS_VERSION' AND workspace_project_ref=%s AND workspace_block_ref=%s",
            (ws_project["record_id"], ws_block["record_id"]),
        ).fetchall()
        matched_versions = match_oa_version_list(
            ws_versions,
            version=version, net_ver=net_ver, revision=revision,
            layout=layout, eco_num=eco_num, source_namespace=source_namespace,
        )
        if not matched_versions:
            raise DomainError(
                "No registered version matches the specified project/block/version criteria. Register it in OA first.",
                code="version_not_found", status_code=404,
            )
        if len(matched_versions) > 1:
            raise DomainError(
                f"Matched {len(matched_versions)} versions ({format_version_candidates(matched_versions)}). "
                "Narrow with --revision, --layout, --eco-num, --source-namespace, or --version.",
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
        binding.get("namespace") != BINDING_NAMESPACE
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
        "eco_num": vp.get("eco_num"),
        "project": {"id": area_project["id"], "native_id": native_pid, "name": (ws_project["payload"] or {}).get("name")},
        "block": {"id": area_block["id"], "native_id": native_bid, "name": (ws_block["payload"] or {}).get("name")},
        "version": {
            "id": area_version["id"], "native_id": native_vid,
            "net_ver": vp.get("net_ver"), "revision": vp.get("revision"),
            "layout": vp.get("layout"), "eco_num": vp.get("eco_num"),
            "source_namespace": vp.get("source_namespace"),
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
    eco_num: str | None,
    source_namespace: str | None,
) -> dict[str, Any]:
    area_projects, area_blocks, area_versions = active_area_catalog_rows(db)
    area_project, area_block = resolve_area_only_project_and_block(
        area_projects, area_blocks, project=project, block=block
    )
    matched_versions = match_area_version_list(
        area_versions,
        project_id=area_project["id"],
        block_id=area_block["id"],
        version=version,
        net_ver=net_ver,
        revision=revision,
        layout=layout,
        eco_num=eco_num,
        source_namespace=source_namespace,
    )
    if not matched_versions:
        raise DomainError("No registered version matches the specified criteria.", code="version_not_found", status_code=404)
    if len(matched_versions) > 1:
        raise DomainError(
            f"Matched {len(matched_versions)} versions ({format_version_candidates(matched_versions)}). "
            "Narrow with --revision, --layout, --eco-num, --source-namespace, or --version.",
            code="catalog_ambiguous", status_code=409,
        )
    area_version = matched_versions[0]
    vp = area_version["payload"] or {}
    return {
        "found": True,
        "project_id": area_project["id"],
        "block_id": area_block["id"],
        "version_id": area_version["id"],
        "eco_num": vp.get("eco_num"),
        "project": {
            "id": area_project["id"],
            "native_id": (area_project["payload"] or {}).get("sanity_project_id"),
            "name": (area_project["payload"] or {}).get("name"),
        },
        "block": {
            "id": area_block["id"],
            "native_id": (area_block["payload"] or {}).get("sanity_block_id"),
            "name": (area_block["payload"] or {}).get("name"),
        },
        "version": {
            "id": area_version["id"], "native_id": vp.get("sanity_version_id"),
            "net_ver": vp.get("net_ver"), "revision": vp.get("revision"),
            "layout": vp.get("layout"), "eco_num": vp.get("eco_num"),
            "source_namespace": vp.get("source_namespace"),
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
    eco_num: str | None = None,
    source_namespace: str | None = None,
    host_db_factory=None,
) -> dict[str, Any]:
    """Resolve pre-registered, OA-bound catalog UUIDs strictly read-only."""
    project = clean_catalog_field(project, "project", required=True, max_len=200)
    block = clean_catalog_field(block, "block", required=True, max_len=200)
    version = clean_catalog_field(version, "version", max_len=200)
    net_ver = clean_catalog_field(net_ver, "net_ver", max_len=1000)
    revision = clean_catalog_field(revision, "revision", max_len=1000)
    layout = clean_catalog_field(layout, "layout", max_len=16)
    eco_num = clean_catalog_field(eco_num, "eco_num", max_len=100)
    source_namespace = clean_catalog_field(source_namespace, "source_namespace", max_len=1000)
    if version is None and net_ver is None:
        raise DomainError("Provide at least --net-ver or --version to resolve a catalog version.", code="invalid_request")
    if layout is not None and layout not in ALLOWED_LAYOUTS:
        raise DomainError("layout must be PRE or POST.", code="invalid_request")

    params = dict(
        project=project, block=block, version=version, net_ver=net_ver,
        revision=revision, layout=layout, eco_num=eco_num, source_namespace=source_namespace,
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


__all__ = [
    "ALLOWED_LAYOUTS", "BINDING_PREFIX", "DEFAULT_SOURCE_NAMESPACE", "VERSION_META_PREFIX",
    "VWP_CATALOG_ACTOR", "register_version", "resolve_catalog",
]
