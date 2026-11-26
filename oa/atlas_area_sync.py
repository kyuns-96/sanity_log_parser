"""Area/Power scope provisioning and binding adapters for Design Atlas catalog routes."""
from __future__ import annotations

import logging
from uuid import uuid4

from psycopg import sql

from common.area_power.atlas_bindings import (
    BINDING_PREFIX,
    build_version_binding_payload,
    ensure_bound_area_project_and_block,
)
from common.sanity_common import ReviewError
from common.sanity_db import Database as SanityDatabase, lock_key
from oa.sanity_workspace import WorkspaceStore

logger = logging.getLogger(__name__)


def _fail(code: str = "REQUEST_CONFLICT"):
    raise ReviewError(code, "The shared catalog contract could not be satisfied")


class AreaPreparationError(Exception):
    def __init__(self, result):
        self.result = result


class AreaAdapters:
    """Exact mappings; existing project-global Area version UUIDs stay intact."""

    PREFIX = BINDING_PREFIX

    def __init__(self, catalog):
        from common.area_power.db import Database
        self.catalog = catalog
        self.db = Database()

    def rows(self, conn):
        return conn.execute(
            sql.SQL("SELECT * FROM {} WHERE record_type IN ('project','block','version') ORDER BY id").format(
                self.db.table("di_area_power_tracking_record")
            )
        ).fetchall()

    def bindings(self):
        rows = self.catalog.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
            (self.PREFIX + "%",),
        ).fetchall()
        return [r["payload"] for r in rows]

    @staticmethod
    def binding(row):
        return row["payload"].get("sanity_" + row["record_type"] + "_id")

    def match(self, rows, kind, native_id):
        matches = [r for r in rows if r["record_type"] == kind and self.binding(r) == native_id]
        if len(matches) > 1:
            _fail("AREA_BINDING_CONFLICT")
        return matches[0] if matches else None

    def version_binding(self, bindings, native_id):
        matches = [b for b in bindings if b.get("version_id") == native_id]
        if len(matches) > 1:
            _fail("AREA_BINDING_CONFLICT")
        return matches[0] if matches else None

    def version_row(self, rows, bindings, native_id):
        binding = self.version_binding(bindings, native_id)
        direct = self.match(rows, "version", native_id)
        if binding:
            row = next((r for r in rows if r["record_type"] == "version" and str(r["id"]) == binding["area_version_id"]), None)
            if row is None or (direct is not None and direct["id"] != row["id"]):
                _fail("AREA_BINDING_CONFLICT")
            return row
        return direct

    def validate_previous(self, rows, bindings, row, native_id):
        previous = self.catalog.metadata(native_id).get("previous_version_id")
        parent = self.version_row(rows, bindings, previous) if previous else None
        if previous and parent is None:
            _fail("AREA_PREVIOUS_NOT_PREPARED")
        actual = row["payload"].get("previous_version_id")
        if (str(actual) if actual else None) != (str(parent["id"]) if parent else None):
            _fail("AREA_BINDING_CONFLICT")

    def project_links(self, projects):
        bindings = self.bindings()
        with self.db.connect() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            rows = self.rows(conn)
            for project in projects:
                p = self.match(rows, "project", project["id"])
                project["area_id"] = str(p["id"]) if p else None
                if p:
                    project["process"] = p["payload"].get("process")
                for block in project["blocks"]:
                    b = self.match(rows, "block", block["id"])
                    if b and (not p or b["project_id"] != p["id"]):
                        _fail("AREA_BINDING_CONFLICT")
                    block["area_id"] = str(b["id"]) if b else None
                    for version in block["versions"]:
                        binding = self.version_binding(bindings, version["id"])
                        version["area_id"] = None
                        if binding:
                            v = self.version_row(rows, bindings, version["id"])
                            if (
                                not p
                                or not b
                                or v["project_id"] != p["id"]
                                or binding != self.binding_value(project["id"], block["id"], version["id"], p, b, v)
                            ):
                                _fail("AREA_BINDING_CONFLICT")
                            self.validate_previous(rows, bindings, v, version["id"])
                            version["area_id"] = str(v["id"])
            if self.catalog.local:
                bound_versions = {b["area_version_id"] for b in bindings}
                unbound = {
                    kind + "s": [
                        r for r in rows
                        if r["record_type"] == kind
                        and (str(r["id"]) not in bound_versions if kind == "version" else not self.binding(r))
                    ]
                    for kind in ("project", "block", "version")
                }
                bindable = {kind + "s": [r for r in rows if r["record_type"] == kind] for kind in ("project", "block", "version")}
                return {"unbound_area": unbound, "bindable_area": bindable}
        return None

    @staticmethod
    def binding_value(project_id, block_id, version_id, project, block, version):
        return build_version_binding_payload(
            project_id, block_id, version_id, project["id"], block["id"], version["id"]
        )

    def _bind_existing_entity(self, conn, rows, kind, native, parent=None, chosen_id=None):
        native_id = native["workspace_key"]
        old = self.match(rows, kind, native_id)
        if chosen_id:
            chosen = self.db.record(conn, chosen_id, kind=kind, lock=True)
            if old and chosen["id"] != old["id"]:
                _fail("AREA_BINDING_CONFLICT")
            if self.binding(chosen) not in (None, native_id):
                _fail("AREA_BINDING_CONFLICT")
            old = chosen
        else:
            _fail("INVALID_REQUEST")
        if kind == "block" and old["project_id"] != parent["id"]:
            _fail("AREA_BINDING_CONFLICT")
        if self.binding(old) is None:
            payload = {**old["payload"], "sanity_" + kind + "_id": native_id}
            initial_process = self.catalog.ws.initial_process(native_id) if kind == "project" else None
            if initial_process:
                if payload.get("process") not in (None, initial_process):
                    _fail("AREA_BINDING_CONFLICT")
                payload["process"] = initial_process
            old = self.db.update_record(
                conn, old["id"], self.catalog.actor.user_id,
                expected_row_version=old["row_version"], payload=payload,
            )
            rows[:] = [old if r["id"] == old["id"] else r for r in rows]
        return old

    def prepare(self, body, *, bind=False):
        catalog = self.catalog
        catalog.lock()
        manage_scope = not bind and body.version_id is None and catalog.can_manage
        project, block, version = catalog.scope(body.project_id, body.block_id, body.version_id, write=True, manage=manage_scope)
        if bind and not catalog.local:
            _fail("FORBIDDEN")
        if version and "previous_version_id" not in catalog.metadata(body.version_id):
            _fail("AREA_LINEAGE_REQUIRED")
        bindings = self.bindings()
        with self.db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
            rows = self.rows(conn)
            if bind:
                p = self._bind_existing_entity(conn, rows, "project", project, chosen_id=getattr(body, "area_project_id", None))
                b = self._bind_existing_entity(conn, rows, "block", block, parent=p, chosen_id=getattr(body, "area_block_id", None)) if block else None
            else:
                pid = project["workspace_key"]
                p, b, _, _ = ensure_bound_area_project_and_block(
                    self.db,
                    conn,
                    rows,
                    actor_id=catalog.actor.user_id,
                    project_id=pid,
                    project_name=project["payload"]["name"],
                    process=catalog.ws.initial_process(pid),
                    block_id=block["workspace_key"] if block else None,
                    block_name=block["payload"]["name"] if block else None,
                    on_conflict=lambda: _fail("AREA_BINDING_CONFLICT"),
                )
            v = None
            binding = None
            if version:
                vid = version["workspace_key"]
                old_binding = self.version_binding(bindings, vid)
                v = self.version_row(rows, bindings, vid)
                if bind:
                    chosen = self.db.record(conn, body.area_version_id, kind="version", lock=True)
                    if v and v["id"] != chosen["id"]:
                        _fail("AREA_BINDING_CONFLICT")
                    v = chosen
                if v is None:
                    previous = catalog.metadata(vid).get("previous_version_id")
                    previous_row = self.version_row(rows, bindings, previous) if previous else None
                    if previous and previous_row is None:
                        _fail("AREA_PREVIOUS_NOT_PREPARED")
                    identifier = uuid4()
                    vp = version["payload"] or {}
                    area_ver_payload = {
                        "name": vid,
                        "sanity_version_id": vid,
                        "net_ver": vp.get("net_ver"),
                        "revision": vp.get("revision"),
                        "layout": vp.get("layout"),
                        "previous_version_id": str(previous_row["id"]) if previous_row else None,
                    }
                    if vp.get("eco_num") is not None:
                        area_ver_payload["eco_num"] = vp["eco_num"]
                    v = self.db.insert_record(
                        conn, "version", area_ver_payload,
                        catalog.actor.user_id, id=identifier, entity_id=identifier, version_id=identifier,
                        project_id=p["id"], parent_id=p["id"],
                    )
                    rows.append(v)
                if v["project_id"] != p["id"] or v.get("block_id") not in (None, b["id"]):
                    _fail("AREA_BINDING_CONFLICT")
                self.validate_previous(rows, bindings, v, vid)
                binding = self.binding_value(body.project_id, body.block_id, vid, p, b, v)
                if old_binding and old_binding != binding:
                    _fail("AREA_BINDING_CONFLICT")
            result = {
                "status": "PREPARED",
                "project_id": body.project_id,
                "block_id": body.block_id,
                "version_id": body.version_id,
                "area_project_id": str(p["id"]),
                "area_block_id": str(b["id"]) if b else None,
                "area_version_id": str(v["id"]) if v else None,
            }
        if binding and not self.version_binding(bindings, body.version_id):
            try:
                catalog.ws.put("WS_CONFIG", self.PREFIX + body.version_id, binding)
            except Exception as exc:
                raise AreaPreparationError(result) from exc
        return result


