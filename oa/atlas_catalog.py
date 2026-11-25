"""The native WS catalog and explicit, exact-bound numerical adapters.

No identity registration, DDL, name matching, or writes during catalog reads.
Native version payloads remain byte-for-byte compatible with source intake;
Atlas declarations and numerical ancestry live in a separate WS_CONFIG record.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import UUID, uuid4

from common.sanity_common import ReviewError
from common.sanity_db import Database as SanityDatabase, json_value, lock_key
from common.sanity_submission import source_version
from oa.sanity_workspace import WorkspaceStore

logger = logging.getLogger(__name__)


def fail(code="REQUEST_CONFLICT"):
    raise ReviewError(code, "The shared catalog contract could not be satisfied")


class AtlasCatalog:
    def __init__(self, db, actor):
        self.db, self.actor = db, actor
        self.ws = WorkspaceStore(db)
        self.local = actor.auth_method == "local_token"
        self.access = getattr(actor, "access", None)
        if not self.local and self.access is None:
            fail("FORBIDDEN")

    @property
    def can_manage(self):
        return self.local or self.access.is_admin(self.actor.user_id)

    def lock(self):
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-catalog"),))

    def project(self, project_id, *, write=False, manage=False):
        if manage and not self.can_manage:
            fail("FORBIDDEN")
        row = self.ws.get("WS_PROJECT", project_id)
        if not self.local:
            registry = self.access.catalog()
            if project_id not in {p["id"] for p in registry["projects"]}:
                fail("RESOURCE_NOT_FOUND")
            if write and not (manage and self.can_manage):
                snapshot = self.access.snapshot(self.actor.user_id)
                if project_id not in {g["project_id"] for g in snapshot["grants"]}:
                    fail("FORBIDDEN")
        return row

    def scope(self, project_id, block_id=None, version_id=None, *, write=False, manage=False):
        project = self.project(project_id, write=write, manage=manage)
        if version_id is not None and block_id is None:
            fail("INVALID_REQUEST")
        block = self.ws.get("WS_BLOCK", block_id) if block_id else None
        if block and (block["payload"]["project_id"] != project_id
                      or block["workspace_project_ref"] != project["record_id"]):
            fail("RESOURCE_NOT_FOUND")
        if block and not self.local:
            if manage and self.can_manage:
                reg_blocks = {(b["project_id"], b["id"]) for b in self.access.catalog()["blocks"]}
                if (project_id, block_id) not in reg_blocks:
                    fail("RESOURCE_NOT_FOUND")
            elif {"schema_version": 1, "project_id": project_id, "block_id": block_id} not in self.access._scopes(self.actor.user_id, write=write):
                fail("RESOURCE_NOT_FOUND")
        version = self.ws.get("WS_VERSION", version_id) if version_id else None
        if version and (version["workspace_project_ref"] != project["record_id"]
                        or version["workspace_block_ref"] != block["record_id"]
                        or version["payload"].get("project_id") != project_id
                        or version["payload"].get("block_id") != block_id):
            fail("RESOURCE_NOT_FOUND")
        return project, block, version

    def metadata(self, version_id):
        row = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                              ("atlas.version." + version_id,)).fetchone()
        return row["payload"] if row else {}

    def declare_lineage(self, body):
        if not body.version_id:
            fail("INVALID_REQUEST")
        self.lock()
        self.scope(body.project_id, body.block_id, body.version_id, write=True)
        existing = self.metadata(body.version_id)
        if existing:
            if existing.get("previous_version_id") != body.previous_version_id:
                fail("AREA_BINDING_CONFLICT")
            return
        seen = {body.version_id}
        previous = body.previous_version_id
        while previous:
            if previous in seen:
                fail("INVALID_REQUEST")
            seen.add(previous)
            self.scope(body.project_id, body.block_id, previous)
            previous = self.metadata(previous).get("previous_version_id")
        self.ws.put("WS_CONFIG", "atlas.version." + body.version_id,
                    {"previous_version_id": body.previous_version_id})

    def command(self, request_id, operation, data, action):
        self.lock()
        key = "atlas.command." + str(request_id)
        expected = {"actor_id": self.actor.user_id, "operation": operation, "data": data}
        existing = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (key,)).fetchone()
        if existing:
            if any(existing["payload"].get(k) != value for k, value in expected.items()):
                fail()
            return {**existing["payload"]["result"], "status": "ALREADY_CREATED"}
        result = action()
        self.ws.put("WS_CONFIG", key, {**expected, "result": result})
        return result

    def create_project(self, body):
        if not self.can_manage:
            fail("FORBIDDEN")
        if not self.local:
            return self.access.create_project(self.actor, **body.model_dump())
        def create():
            project_id, existing = self.ws.named_catalog("WS_PROJECT", body.name)
            self.ws.select_initial_process(project_id, body.process, existing=existing is not None)
            if existing:
                if body.description and existing["payload"].get("description", "") != body.description:
                    fail()
                return {"status": "ALREADY_CREATED", "project_id": project_id}
            self.ws.put("WS_PROJECT", project_id, {"id": project_id, "project_id": project_id,
                        "name": body.name, "description": body.description, "tops": []})
            return {"status": "CREATED", "project_id": project_id}
        return self.command(body.request_id, "PROJECT", body.model_dump(mode="json", exclude={"request_id"}, exclude_none=True), create)

    def create_block(self, body):
        if not self.can_manage:
            fail("FORBIDDEN")
        self.project(body.project_id, manage=True)
        if not self.local:
            return self.access.create_block(self.actor, body.request_id, body.project_id, body.name)
        def create():
            block_id, existing = self.ws.named_catalog("WS_BLOCK", body.name, body.project_id)
            if existing:
                return {"status": "ALREADY_CREATED", "project_id": body.project_id, "block_id": block_id}
            self.ws.put("WS_BLOCK", block_id, {"id": block_id, "project_id": body.project_id,
                        "name": body.name, "owners": []}, project=body.project_id)
            return {"status": "CREATED", "project_id": body.project_id, "block_id": block_id}
        return self.command(body.request_id, "BLOCK", body.model_dump(mode="json", exclude={"request_id"}), create)

    @staticmethod
    def identity(body):
        return source_version({"source_namespace": body.source_namespace, "project_key": body.project_id,
                               "block_key": body.block_id}, body.model_dump())

    def create_version(self, body):
        self.scope(body.project_id, body.block_id, write=True)
        if body.version_id != self.identity(body):
            fail("INVALID_VERSION_IDENTITY")
        if body.layout == "PRE" and body.source_pre_id is not None:
            fail("INVALID_REQUEST")
        if any(eco is None for eco in body.ecos) or len(body.ecos) != len(set(body.ecos)):
            fail("INVALID_REQUEST")
        if body.ecos and body.ecos != [body.eco_num]:
            fail("INVALID_REQUEST")
        if body.source_pre_id:
            pre = self.scope(body.project_id, body.block_id, body.source_pre_id)[2]["payload"]
            if (pre["layout"] != "PRE" or pre["net_ver"] != body.net_ver
                    or pre.get("source_namespace") != body.source_namespace):
                fail("INVALID_REQUEST")
        if body.previous_version_id:
            self.scope(body.project_id, body.block_id, body.previous_version_id)
            if body.previous_version_id == body.version_id:
                fail("INVALID_REQUEST")
        payload = {"id": body.version_id, "project_id": body.project_id, "block_id": body.block_id,
                   "net_ver": body.net_ver, "revision": body.revision, "layout": body.layout,
                   "eco_num": body.eco_num, "ecos": [], "source_pre_id": body.source_pre_id,
                   "source_namespace": body.source_namespace}
        metadata = {"previous_version_id": body.previous_version_id,
                    "declared_ecos": [body.eco_num]}
        def create():
            old = self.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_VERSION' AND workspace_key=%s", (body.version_id,)).fetchone()
            if old:
                if old["payload"] != payload or self.metadata(body.version_id) != metadata:
                    fail()
                return {"status": "ALREADY_CREATED", "version_id": body.version_id}
            self.ws.put("WS_VERSION", body.version_id, payload, project=body.project_id, block=body.block_id)
            self.ws.put("WS_CONFIG", "atlas.version." + body.version_id, metadata)
            return {"status": "CREATED", "project_id": body.project_id, "block_id": body.block_id, "version_id": body.version_id}
        return self.command(body.request_id, "VERSION", body.model_dump(mode="json", exclude={"request_id"}), create)

    def catalog(self):
        allowed = None if self.local else {p["id"] for p in self.access.catalog()["projects"]}
        writable = None if self.local else {g["project_id"] for g in self.access.snapshot(self.actor.user_id)["grants"]}
        blocks = self.ws.rows("WS_BLOCK")
        versions = self.ws.rows("WS_VERSION")
        process_by_project = {row["payload"]["project_id"]: row["payload"]["process"] for row in self.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
            ("atlas.project-process.%",)).fetchall()}
        status_by_project = {}
        for row in self.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' "
            "AND (workspace_key LIKE %s OR workspace_key LIKE %s) "
            "ORDER BY COALESCE((payload->>'revision')::bigint, 0) ASC, created_at ASC, record_id ASC",
            ("atlas.project-status.%", "atlas.pstat.%"),
        ).fetchall():
            p = row["payload"] or {}
            pid_val = p.get("project_id")
            if pid_val and p.get("status") in ("active", "completed"):
                status_by_project[pid_val] = p["status"]
        por_by_block = {}
        for row in self.db.execute("SELECT workspace_key, version, state FROM {oa} WHERE record_type='WS_POR'").fetchall():
            state = row["state"] or {}
            por_by_block[row["workspace_key"]] = {
                "revision": row["version"],
                "version_id": state.get("version_id"),
                "assigned_by": state.get("assigned_by"),
                "assigned_at": state.get("assigned_at"),
            }
        empty_por = {"revision": 0, "version_id": None, "assigned_by": None, "assigned_at": None}
        result = []
        for project in self.ws.rows("WS_PROJECT"):
            pid = project["workspace_key"]
            if allowed is not None and pid not in allowed:
                continue
            value = project["payload"]
            item = {"id": pid, "name": value["name"], "description": value.get("description", ""),
                    "process": process_by_project.get(pid),
                    "status": "completed" if status_by_project.get(pid) == "completed" else "active",
                    "can_write": writable is None or pid in writable, "blocks": []}
            for block in blocks:
                if block["workspace_project_ref"] != project["record_id"]:
                    continue
                bid = block["workspace_key"]
                child = {"id": bid, "name": block["payload"]["name"], "por": por_by_block.get(bid, dict(empty_por)), "versions": []}
                for version in versions:
                    if version["workspace_block_ref"] == block["record_id"]:
                        child["versions"].append({**self.ws.version_ecos(version), **self.metadata(version["workspace_key"])})
                item["blocks"].append(child)
            result.append(item)
        return result

    def set_project_status(self, project_id: str, status: str):
        if status not in ("active", "completed"):
            fail("INVALID_REQUEST")
        self.lock()
        self.project(project_id, write=True, manage=self.can_manage)
        rows = self.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' "
            "AND (workspace_key LIKE %s OR workspace_key LIKE %s) "
            "AND payload->>'project_id' = %s",
            ("atlas.project-status.%", "atlas.pstat.%", project_id),
        ).fetchall()
        next_revision = max((int((r["payload"] or {}).get("revision") or 0) for r in rows), default=0) + 1
        self.ws.put("WS_CONFIG", "atlas.pstat." + uuid4().hex, {
            "project_id": project_id,
            "status": status,
            "revision": next_revision,
            "updated_by": self.actor.user_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"status": "UPDATED", "project_id": project_id, "project_status": status}

    def assign_por(self, project_id: str, block_id: str, version_id: str, expected_revision: int, request_id, reason: str):
        if (type(expected_revision) is not int or expected_revision < 0
                or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500):
            fail("INVALID_REQUEST")
        try:
            request_id = str(UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError):
            fail("INVALID_REQUEST")
        project, block, version = self.scope(project_id, block_id, version_id, write=True, manage=self.can_manage)
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("production-por-" + block_id),))
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("workspace-request-" + request_id),))
        command = {
            "user_id": self.actor.user_id,
            "block_id": block_id,
            "version_id": version_id,
            "expected_revision": expected_revision,
            "reason": reason.strip(),
        }
        previous = self.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_POR_EVENT' AND workspace_key=%s", (request_id,)
        ).fetchone()
        if previous:
            if previous["payload"]["command"] != command:
                raise ReviewError("REQUEST_CONFLICT", "Request ID has different contents")
            return previous["payload"]["result"]
        exists = self.db.execute(
            "SELECT 1 FROM {oa} WHERE record_type='WS_POR' AND workspace_key=%s", (block_id,)
        ).fetchone()
        if not exists:
            if expected_revision != 0:
                raise ReviewError("REVISION_CONFLICT", "PoR changed; reload the current version")
            self.ws.put("WS_POR", block_id, {"block_id": block_id},
                        project=project["workspace_key"], block=block["workspace_key"])
        current = self.ws.get("WS_POR", block_id, lock=True)
        if current["version"] != expected_revision:
            raise ReviewError("REVISION_CONFLICT", "PoR changed; reload the current version")
        if current["state"].get("version_id") == version_id:
            raise ReviewError("POR_UNCHANGED", "This version is already the current PoR")
        at = datetime.now(timezone.utc).isoformat()
        revision = current["version"] + 1
        actor_info = {"id": self.actor.user_id, "name": getattr(self.actor, "reviewer", None) or self.actor.user_id}
        next_state = {
            "version_id": version_id,
            "version_record_id": str(version["record_id"]),
            "assigned_by": actor_info,
            "assigned_at": at,
        }
        self.db.execute(
            "UPDATE {oa} SET state=%s, version=%s, updated_at=clock_timestamp() WHERE record_id=%s",
            (json_value(next_state), revision, current["record_id"]),
        )
        result = {
            "status": "POR_ASSIGNED",
            "revision": revision,
            "version": version["payload"],
            "assigned_by": actor_info,
            "assigned_at": at,
        }
        event = {
            "id": request_id,
            "command": command,
            "previous_version_id": current["state"].get("version_id"),
            "version_record_id": str(version["record_id"]),
            "version": version["payload"],
            "actor": actor_info,
            "reason": reason.strip(),
            "at": at,
            "revision": revision,
            "result": result,
        }
        self.ws.put("WS_POR_EVENT", request_id, event, project=project["workspace_key"], block=block_id)
        return result


class AreaPreparationError(Exception):
    def __init__(self, result):
        self.result = result


class AreaAdapters:
    """Exact mappings; existing project-global Area version UUIDs stay intact."""
    PREFIX = "design-atlas-v1.binding."

    def __init__(self, catalog):
        from common.area_power.db import Database
        self.catalog = catalog
        self.db = Database()

    def rows(self, conn):
        from psycopg import sql
        return conn.execute(sql.SQL("SELECT * FROM {} WHERE record_type IN ('project','block','version') ORDER BY id").format(self.db.table("di_area_power_tracking_record"))).fetchall()

    def bindings(self):
        rows = self.catalog.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                                       (self.PREFIX + "%",)).fetchall()
        return [r["payload"] for r in rows]

    @staticmethod
    def binding(row):
        return row["payload"].get("sanity_" + row["record_type"] + "_id")

    def match(self, rows, kind, native_id):
        matches = [r for r in rows if r["record_type"] == kind and self.binding(r) == native_id]
        if len(matches) > 1:
            fail("AREA_BINDING_CONFLICT")
        return matches[0] if matches else None

    def version_binding(self, bindings, native_id):
        matches = [b for b in bindings if b.get("version_id") == native_id]
        if len(matches) > 1:
            fail("AREA_BINDING_CONFLICT")
        return matches[0] if matches else None

    def version_row(self, rows, bindings, native_id):
        binding = self.version_binding(bindings, native_id)
        direct = self.match(rows, "version", native_id)
        if binding:
            row = next((r for r in rows if r["record_type"] == "version" and str(r["id"]) == binding["area_version_id"]), None)
            if row is None or (direct is not None and direct["id"] != row["id"]):
                fail("AREA_BINDING_CONFLICT")
            return row
        return direct

    def validate_previous(self, rows, bindings, row, native_id):
        previous = self.catalog.metadata(native_id).get("previous_version_id")
        parent = self.version_row(rows, bindings, previous) if previous else None
        if previous and parent is None:
            fail("AREA_PREVIOUS_NOT_PREPARED")
        actual = row["payload"].get("previous_version_id")
        if (str(actual) if actual else None) != (str(parent["id"]) if parent else None):
            fail("AREA_BINDING_CONFLICT")

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
                        fail("AREA_BINDING_CONFLICT")
                    block["area_id"] = str(b["id"]) if b else None
                    for version in block["versions"]:
                        binding = self.version_binding(bindings, version["id"])
                        version["area_id"] = None
                        if binding:
                            v = self.version_row(rows, bindings, version["id"])
                            if (not p or not b or v["project_id"] != p["id"]
                                    or binding != self.binding_value(project["id"], block["id"], version["id"], p, b, v)):
                                fail("AREA_BINDING_CONFLICT")
                            self.validate_previous(rows, bindings, v, version["id"])
                            version["area_id"] = str(v["id"])
            if self.catalog.local:
                bound_versions = {b["area_version_id"] for b in bindings}
                unbound = {kind + "s": [r for r in rows if r["record_type"] == kind
                                       and (str(r["id"]) not in bound_versions if kind == "version" else not self.binding(r))]
                           for kind in ("project", "block", "version")}
                bindable = {kind + "s": [r for r in rows if r["record_type"] == kind]
                            for kind in ("project", "block", "version")}
                return {"unbound_area": unbound, "bindable_area": bindable}
        return None

    @staticmethod
    def binding_value(project_id, block_id, version_id, project, block, version):
        return {"namespace": "design-atlas-v1", "project_id": project_id, "block_id": block_id,
                "version_id": version_id, "area_project_id": str(project["id"]),
                "area_block_id": str(block["id"]), "area_version_id": str(version["id"])}

    def prepare(self, body, *, bind=False):
        catalog = self.catalog
        catalog.lock()
        manage_scope = not bind and body.version_id is None and catalog.can_manage
        project, block, version = catalog.scope(body.project_id, body.block_id, body.version_id, write=True, manage=manage_scope)
        if bind and not catalog.local:
            fail("FORBIDDEN")
        if version and "previous_version_id" not in catalog.metadata(body.version_id):
            fail("AREA_LINEAGE_REQUIRED")
        bindings = self.bindings()
        with self.db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
            rows = self.rows(conn)
            def adapter(kind, native, parent=None):
                if native is None:
                    return None
                native_id = native["workspace_key"]
                old = self.match(rows, kind, native_id)
                chosen_id = getattr(body, "area_" + kind + "_id", None) if bind else None
                if chosen_id:
                    chosen = self.db.record(conn, chosen_id, kind=kind, lock=True)
                    if old and chosen["id"] != old["id"]:
                        fail("AREA_BINDING_CONFLICT")
                    if self.binding(chosen) not in (None, native_id):
                        fail("AREA_BINDING_CONFLICT")
                    old = chosen
                elif bind:
                    fail("INVALID_REQUEST")
                if old:
                    if kind == "block" and old["project_id"] != parent["id"]:
                        fail("AREA_BINDING_CONFLICT")
                    if chosen_id and self.binding(old) is None:
                        payload = {**old["payload"], "sanity_" + kind + "_id": native_id}
                        initial_process = catalog.ws.initial_process(native_id) if kind == "project" else None
                        if initial_process:
                            if payload.get("process") not in (None, initial_process):
                                fail("AREA_BINDING_CONFLICT")
                            payload["process"] = initial_process
                        old = self.db.update_record(conn, old["id"], catalog.actor.user_id,
                            expected_row_version=old["row_version"], payload=payload)
                        rows[:] = [old if r["id"] == old["id"] else r for r in rows]
                    return old
                identifier = uuid4()
                fields = {"id": identifier, "entity_id": identifier}
                if kind == "block":
                    fields.update(project_id=parent["id"], parent_id=parent["id"], block_id=identifier)
                payload = {"name": native["payload"]["name"], "sanity_" + kind + "_id": native_id}
                if kind == "project" and (initial_process := catalog.ws.initial_process(native_id)):
                    payload["process"] = initial_process
                new = self.db.insert_record(conn, kind, payload, catalog.actor.user_id, **fields)
                rows.append(new)
                return new
            p = adapter("project", project)
            b = adapter("block", block, p)
            v = None
            binding = None
            if version:
                vid = version["workspace_key"]
                old_binding = self.version_binding(bindings, vid)
                v = self.version_row(rows, bindings, vid)
                if bind:
                    chosen = self.db.record(conn, body.area_version_id, kind="version", lock=True)
                    if v and v["id"] != chosen["id"]:
                        fail("AREA_BINDING_CONFLICT")
                    v = chosen
                if v is None:
                    previous = catalog.metadata(vid).get("previous_version_id")
                    previous_row = self.version_row(rows, bindings, previous) if previous else None
                    if previous and previous_row is None:
                        fail("AREA_PREVIOUS_NOT_PREPARED")
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
                    v = self.db.insert_record(conn, "version", area_ver_payload,
                        catalog.actor.user_id, id=identifier, entity_id=identifier, version_id=identifier,
                        project_id=p["id"], parent_id=p["id"])
                    rows.append(v)
                # Historical Area versions are project-global. Never reparent,
                # change their payload, or collapse their immutable report IDs.
                if v["project_id"] != p["id"] or v.get("block_id") not in (None, b["id"]):
                    fail("AREA_BINDING_CONFLICT")
                self.validate_previous(rows, bindings, v, vid)
                binding = self.binding_value(body.project_id, body.block_id, vid, p, b, v)
                if old_binding and old_binding != binding:
                    fail("AREA_BINDING_CONFLICT")
            result = {"status": "PREPARED", "project_id": body.project_id, "block_id": body.block_id,
                      "version_id": body.version_id, "area_project_id": str(p["id"]),
                      "area_block_id": str(b["id"]) if b else None, "area_version_id": str(v["id"]) if v else None}
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
    """Idempotently create or reuse Area/Power project (and optional block) scope rows.

    Uses the exact same advisory lock and sanity_project_id/sanity_block_id payload
    contract as AreaAdapters.prepare() so newly registered projects and blocks are
    immediately ready for Area/Power without a separate binding step.
    """
    from psycopg import sql
    from common.area_power.db import Database as AreaDatabase

    area_db = AreaDatabase()
    with area_db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("design-atlas-area-bindings"),))
        rows = conn.execute(
            sql.SQL("SELECT * FROM {} WHERE record_type IN ('project','block') ORDER BY id").format(
                area_db.table("di_area_power_tracking_record")
            )
        ).fetchall()

        def match(kind: str, native_id: str):
            matches = [r for r in rows if r["record_type"] == kind and r["payload"].get("sanity_" + kind + "_id") == native_id]
            if len(matches) > 1:
                fail("AREA_BINDING_CONFLICT")
            return matches[0] if matches else None

        project_row = match("project", project_id)
        if project_row is None:
            pid = uuid4()
            payload = {"name": project_name, "sanity_project_id": project_id}
            if process:
                payload["process"] = process
            project_row = area_db.insert_record(conn, "project", payload, actor_id, id=pid, entity_id=pid)
            rows.append(project_row)

        block_row = None
        if block_id is not None:
            block_row = match("block", block_id)
            if block_row is not None:
                if block_row["project_id"] != project_row["id"]:
                    fail("AREA_BINDING_CONFLICT")
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
    """Attach Area/Power project/block binding on creation when Area/Power is enabled.

    Never fails the already-committed native project/block creation if Area DB is
    temporarily unreachable; surfaces area_prepared=False so manual preparation can retry.
    """
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


def sync_all_area_project_blocks(*, actor_id: str = "oa-startup") -> dict[str, int]:
    """Idempotently bind every registered WS_PROJECT and WS_BLOCK to Area/Power.

    Safe to call at OA server startup or from an explicit admin sync action.
    Existing bindings are preserved untouched; missing project/block rows are created
    under the shared design-atlas-area-bindings advisory lock.
    """
    from psycopg import sql
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

    project_ref_to_id = {row["record_id"]: row["workspace_key"] for row in native_projects}
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

        project_by_native: dict[str, dict] = {}
        block_by_native: dict[str, dict] = {}
        for row in rows:
            kind = row["record_type"]
            native_id = row["payload"].get("sanity_" + kind + "_id")
            if not native_id:
                continue
            target = project_by_native if kind == "project" else block_by_native
            if native_id in target:
                fail("AREA_BINDING_CONFLICT")
            target[native_id] = row

        for proj in native_projects:
            pid = proj["workspace_key"]
            if pid in project_by_native:
                continue
            area_pid = uuid4()
            payload = {"name": proj["payload"]["name"], "sanity_project_id": pid}
            if process := process_by_project.get(pid):
                payload["process"] = process
            created = area_db.insert_record(conn, "project", payload, actor_id, id=area_pid, entity_id=area_pid)
            project_by_native[pid] = created
            projects_created += 1

        for blk in native_blocks:
            bid = blk["workspace_key"]
            pid = project_ref_to_id.get(blk["workspace_project_ref"])
            if not pid or pid not in project_by_native:
                continue
            parent = project_by_native[pid]
            if bid in block_by_native:
                if block_by_native[bid]["project_id"] != parent["id"]:
                    fail("AREA_BINDING_CONFLICT")
                continue
            area_bid = uuid4()
            created = area_db.insert_record(
                conn,
                "block",
                {"name": blk["payload"]["name"], "sanity_block_id": bid},
                actor_id,
                id=area_bid,
                entity_id=area_bid,
                project_id=parent["id"],
                parent_id=parent["id"],
                block_id=area_bid,
            )
            block_by_native[bid] = created
            blocks_created += 1

    return {
        "projects_created": projects_created,
        "blocks_created": blocks_created,
        "projects_total": len(native_projects),
        "blocks_total": len(native_blocks),
    }
