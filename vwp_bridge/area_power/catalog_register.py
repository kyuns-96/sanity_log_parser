"""VWP version registration and binding under pre-registered, Area-bound project/block scopes."""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from common.area_power.atlas_bindings import (
    ALLOWED_LAYOUTS,
    BINDING_NAMESPACE,
    BINDING_PREFIX,
    DEFAULT_SOURCE_NAMESPACE,
    VERSION_META_PREFIX,
    active_area_catalog_rows,
    build_version_binding_payload,
    clean_catalog_field,
    format_version_candidates,
    match_area_version_list,
    match_oa_version_list,
    put_oa_workspace_row,
    resolve_area_only_project_and_block,
    resolve_bound_area_project_and_block,
    resolve_oa_project_and_block,
)
from common.area_power.errors import ConfigurationError, DomainError
from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_db import Database as HostDatabase, lock_key
from common.sanity_submission import source_version

VWP_CATALOG_ACTOR = "vwp_server"


def _oa_db_configured() -> bool:
    try:
        env = load_environment()
    except ReviewError:
        return False
    return bool(env.get("SANITY_OA_DSN") or env.get("SANITY_DB_HOST"))


def _infer_source_namespace(host_db, ws_project_ref, ws_versions: list[dict[str, Any]]) -> str:
    block_ns = {
        (row["payload"] or {}).get("source_namespace")
        for row in ws_versions
        if (row["payload"] or {}).get("source_namespace")
    }
    if len(block_ns) == 1:
        return next(iter(block_ns))
    if len(block_ns) > 1:
        listed = ", ".join(sorted(block_ns)[:5])
        raise DomainError(
            f"Multiple source_namespace values exist in this block ({listed}). Specify --source-namespace explicitly.",
            code="catalog_ambiguous", status_code=409,
        )
    project_rows = host_db.execute(
        "SELECT payload FROM {oa} WHERE record_type='WS_VERSION' AND workspace_project_ref=%s",
        (ws_project_ref,),
    ).fetchall()
    proj_ns = {
        (row["payload"] or {}).get("source_namespace")
        for row in project_rows
        if (row["payload"] or {}).get("source_namespace")
    }
    if len(proj_ns) == 1:
        return next(iter(proj_ns))
    if len(proj_ns) > 1:
        listed = ", ".join(sorted(proj_ns)[:5])
        raise DomainError(
            f"Multiple source_namespace values exist in this project ({listed}). Specify --source-namespace explicitly.",
            code="catalog_ambiguous", status_code=409,
        )
    return DEFAULT_SOURCE_NAMESPACE