def ensure_area_project_block(
    *,
    actor_id: str,
    project_id: str,
    project_name: str,
    process: str | None = None,
    block_id: str | None = None,
    block_name: str | None = None,
) -> dict[str, str | None]:
    """Idempotently create or reuse Area/Power project (and optional block) scope rows."""
    from common.area_power.db import Database as AreaDatabase

    area_db = AreaDatabase()
    with area_db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
        rows = conn.execute(
            sql.SQL("SELECT * FROM {} WHERE record_type IN ('project','block') ORDER BY id").format(
                area_db.table("di_area_power_tracking_record")
            )
        ).fetchall()
        project_row, block_row, _, _ = ensure_bound_area_project_and_block(
            area_db,
            conn,
            rows,
            actor_id=actor_id,
            project_id=project_id,
            project_name=project_name,
            process=process,
            block_id=block_id,
            block_name=block_name,
            on_conflict=lambda: _fail("AREA_BINDING_CONFLICT"),
        )
        return {
            "area_project_id": str(project_row["id"]),
            "area_block_id": str(block_row["id"]) if block_row else None,
        }


def try_auto_prepare_area_scope(
    request,
    result: dict,
    *,
    actor_id: str,
    project_id: str,
    project_name: str,
    process: str | None = None,
    block_id: str | None = None,
    block_name: str | None = None,
) -> dict:
    """Attach Area/Power project/block binding on creation when Area/Power is enabled."""
    if not getattr(request.app.state, "area_power_enabled", False):
        return result
    from common.area_power.errors import ConfigurationError, DomainError

    try:
        bound = ensure_area_project_block(
            actor_id=actor_id,
            project_id=project_id,
            project_name=project_name,
            process=process,
            block_id=block_id,
            block_name=block_name,
        )
        return {**result, "area_prepared": True, **bound}
    except ReviewError as exc:
        logger.warning("Auto Area/Power scope binding skipped (%s) for project=%s block=%s", exc.code, project_id, block_id)
        return {**result, "area_prepared": False, "area_error": exc.code}
    except (ConfigurationError, DomainError) as exc:
        code = "AREA_POWER_UNAVAILABLE" if isinstance(exc, ConfigurationError) else exc.code.upper()
        logger.warning("Auto Area/Power scope binding failed (%s) for project=%s block=%s", code, project_id, block_id)
        return {**result, "area_prepared": False, "area_error": code}


