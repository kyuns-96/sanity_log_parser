"""Shared Atlas <-> Area/Power catalog binding constants, matchers, and version resolvers.

Neutral module with no imports from ``oa.*`` or ``vwp_bridge.*`` so both server roles
can validate and resolve exact catalog bindings without cross-server dependencies.
"""
from __future__ import annotations

from typing import Any, Callable
from uuid import UUID, uuid4

from psycopg import sql

from common.area_power.analysis.groups import version_chain
from common.area_power.errors import DomainError
from common.sanity_db import Database as HostDatabase, json_value, stable_uuid

BINDING_NAMESPACE = "design-atlas-v1"
BINDING_PREFIX = "design-atlas-v1.binding."
VERSION_META_PREFIX = "atlas.version."
DEFAULT_SOURCE_NAMESPACE = "area-power-vwp-v1"
ALLOWED_LAYOUTS = frozenset({"PRE", "POST"})


def oa_workspace_record_id(kind: str, key: str):
    return stable_uuid("workspace-v1", kind, key)


def put_oa_workspace_row(
    host_db,
    kind: str,
    key: str,
    payload: dict[str, Any],
    *,
    project: str | None = None,
    block: str | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    """Insert an immutable workspace row into ``sanity_oa_data`` using the canonical WS contract."""
    project_ref = oa_workspace_record_id("WS_PROJECT", project) if project else None
    block_ref = oa_workspace_record_id("WS_BLOCK", block) if block else None
    scope = payload.get("project_id") if kind == "WS_PROJECT" else project
    values = (
        oa_workspace_record_id(kind, key),
        kind,
        key,
        scope,
        json_value(payload),
        json_value(state or {}),
        project_ref,
        block_ref,
    )
    host_db.execute(
        "INSERT INTO {oa} (record_id,record_type,workspace_key,project,payload,state,"
        "workspace_project_ref,workspace_block_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        values,
    )


def clean_catalog_field(
    value: str | None,
    field: str,
    *,
    required: bool = False,
    max_len: int = 1000,
) -> str | None:
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


def active_area_catalog_rows(db, conn=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    query = sql.SQL(
        "SELECT id, record_type, entity_id, revision, state, project_id, block_id, version_id, payload "
        "FROM {} WHERE record_type IN ('project', 'block', 'version') "
        "AND state = 'active' AND revision = 1 AND entity_id = id ORDER BY id"
    ).format(db.table("di_area_power_tracking_record"))
    if conn is None:
        with db.connect() as local_conn:
            local_conn.execute("SET TRANSACTION READ ONLY")
            rows = local_conn.execute(query).fetchall()
    else:
        rows = conn.execute(query).fetchall()
    projects = [r for r in rows if r["record_type"] == "project"]
    blocks = [r for r in rows if r["record_type"] == "block" and r.get("block_id") == r["id"]]
    versions = [r for r in rows if r["record_type"] == "version" and r.get("version_id") == r["id"]]
    return projects, blocks, versions


def format_version_candidates(rows: list[dict[str, Any]]) -> str:
    labels = []
    for row in rows[:5]:
        p = row.get("payload") or {}
        parts = [p.get("net_ver"), p.get("revision"), p.get("layout"), p.get("eco_num"), p.get("source_namespace")]
        text = " / ".join(str(part) for part in parts if part)
        labels.append(text or str(row.get("workspace_key") or row.get("id")))
    suffix = ", ..." if len(rows) > 5 else ""
    return ", ".join(labels) + suffix


def match_oa_version_list(
    rows: list[dict[str, Any]],
    *,
    version: str | None = None,
    net_ver: str | None = None,
    revision: str | None = None,
    layout: str | None = None,
    eco_num: str | None = None,
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
        if eco_num is not None and payload.get("eco_num") != eco_num:
            continue
        if source_namespace is not None and payload.get("source_namespace") != source_namespace:
            continue
        matched.append(row)
    return matched


def match_area_version_list(
    rows: list[dict[str, Any]],
    *,
    project_id=None,
    block_id=None,
    version: str | None = None,
    net_ver: str | None = None,
    revision: str | None = None,
    layout: str | None = None,
    eco_num: str | None = None,
    source_namespace: str | None = None,
    allow_sanity_version_id: bool = True,
) -> list[dict[str, Any]]:
    matched = []
    for row in rows:
        if project_id is not None and row.get("project_id") != project_id:
            continue
        if block_id is not None and row.get("block_id") not in (None, block_id):
            continue
        p = row["payload"] or {}
        if version is not None:
            if p.get("name") != version and (not allow_sanity_version_id or p.get("sanity_version_id") != version):
                continue
        if net_ver is not None and p.get("net_ver") != net_ver:
            continue
        if revision is not None and p.get("revision") != revision:
            continue
        if layout is not None and p.get("layout") != layout:
            continue
        if eco_num is not None and p.get("eco_num") != eco_num:
            continue
        if source_namespace is not None and p.get("source_namespace") != source_namespace:
            continue
        matched.append(row)
    return matched


def resolve_oa_project_and_block(host_db, project: str, block: str) -> tuple[dict[str, Any], dict[str, Any]]:
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
    return ws_project, matched_blocks[0]


def resolve_bound_area_project_and_block(
    area_projects: list[dict[str, Any]],
    area_blocks: list[dict[str, Any]],
    *,
    native_pid: str,
    native_bid: str,
    project_label: str,
    block_label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    bound_projects = [r for r in area_projects if (r["payload"] or {}).get("sanity_project_id") == native_pid]
    if not bound_projects:
        raise DomainError(
            f"Project '{project_label}' exists in OA, but its Area/Power scope is not prepared yet. Prepare it in OA first.",
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
            f"Block '{block_label}' exists in OA, but its Area/Power scope is not prepared yet. Prepare it in OA first.",
            code="block_not_bound", status_code=404,
        )
    if len(bound_blocks) > 1:
        raise DomainError("Block has conflicting Area/Power bindings.", code="area_binding_conflict", status_code=409)
    return area_project, bound_blocks[0]


def resolve_area_only_project_and_block(
    area_projects: list[dict[str, Any]],
    area_blocks: list[dict[str, Any]],
    *,
    project: str,
    block: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
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
    return area_project, matched_blocks[0]


def build_version_binding_payload(
    project_id: str,
    block_id: str,
    version_id: str,
    area_project_id: Any,
    area_block_id: Any,
    area_version_id: Any,
) -> dict[str, str]:
    return {
        "namespace": BINDING_NAMESPACE,
        "project_id": project_id,
        "block_id": block_id,
        "version_id": version_id,
        "area_project_id": str(area_project_id),
        "area_block_id": str(area_block_id),
        "area_version_id": str(area_version_id),
    }


def ensure_bound_area_project_and_block(
    area_db,
    conn,
    rows: list[dict[str, Any]],
    *,
    actor_id: str,
    project_id: str,
    project_name: str,
    process: str | None = None,
    block_id: str | None = None,
    block_name: str | None = None,
    on_conflict: Callable[[], None],
) -> tuple[dict[str, Any], dict[str, Any] | None, bool, bool]:
    """Idempotently find or insert Area ``project`` and optional ``block`` rows under binding lock."""
    def match(kind: str, native_id: str):
        matches = [
            r for r in rows
            if r["record_type"] == kind and (r.get("payload") or {}).get("sanity_" + kind + "_id") == native_id
        ]
        if len(matches) > 1:
            on_conflict()
        return matches[0] if matches else None

    project_created = False
    block_created = False
    project_row = match("project", project_id)
    if project_row is None:
        pid = uuid4()
        payload: dict[str, Any] = {"name": project_name, "sanity_project_id": project_id}
        if process:
            payload["process"] = process
        project_row = area_db.insert_record(conn, "project", payload, actor_id, id=pid, entity_id=pid)
        rows.append(project_row)
        project_created = True

    block_row = None
    if block_id is not None:
        block_row = match("block", block_id)
        if block_row is not None:
            if block_row["project_id"] != project_row["id"]:
                on_conflict()
        else:
            bid = uuid4()
            block_row = area_db.insert_record(
                conn,
                "block",
                {"name": block_name or block_id, "sanity_block_id": block_id},
                actor_id,
                id=bid,
                entity_id=bid,
                project_id=project_row["id"],
                parent_id=project_row["id"],
                block_id=bid,
            )
            rows.append(block_row)
            block_created = True
    return project_row, block_row, project_created, block_created


def resolve_area_version_info(
    db,
    conn,
    version_or_id: dict[str, Any] | UUID,
    block_id: UUID | None = None,
    *,
    host_db=None,
    auto_open_host: bool = False,
    cached_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, str | None]:
    """Resolve explicit ``(net_ver, revision, layout, eco_num)`` from Area version payload or OA WS_VERSION.

    Names, order, and timestamps are never used to guess a NET_VER.
    """
    empty = {"net_ver": None, "revision": None, "layout": None, "eco_num": None}
    version = version_or_id if isinstance(version_or_id, dict) else db.record(conn, version_or_id, kind="version")
    version_id = version["id"]
    payload = version.get("payload") or {}
    if payload.get("net_ver"):
        return {
            "net_ver": str(payload["net_ver"]),
            "revision": str(payload.get("revision") or ""),
            "layout": str(payload.get("layout") or ""),
            "eco_num": str(payload.get("eco_num") or ""),
        }
    close_host = False
    if host_db is None and auto_open_host:
        try:
            host_db = HostDatabase("oa")
            host_db.__enter__()
            close_host = True
        except Exception:
            host_db = None
    if host_db is None:
        return dict(empty)
    try:
        bindings = cached_bindings
        if bindings is None:
            bindings = [
                row["payload"]
                for row in host_db.execute(
                    "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                    (BINDING_PREFIX + "%",),
                ).fetchall()
            ]
        natives = {
            b.get("version_id")
            for b in bindings
            if b.get("area_version_id") == str(version_id)
            and (block_id is None or b.get("area_block_id") == str(block_id))
        }
        direct = payload.get("sanity_version_id")
        if not natives and isinstance(direct, str):
            natives = {direct}
        found = []
        for native in natives:
            row = host_db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_VERSION' AND workspace_key=%s",
                (native,),
            ).fetchone()
            if row and row["payload"].get("net_ver"):
                found.append(row["payload"])
        net_set = {item.get("net_ver") for item in found if item.get("net_ver")}
        if len(net_set) == 1:
            sample = found[0]
            return {
                "net_ver": sample.get("net_ver"),
                "revision": sample.get("revision"),
                "layout": sample.get("layout"),
                "eco_num": sample.get("eco_num") or "",
            }
        return dict(empty)
    except Exception:
        return dict(empty)
    finally:
        if close_host and host_db is not None:
            try:
                host_db.__exit__(None, None, None)
            except Exception:
                pass


def collect_applicable_triage_inputs(
    db,
    conn,
    current_upload: dict[str, Any],
    baseline_upload: dict[str, Any],
    kind: str | None,
    *,
    raw_db=None,
    host_db=None,
    lineage_fn: Callable[[Any, Any], list[Any]] | None = None,
    current_net_override: str | None = None,
    auto_open_host: bool = False,
) -> dict[str, Any]:
    """Shared lineage/NET_VER/notes/watched/expectations/process collector for OA and VWP triage."""
    raw = raw_db if raw_db is not None else db
    chain = [str(v) for v in lineage_fn(conn, current_upload["version_id"])] if lineage_fn else version_chain(db, conn, current_upload["version_id"])
    interval = chain
    bvid_str = str(baseline_upload["version_id"])
    if kind == "previous_version":
        interval = chain[: chain.index(bvid_str)] if bvid_str in chain else chain
    wanted = list(dict.fromkeys([*interval, *([bvid_str] if kind == "previous_version" else [])]))

    cached_bindings = None
    if host_db is not None:
        cached_bindings = [
            row["payload"]
            for row in host_db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                (BINDING_PREFIX + "%",),
            ).fetchall()
        ]
    labels: dict[str, str | None] = {}
    for vid in wanted:
        info = resolve_area_version_info(
            db, conn, UUID(str(vid)), current_upload["block_id"],
            host_db=host_db, auto_open_host=auto_open_host, cached_bindings=cached_bindings,
        )
        labels[str(vid)] = info.get("net_ver")
    if current_net_override:
        labels[str(current_upload["version_id"])] = current_net_override

    baseline_net = labels.get(bvid_str) if kind == "previous_version" else None
    current_net = labels.get(str(current_upload["version_id"]))
    applicable = [
        label
        for label in dict.fromkeys(labels.get(vid) for vid in reversed(interval))
        if label is not None and label != baseline_net
    ]
    if current_net_override and current_net_override != baseline_net and current_net_override not in applicable:
        applicable.append(current_net_override)

    warnings = []
    unresolved = sum(labels.get(vid) is None for vid in interval)
    if unresolved:
        warnings.append({"code": "net_ver_unresolved", "count": unresolved})
    if kind == "previous_version" and baseline_net is None:
        warnings.append({"code": "baseline_net_ver_unresolved", "count": 1})
    same_rtl = kind == "previous_version" and current_net is not None and current_net == baseline_net

    notes = []
    for row in db.records(conn, "change", project_id=current_upload["project_id"], block_id=current_upload["block_id"], latest=True, limit=500):
        payload = row["payload"]
        if payload.get("archived"):
            continue
        if payload.get("note_kind") == "net_ver":
            applies = payload.get("net_ver") in applicable
        elif payload.get("note_kind") == "project_change":
            applies = kind == "previous_project" and str(payload.get("baseline_project_id")) == str(baseline_upload["project_id"])
        else:
            applies = False
        if applies:
            notes.append({
                "id": str(row["id"]),
                "entity_id": str(row["entity_id"]),
                "revision": row["revision"],
                "kind": payload["note_kind"],
                "net_ver": payload.get("net_ver"),
                "text": payload["text"],
                "updated_by": row.get("updated_by"),
                "created_at": row.get("created_at"),
            })
    order = {label: pos for pos, label in enumerate(applicable)}
    notes.sort(key=lambda note: (note["kind"] != "project_change", order.get(note["net_ver"], -1), note["id"]))

    watched = []
    for row in db.records(conn, "target", project_id=current_upload["project_id"], block_id=current_upload["block_id"], latest=True, limit=500):
        if row["payload"].get("watch"):
            watched.extend(row["payload"].get("paths", []))

    expectations = []
    for row in db.records(conn, "tracking_expectation", project_id=current_upload["project_id"], block_id=current_upload["block_id"], latest=True, limit=500):
        p = row["payload"]
        if p.get("archived") or row.get("state") == "archived":
            continue
        eff = p.get("effective_net_ver")
        if eff is None or eff in applicable or eff == current_net:
            expectations.append({"id": str(row["id"]), **p})

    process = {
        "current": raw.record(conn, current_upload["project_id"], kind="project")["payload"].get("process"),
        "baseline": raw.record(conn, baseline_upload["project_id"], kind="project")["payload"].get("process"),
    }
    return {
        "comparison_kind": kind,
        "notes": notes,
        "note_warnings": warnings,
        "same_rtl": same_rtl,
        "applicable_net_vers": applicable,
        "watched_paths": list(dict.fromkeys(watched)),
        "approved_expectations": expectations,
        "process": process,
    }
