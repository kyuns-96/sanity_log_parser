"""Read-only lookup of pre-registered, OA-bound Area/Power catalog UUIDs.

Never creates, mutates, binds, or fuzzy-matches catalog records. Returns exact
project/block/version UUIDs only when a single active catalog scope exists and
passes the same Area catalog contract checked at upload time.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import sql

from common.area_power.errors import ConfigurationError, DomainError
from common.sanity_common import ReviewError
from common.sanity_config import load_environment
from common.sanity_db import Database as HostDatabase, lock_key
from common.sanity_submission import source_version
from oa.sanity_workspace_core import WorkspaceCoreStore

BINDING_PREFIX = "design-atlas-v1.binding."
VERSION_META_PREFIX = "atlas.version."
DEFAULT_SOURCE_NAMESPACE = "area-power-vwp-v1"
ALLOWED_LAYOUTS = frozenset({"PRE", "POST"})
VWP_CATALOG_ACTOR = "vwp_server"


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


def _match_oa_version_list(
    rows: list[dict[str, Any]],
    *,
    version: str | None = None,
    net_ver: str | None = None,
    revision: str | None = None,
    layout: str | None = None,
    source_namespace: str | None = None,
) -> list[dict[str, Any]]:
    matched = []
    for row in rows:
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
        matched.append(row)
    return matched


def _infer_source_namespace(host_db, ws_project_ref, ws_block_ref, ws_versions: list[dict[str, Any]]) -> str:
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
    layout: str | None,
    source_namespace: str | None,
    previous_version: str | None,
    previous_net_ver: str | None,
    previous_revision: str | None,
    previous_layout: str | None,
    previous_source_namespace: str | None,
) -> dict[str, Any]:
    has_prev_selector = any(
        val is not None for val in (previous_version, previous_net_ver, previous_revision, previous_layout, previous_source_namespace)
    )
    with host_db.transaction():
        host_db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-catalog"),))
        ws = WorkspaceCoreStore(host_db)

        ws_projects = host_db.execute(
            "SELECT record_id, workspace_key, payload FROM {oa} WHERE record_type='WS_PROJECT'"
        ).fetchall()
        matched_projects = [
            row for row in ws_projects
            if row["workspace_key"] == project or (row["payload"] or {}).get("name") == project
        ]
        if not matched_projects:
            raise DomainError(
                f"Project '{project}' is not registered in OA. Register the project in OA first.",
                code="project_not_found", status_code=404,
            )
        if len(matched_projects) > 1:
            raise DomainError(
                f"Multiple projects match '{project}'. Specify the exact native project ID.",
                code="catalog_ambiguous", status_code=409,
            )
        ws_project = matched_projects[0]
        native_pid = ws_project["workspace_key"]

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
                f"Block '{block}' is not registered under project '{project}'. Register the block in OA first.",
                code="block_not_found", status_code=404,
            )
        if len(matched_blocks) > 1:
            raise DomainError(
                f"Multiple blocks match '{block}' under project '{project}'. Specify the exact native block ID.",
                code="catalog_ambiguous", status_code=409,
            )
        ws_block = matched_blocks[0]
        native_bid = ws_block["workspace_key"]

        ws_versions = host_db.execute(
            "SELECT record_id, workspace_key, payload FROM {oa} "
            "WHERE record_type='WS_VERSION' AND workspace_project_ref=%s AND workspace_block_ref=%s",
            (ws_project["record_id"], ws_block["record_id"]),
        ).fetchall()

        # Verify project and block are already bound in Area DB before creating any OA version.
        area_projects, area_blocks, _ = _active_area_catalog(db)
        bound_projects = [r for r in area_projects if (r["payload"] or {}).get("sanity_project_id") == native_pid]
        if not bound_projects:
            raise DomainError(
                f"Project '{project}' exists in OA, but its Area/Power scope is not prepared yet. Prepare it in OA first.",
                code="project_not_bound", status_code=404,
            )
        if len(bound_projects) > 1:
            raise DomainError("Project has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
        area_project = bound_projects[0]
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

        # Resolve optional explicit previous version under the same (project, block).
        requested_prev_vid = None
        if has_prev_selector:
            if previous_version is None and previous_net_ver is None:
                raise DomainError(
                    "Specify --previous-net-ver or --previous-version when declaring previous version lineage.",
                    code="invalid_request",
                )
            prev_matches = _match_oa_version_list(
                ws_versions,
                version=previous_version, net_ver=previous_net_ver,
                revision=previous_revision, layout=previous_layout,
                source_namespace=previous_source_namespace,
            )
            if not prev_matches:
                raise DomainError(
                    "Previous version was not found under this project and block.",
                    code="previous_version_not_found", status_code=404,
                )
            if len(prev_matches) > 1:
                raise DomainError(
                    f"Matched {len(prev_matches)} previous versions ({_format_candidates(prev_matches)}). Narrow previous version selectors.",
                    code="catalog_ambiguous", status_code=409,
                )
            requested_prev_vid = prev_matches[0]["workspace_key"]

        # Check whether the requested version already exists in OA WS_VERSION.
        existing_matches = _match_oa_version_list(
            ws_versions, net_ver=net_ver, revision=revision, layout=layout, source_namespace=source_namespace,
        )
        created_version = False
        if len(existing_matches) > 1:
            raise DomainError(
                f"Matched {len(existing_matches)} versions ({_format_candidates(existing_matches)}). "
                "Specify --layout or --source-namespace explicitly.",
                code="catalog_ambiguous", status_code=409,
            )
        if len(existing_matches) == 1:
            ws_version = existing_matches[0]
            native_vid = ws_version["workspace_key"]
            vp = ws_version["payload"] or {}
            actual_layout = vp.get("layout")
        else:
            if layout is None:
                raise DomainError(
                    "layout (PRE or POST) is required to register a new version.",
                    code="invalid_request",
                )
            actual_ns = source_namespace or _infer_source_namespace(host_db, ws_project["record_id"], ws_block["record_id"], ws_versions)
            native_vid = source_version(
                {"source_namespace": actual_ns, "project_key": native_pid, "block_key": native_bid},
                {"net_ver": net_ver, "revision": revision, "layout": layout},
            )
            vp = {
                "id": native_vid, "project_id": native_pid, "block_id": native_bid,
                "net_ver": net_ver, "revision": revision, "layout": layout,
                "ecos": [None] if layout == "PRE" else [],
                "source_pre_id": None, "source_namespace": actual_ns,
            }
            old_by_id = host_db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_VERSION' AND workspace_key=%s", (native_vid,)
            ).fetchone()
            if old_by_id:
                if old_by_id["payload"] != vp:
                    raise DomainError("Version identity conflicts with an existing record.", code="area_binding_conflict", status_code=409)
            else:
                ws.put("WS_VERSION", native_vid, vp, project=native_pid, block=native_bid)
                created_version = True
            actual_layout = layout

        if requested_prev_vid == native_vid:
            raise DomainError("A version cannot declare itself as its previous version.", code="invalid_request")

        # Ensure atlas.version.<vid> lineage metadata (required by OA AtlasCatalog.project_links).
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
            ws.put(
                "WS_CONFIG",
                VERSION_META_PREFIX + native_vid,
                {"previous_version_id": previous_native_vid, "declared_ecos": [None] if actual_layout == "PRE" else []},
            )

        # Resolve previous version's Area UUID if lineage is declared.
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
            if (prev_binding.get("namespace") != "design-atlas-v1"
                    or prev_binding.get("area_project_id") != str(area_project["id"])
                    or prev_binding.get("area_block_id") != str(area_block["id"])):
                raise DomainError("Previous version binding is inconsistent.", code="area_binding_conflict", status_code=409)
            previous_area_vid = prev_binding.get("area_version_id")

        binding_row = host_db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
            (BINDING_PREFIX + native_vid,),
        ).fetchone()
        existing_binding = (binding_row["payload"] or {}) if binding_row else None

        # Bind/create Area version row under the same advisory lock used by oa/atlas_catalog.py.
        created_area_binding = False
        with db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
            rows = conn.execute(sql.SQL(
                "SELECT id, record_type, entity_id, revision, state, project_id, block_id, version_id, payload "
                "FROM {} WHERE record_type IN ('project', 'block', 'version') "
                "AND state = 'active' AND revision = 1 AND entity_id = id ORDER BY id"
            ).format(db.table("di_area_power_tracking_record"))).fetchall()
            area_versions = [r for r in rows if r["record_type"] == "version" and r.get("version_id") == r["id"]]
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
                area_version = db.insert_record(
                    conn, "version",
                    {"name": native_vid, "sanity_version_id": native_vid,
                     "previous_version_id": str(previous_area_vid) if previous_area_vid else None},
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

        expected_binding = {
            "namespace": "design-atlas-v1",
            "project_id": native_pid,
            "block_id": native_bid,
            "version_id": native_vid,
            "area_project_id": str(area_project["id"]),
            "area_block_id": str(area_block["id"]),
            "area_version_id": str(area_version["id"]),
        }
        if existing_binding is not None:
            if existing_binding != expected_binding:
                raise DomainError("Version has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
        else:
            ws.put("WS_CONFIG", BINDING_PREFIX + native_vid, expected_binding)
            created_area_binding = True

    return {
        "found": True,
        "created": created_version,
        "bound": created_area_binding,
        "project_id": area_project["id"],
        "block_id": area_block["id"],
        "version_id": area_version["id"],
        "project": {"id": area_project["id"], "native_id": native_pid, "name": (ws_project["payload"] or {}).get("name")},
        "block": {"id": area_block["id"], "native_id": native_bid, "name": (ws_block["payload"] or {}).get("name")},
        "version": {
            "id": area_version["id"], "native_id": native_vid,
            "net_ver": vp.get("net_ver"), "revision": vp.get("revision"),
            "layout": vp.get("layout"), "source_namespace": vp.get("source_namespace"),
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
    layout: str | None,
    source_namespace: str | None,
    previous_version: str | None,
    previous_net_ver: str | None,
    previous_revision: str | None,
    previous_layout: str | None,
    previous_source_namespace: str | None,
) -> dict[str, Any]:
    has_prev_selector = any(
        val is not None for val in (previous_version, previous_net_ver, previous_revision, previous_layout, previous_source_namespace)
    )
    with db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
        rows = conn.execute(sql.SQL(
            "SELECT id, record_type, entity_id, revision, state, project_id, block_id, version_id, payload "
            "FROM {} WHERE record_type IN ('project', 'block', 'version') "
            "AND state = 'active' AND revision = 1 AND entity_id = id ORDER BY id"
        ).format(db.table("di_area_power_tracking_record"))).fetchall()
        area_projects = [r for r in rows if r["record_type"] == "project"]
        area_blocks = [r for r in rows if r["record_type"] == "block" and r.get("block_id") == r["id"]]
        area_versions = [r for r in rows if r["record_type"] == "version" and r.get("version_id") == r["id"]]

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

        scoped_versions = [
            r for r in area_versions
            if r.get("project_id") == area_project["id"] and r.get("block_id") in (None, area_block["id"])
        ]
        prev_area_id = None
        if has_prev_selector:
            prev_matches = [
                r for r in scoped_versions
                if (previous_version is None or (r["payload"] or {}).get("name") == previous_version)
                and (previous_net_ver is None or (r["payload"] or {}).get("net_ver") == previous_net_ver)
                and (previous_revision is None or (r["payload"] or {}).get("revision") == previous_revision)
                and (previous_layout is None or (r["payload"] or {}).get("layout") == previous_layout)
                and (previous_source_namespace is None or (r["payload"] or {}).get("source_namespace") == previous_source_namespace)
            ]
            if not prev_matches:
                raise DomainError("Previous version was not found.", code="previous_version_not_found", status_code=404)
            if len(prev_matches) > 1:
                raise DomainError("Previous version selector matched multiple versions.", code="catalog_ambiguous", status_code=409)
            prev_area_id = str(prev_matches[0]["id"])

        existing = [
            r for r in scoped_versions
            if (r["payload"] or {}).get("net_ver") == net_ver
            and (r["payload"] or {}).get("revision") == revision
            and (layout is None or (r["payload"] or {}).get("layout") == layout)
            and (source_namespace is None or (r["payload"] or {}).get("source_namespace") == source_namespace)
        ]
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
                    "name": f"{net_ver} · {revision} · {layout}",
                    "net_ver": net_ver, "revision": revision, "layout": layout,
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
        "project": {"id": area_project["id"], "native_id": (area_project["payload"] or {}).get("sanity_project_id"), "name": (area_project["payload"] or {}).get("name")},
        "block": {"id": area_block["id"], "native_id": (area_block["payload"] or {}).get("sanity_block_id"), "name": (area_block["payload"] or {}).get("name")},
        "version": {
            "id": area_version["id"], "native_id": vp.get("sanity_version_id"),
            "net_ver": vp.get("net_ver"), "revision": vp.get("revision"),
            "layout": vp.get("layout"), "source_namespace": vp.get("source_namespace"),
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
    layout: str | None = None,
    source_namespace: str | None = None,
    previous_version: str | None = None,
    previous_net_ver: str | None = None,
    previous_revision: str | None = None,
    previous_layout: str | None = None,
    previous_source_namespace: str | None = None,
    host_db_factory=None,
) -> dict[str, Any]:
    """Register (or reuse) a version under an already OA-registered & Area-bound project/block."""
    project = _clean(project, "project", required=True, max_len=200)
    block = _clean(block, "block", required=True, max_len=200)
    net_ver = _clean(net_ver, "net_ver", required=True, max_len=1000)
    revision = _clean(revision, "revision", required=True, max_len=1000)
    layout = _clean(layout, "layout", max_len=16)
    source_namespace = _clean(source_namespace, "source_namespace", max_len=1000)
    previous_version = _clean(previous_version, "previous_version", max_len=200)
    previous_net_ver = _clean(previous_net_ver, "previous_net_ver", max_len=1000)
    previous_revision = _clean(previous_revision, "previous_revision", max_len=1000)
    previous_layout = _clean(previous_layout, "previous_layout", max_len=16)
    previous_source_namespace = _clean(previous_source_namespace, "previous_source_namespace", max_len=1000)
    if layout is not None and layout not in ALLOWED_LAYOUTS:
        raise DomainError("layout must be PRE or POST.", code="invalid_request")
    if previous_layout is not None and previous_layout not in ALLOWED_LAYOUTS:
        raise DomainError("previous_layout must be PRE or POST.", code="invalid_request")

    params = dict(
        project=project, block=block, net_ver=net_ver, revision=revision,
        layout=layout, source_namespace=source_namespace,
        previous_version=previous_version, previous_net_ver=previous_net_ver,
        previous_revision=previous_revision, previous_layout=previous_layout,
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