def resolve_project_sync_metadata(ws: WorkspaceStore, project_id: str, *, explicit_process: str | None = None) -> tuple[str, str | None]:
    project_row = ws.get("WS_PROJECT", project_id)
    process = explicit_process if explicit_process is not None else ws.initial_process(project_id)
    return project_row["payload"]["name"], process


def sync_all_area_project_blocks(*, actor_id: str = "oa-startup") -> dict[str, int]:
    """Idempotently bind every registered WS_PROJECT and WS_BLOCK to Area/Power."""
    from common.area_power.db import Database as AreaDatabase

    with SanityDatabase("oa") as oa_db, oa_db.transaction(readonly=True):
        ws = WorkspaceStore(oa_db)
        native_projects = ws.rows("WS_PROJECT")
        native_blocks = ws.rows("WS_BLOCK")
        process_by_project = {
            row["payload"]["project_id"]: row["payload"]["process"]
            for row in oa_db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                ("atlas.project-process.%",),
            ).fetchall()
        }

    project_by_ref = {row["record_id"]: row for row in native_projects}
    area_db = AreaDatabase()
    projects_created = 0
    blocks_created = 0
    with area_db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
        rows = conn.execute(
            sql.SQL("SELECT * FROM {} WHERE record_type IN ('project','block') ORDER BY id").format(
                area_db.table("di_area_power_tracking_record")
            )
        ).fetchall()
        for proj in native_projects:
            pid = proj["workspace_key"]
            _, _, p_new, _ = ensure_bound_area_project_and_block(
                area_db,
                conn,
                rows,
                actor_id=actor_id,
                project_id=pid,
                project_name=proj["payload"]["name"],
                process=process_by_project.get(pid),
                on_conflict=lambda: _fail("AREA_BINDING_CONFLICT"),
            )
            projects_created += int(p_new)

        for blk in native_blocks:
            proj = project_by_ref.get(blk["workspace_project_ref"])
            if proj is None:
                continue
            pid = proj["workspace_key"]
            _, _, _, b_new = ensure_bound_area_project_and_block(
                area_db,
                conn,
                rows,
                actor_id=actor_id,
                project_id=pid,
                project_name=proj["payload"]["name"],
                process=process_by_project.get(pid),
                block_id=blk["workspace_key"],
                block_name=blk["payload"]["name"],
                on_conflict=lambda: _fail("AREA_BINDING_CONFLICT"),
            )
            blocks_created += int(b_new)

    return {
        "projects_created": projects_created,
        "blocks_created": blocks_created,
        "projects_total": len(native_projects),
        "blocks_total": len(native_blocks),
    }