def _register_version_with_oa(
    db,
    host_db,
    *,
    project: str,
    block: str,
    net_ver: str,
    revision: str,
    eco_num: str,
    layout: str | None,
    source_namespace: str | None,
    previous_version: str | None,
    previous_net_ver: str | None,
    previous_revision: str | None,
    previous_layout: str | None,
    previous_eco_num: str | None,
    previous_source_namespace: str | None,
) -> dict[str, Any]:
    has_prev_selector = any(
        val is not None for val in (
            previous_version, previous_net_ver, previous_revision,
            previous_layout, previous_eco_num, previous_source_namespace,
        )
    )
    with host_db.transaction():
        host_db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-catalog"),))
        ws_project, ws_block = resolve_oa_project_and_block(host_db, project, block)
        native_pid, native_bid = ws_project["workspace_key"], ws_block["workspace_key"]

        ws_versions = host_db.execute(
            "SELECT record_id, workspace_key, payload FROM {oa} "
            "WHERE record_type='WS_VERSION' AND workspace_project_ref=%s AND workspace_block_ref=%s",
            (ws_project["record_id"], ws_block["record_id"]),
        ).fetchall()

        area_projects, area_blocks, _ = active_area_catalog_rows(db)
        area_project, area_block = resolve_bound_area_project_and_block(
            area_projects, area_blocks,
            native_pid=native_pid, native_bid=native_bid,
            project_label=project, block_label=block,
        )

        requested_prev_vid = None
        if has_prev_selector:
            if previous_version is None and previous_net_ver is None:
                raise DomainError(
                    "Specify --previous-net-ver or --previous-version when declaring previous version lineage.",
                    code="invalid_request",
                )
            prev_matches = match_oa_version_list(
                ws_versions,
                version=previous_version, net_ver=previous_net_ver,
                revision=previous_revision, layout=previous_layout,
                eco_num=previous_eco_num, source_namespace=previous_source_namespace,
            )
            if not prev_matches:
                raise DomainError(
                    "Previous version was not found under this project and block.",
                    code="previous_version_not_found", status_code=404,
                )
            if len(prev_matches) > 1:
                raise DomainError(
                    f"Matched {len(prev_matches)} previous versions ({format_version_candidates(prev_matches)}). Narrow previous version selectors.",
                    code="catalog_ambiguous", status_code=409,
                )
            requested_prev_vid = prev_matches[0]["workspace_key"]

        existing_matches = match_oa_version_list(
            ws_versions, net_ver=net_ver, revision=revision, layout=layout,
            eco_num=eco_num, source_namespace=source_namespace,
        )
        created_version = False
        if len(existing_matches) > 1:
            raise DomainError(
                f"Matched {len(existing_matches)} versions ({format_version_candidates(existing_matches)}). "
                "Specify --layout or --source-namespace explicitly.",
                code="catalog_ambiguous", status_code=409,
            )
        if len(existing_matches) == 1:
            ws_version = existing_matches[0]
            native_vid = ws_version["workspace_key"]
            vp = ws_version["payload"] or {}
        else:
            if layout is None:
                raise DomainError("layout (PRE or POST) is required to register a new version.", code="invalid_request")
            actual_ns = source_namespace or _infer_source_namespace(host_db, ws_project["record_id"], ws_versions)
            native_vid = source_version(
                {"source_namespace": actual_ns, "project_key": native_pid, "block_key": native_bid},
                {"net_ver": net_ver, "revision": revision, "layout": layout, "eco_num": eco_num},
            )
            vp = {
                "id": native_vid, "project_id": native_pid, "block_id": native_bid,
                "net_ver": net_ver, "revision": revision, "layout": layout,
                "eco_num": eco_num, "ecos": [],
                "source_pre_id": None, "source_namespace": actual_ns,
            }
            old_by_id = host_db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_VERSION' AND workspace_key=%s", (native_vid,)
            ).fetchone()
            if old_by_id:
                if old_by_id["payload"] != vp:
                    raise DomainError("Version identity conflicts with an existing record.", code="area_binding_conflict", status_code=409)
            else:
                put_oa_workspace_row(host_db, "WS_VERSION", native_vid, vp, project=native_pid, block=native_bid)
                created_version = True

        if requested_prev_vid == native_vid:
            raise DomainError("A version cannot declare itself as its previous version.", code="invalid_request")

        meta_row = host_db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
            (VERSION_META_PREFIX + native_vid,),
        ).fetchone()
        if meta_row is not None:
            previous_native_vid = (meta_row["payload"] or {}).get("previous_version_id")
            if has_prev_selector and previous_native_vid != requested_prev_vid:
                raise DomainError(
                    "Version already has a different previous_version_id declared.",
                    code="area_binding_conflict", status_code=409,
                )
        else:
            previous_native_vid = requested_prev_vid
            put_oa_workspace_row(
                host_db,
                "WS_CONFIG",
                VERSION_META_PREFIX + native_vid,
                {"previous_version_id": previous_native_vid, "declared_ecos": [eco_num]},
            )

        previous_area_vid = None
        if previous_native_vid is not None:
            prev_binding_row = host_db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                (BINDING_PREFIX + previous_native_vid,),
            ).fetchone()
            if prev_binding_row is None:
                raise DomainError(
                    "Previous version exists in OA, but its Area/Power scope is not prepared yet.",
                    code="area_previous_not_prepared", status_code=409,
                )
            prev_binding = prev_binding_row["payload"] or {}
            if (prev_binding.get("namespace") != BINDING_NAMESPACE
                    or prev_binding.get("area_project_id") != str(area_project["id"])
                    or prev_binding.get("area_block_id") != str(area_block["id"])):
                raise DomainError("Previous version binding is inconsistent.", code="area_binding_conflict", status_code=409)
            previous_area_vid = prev_binding.get("area_version_id")

        binding_row = host_db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
            (BINDING_PREFIX + native_vid,),
        ).fetchone()
        existing_binding = (binding_row["payload"] or {}) if binding_row else None

        created_area_binding = False
        with db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
            _, _, area_versions = active_area_catalog_rows(db, conn)
            direct = [r for r in area_versions if (r["payload"] or {}).get("sanity_version_id") == native_vid]
            if len(direct) > 1:
                raise DomainError("Version has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
            area_version = None
            if existing_binding is not None:
                try:
                    bound_vid = UUID(str(existing_binding.get("area_version_id")))
                except (ValueError, TypeError):
                    raise DomainError("Version has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409) from None
                area_version = next((r for r in area_versions if r["id"] == bound_vid), None)
                if area_version is None or (direct and direct[0]["id"] != area_version["id"]):
                    raise DomainError("Version has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
            elif direct:
                area_version = direct[0]

            if area_version is None:
                identifier = uuid4()
                area_ver_payload = {
                    "name": native_vid,
                    "sanity_version_id": native_vid,
                    "net_ver": vp.get("net_ver"),
                    "revision": vp.get("revision"),
                    "layout": vp.get("layout"),
                    "previous_version_id": str(previous_area_vid) if previous_area_vid else None,
                }
                if vp.get("eco_num") is not None:
                    area_ver_payload["eco_num"] = vp["eco_num"]
                area_version = db.insert_record(
                    conn, "version", area_ver_payload,
                    VWP_CATALOG_ACTOR,
                    id=identifier, entity_id=identifier, version_id=identifier,
                    project_id=area_project["id"], parent_id=area_project["id"],
                )
                created_area_binding = True

            if area_version["project_id"] != area_project["id"] or area_version.get("block_id") not in (None, area_block["id"]):
                raise DomainError("Version has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
            actual_prev = area_version["payload"].get("previous_version_id")
            if (str(actual_prev) if actual_prev else None) != (str(previous_area_vid) if previous_area_vid else None):
                raise DomainError("Version lineage conflicts with existing Area version.", code="area_binding_conflict", status_code=409)

        expected_binding = build_version_binding_payload(
            native_pid, native_bid, native_vid, area_project["id"], area_block["id"], area_version["id"]
        )
        if existing_binding is not None:
            if existing_binding != expected_binding:
                raise DomainError("Version has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
        else:
            put_oa_workspace_row(host_db, "WS_CONFIG", BINDING_PREFIX + native_vid, expected_binding)
            created_area_binding = True

    return {
        "found": True,
        "created": created_version,
        "bound": created_area_binding,
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
            "previous_version_id": previous_area_vid,
        },
    }


def _register_version_area_only(
    db,
    *,
    project: str,
    block: str,
    net_ver: str,
    revision: str,
    eco_num: str,
    layout: str | None,
    source_namespace: str | None,
    previous_version: str | None,
    previous_net_ver: str | None,
    previous_revision: str | None,
    previous_layout: str | None,
    previous_eco_num: str | None,
    previous_source_namespace: str | None,
) -> dict[str, Any]:
    has_prev_selector = any(
        val is not None for val in (
            previous_version, previous_net_ver, previous_revision,
            previous_layout, previous_eco_num, previous_source_namespace,
        )
    )
    with db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
        area_projects, area_blocks, area_versions = active_area_catalog_rows(db, conn)
        area_project, area_block = resolve_area_only_project_and_block(
            area_projects, area_blocks, project=project, block=block
        )

        prev_area_id = None
        if has_prev_selector:
            prev_matches = match_area_version_list(
                area_versions,
                project_id=area_project["id"],
                block_id=area_block["id"],
                version=previous_version,
                net_ver=previous_net_ver,
                revision=previous_revision,
                layout=previous_layout,
                eco_num=previous_eco_num,
                source_namespace=previous_source_namespace,
                allow_sanity_version_id=False,
            )
            if not prev_matches:
                raise DomainError("Previous version was not found.", code="previous_version_not_found", status_code=404)
            if len(prev_matches) > 1:
                raise DomainError("Previous version selector matched multiple versions.", code="catalog_ambiguous", status_code=409)
            prev_area_id = str(prev_matches[0]["id"])

        existing = match_area_version_list(
            area_versions,
            project_id=area_project["id"],
            block_id=area_block["id"],
            net_ver=net_ver,
            revision=revision,
            layout=layout,
            eco_num=eco_num,
            source_namespace=source_namespace,
        )
        if len(existing) > 1:
            raise DomainError("Multiple versions match the requested criteria.", code="catalog_ambiguous", status_code=409)
        if existing:
            area_version = existing[0]
            created = False
        else:
            if layout is None:
                raise DomainError("layout (PRE or POST) is required to register a new version.", code="invalid_request")
            identifier = uuid4()
            area_version = db.insert_record(
                conn, "version",
                {
                    "name": f"{net_ver} · {revision} · {layout} · {eco_num}",
                    "net_ver": net_ver, "revision": revision, "layout": layout,
                    "eco_num": eco_num,
                    "source_namespace": source_namespace or DEFAULT_SOURCE_NAMESPACE,
                    "previous_version_id": prev_area_id,
                },
                VWP_CATALOG_ACTOR,
                id=identifier, entity_id=identifier, version_id=identifier,
                project_id=area_project["id"], block_id=area_block["id"], parent_id=area_project["id"],
            )
            created = True
    vp = area_version["payload"] or {}
    return {
        "found": True,
        "created": created,
        "bound": created,
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
            "previous_version_id": vp.get("previous_version_id"),
        },
    }


def register_version(
    db,
    *,
    project: str,
    block: str,
    net_ver: str,
    revision: str,
    eco_num: str,
    layout: str | None = None,
    source_namespace: str | None = None,
    previous_version: str | None = None,
    previous_net_ver: str | None = None,
    previous_revision: str | None = None,
    previous_layout: str | None = None,
    previous_eco_num: str | None = None,
    previous_source_namespace: str | None = None,
    host_db_factory=None,
) -> dict[str, Any]:
    """Register (or reuse) a version under an already OA-registered & Area-bound project/block."""
    project = clean_catalog_field(project, "project", required=True, max_len=200)
    block = clean_catalog_field(block, "block", required=True, max_len=200)
    net_ver = clean_catalog_field(net_ver, "net_ver", required=True, max_len=1000)
    revision = clean_catalog_field(revision, "revision", required=True, max_len=1000)
    eco_num = clean_catalog_field(eco_num, "eco_num", required=True, max_len=100)
    layout = clean_catalog_field(layout, "layout", max_len=16)
    source_namespace = clean_catalog_field(source_namespace, "source_namespace", max_len=1000)
    previous_version = clean_catalog_field(previous_version, "previous_version", max_len=200)
    previous_net_ver = clean_catalog_field(previous_net_ver, "previous_net_ver", max_len=1000)
    previous_revision = clean_catalog_field(previous_revision, "previous_revision", max_len=1000)
    previous_layout = clean_catalog_field(previous_layout, "previous_layout", max_len=16)
    previous_eco_num = clean_catalog_field(previous_eco_num, "previous_eco_num", max_len=100)
    previous_source_namespace = clean_catalog_field(previous_source_namespace, "previous_source_namespace", max_len=1000)
    if layout is not None and layout not in ALLOWED_LAYOUTS:
        raise DomainError("layout must be PRE or POST.", code="invalid_request")
    if previous_layout is not None and previous_layout not in ALLOWED_LAYOUTS:
        raise DomainError("previous_layout must be PRE or POST.", code="invalid_request")

    params = dict(
        project=project, block=block, net_ver=net_ver, revision=revision,
        eco_num=eco_num, layout=layout, source_namespace=source_namespace,
        previous_version=previous_version, previous_net_ver=previous_net_ver,
        previous_revision=previous_revision, previous_layout=previous_layout,
        previous_eco_num=previous_eco_num,
        previous_source_namespace=previous_source_namespace,
    )
    if host_db_factory is not None or _oa_db_configured():
        factory = host_db_factory or (lambda: HostDatabase("oa"))
        try:
            with factory() as host_db:
                return _register_version_with_oa(db, host_db, **params)
        except DomainError:
            raise
        except ReviewError:
            raise ConfigurationError("OA catalog database is unavailable.") from None
    return _register_version_area_only(db, **params)
