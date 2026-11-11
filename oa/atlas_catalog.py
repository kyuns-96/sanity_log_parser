"""The native WS catalog and explicit, exact-bound numerical adapters.

No identity registration, DDL, name matching, or writes during catalog reads.
Native version payloads remain byte-for-byte compatible with source intake;
Atlas declarations and numerical ancestry live in a separate WS_CONFIG record.
"""
from __future__ import annotations

from uuid import uuid4

from common.sanity_common import ReviewError
from common.sanity_db import lock_key
from common.sanity_submission import source_version
from oa.sanity_workspace import WorkspaceStore


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
            if not (manage and self.can_manage):
                snapshot = self.access.snapshot(self.actor.user_id)
                if project_id not in {g["project_id"] for g in snapshot["grants"]}:
                    fail("RESOURCE_NOT_FOUND")
        return row

    def scope(self, project_id, block_id=None, version_id=None, *, write=False):
        project = self.project(project_id, write=write)
        if version_id is not None and block_id is None:
            fail("INVALID_REQUEST")
        block = self.ws.get("WS_BLOCK", block_id) if block_id else None
        if block and (block["payload"]["project_id"] != project_id
                      or block["workspace_project_ref"] != project["record_id"]):
            fail("RESOURCE_NOT_FOUND")
        if block and not self.local:
            if {"schema_version": 1, "project_id": project_id, "block_id": block_id} not in self.access._scopes(self.actor.user_id, write=write):
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
            return self.access.create_project(self.actor, body.request_id, body.project_id, body.name, body.description)
        def create():
            if self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_PROJECT' AND workspace_key=%s", (body.project_id,)).fetchone():
                fail()
            self.ws.put("WS_PROJECT", body.project_id, {"id": body.project_id, "project_id": body.project_id,
                        "name": body.name, "description": body.description, "tops": []})
            return {"status": "CREATED", "project_id": body.project_id}
        return self.command(body.request_id, "PROJECT", body.model_dump(mode="json", exclude={"request_id"}), create)

    def create_block(self, body):
        if not self.can_manage:
            fail("FORBIDDEN")
        self.project(body.project_id, manage=True)
        if not self.local:
            return self.access.create_block(self.actor, body.request_id, body.project_id, body.block_id, body.name)
        def create():
            if self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_BLOCK' AND workspace_key=%s", (body.block_id,)).fetchone():
                fail()
            self.ws.put("WS_BLOCK", body.block_id, {"id": body.block_id, "project_id": body.project_id,
                        "name": body.name, "owners": []}, project=body.project_id)
            return {"status": "CREATED", "project_id": body.project_id, "block_id": body.block_id}
        return self.command(body.request_id, "BLOCK", body.model_dump(mode="json", exclude={"request_id"}), create)

    @staticmethod
    def identity(body):
        return source_version({"source_namespace": body.source_namespace, "project_key": body.project_id,
                               "block_key": body.block_id}, body.model_dump())

    def create_version(self, body):
        self.scope(body.project_id, body.block_id, write=True)
        if body.version_id != self.identity(body):
            fail("INVALID_VERSION_IDENTITY")
        if body.layout == "PRE":
            if body.ecos not in ([], [None]) or body.source_pre_id is not None:
                fail("INVALID_REQUEST")
        elif any(eco is None for eco in body.ecos):
            fail("INVALID_REQUEST")
        if len(body.ecos) != len(set(body.ecos)):
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
                   "ecos": [None] if body.layout == "PRE" else [], "source_pre_id": body.source_pre_id,
                   "source_namespace": body.source_namespace}
        metadata = {"previous_version_id": body.previous_version_id,
                    "declared_ecos": [None] if body.layout == "PRE" else body.ecos}
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
        result = []
        for project in self.ws.rows("WS_PROJECT"):
            pid = project["workspace_key"]
            if allowed is not None and pid not in allowed:
                continue
            value = project["payload"]
            item = {"id": pid, "name": value["name"], "description": value.get("description", ""),
                    "can_write": writable is None or pid in writable, "blocks": []}
            # Registry names remain discoverable for requesting project access;
            # protected versions and bindings require the current held grant.
            if item["can_write"]:
                for block in blocks:
                    if block["workspace_project_ref"] != project["record_id"]:
                        continue
                    bid = block["workspace_key"]
                    child = {"id": bid, "name": block["payload"]["name"], "versions": []}
                    for version in versions:
                        if version["workspace_block_ref"] == block["record_id"]:
                            child["versions"].append({**self.ws.version_ecos(version), **self.metadata(version["workspace_key"])})
                    item["blocks"].append(child)
            result.append(item)
        return result


class AreaPreparationError(Exception):
    def __init__(self, result):
        self.result = result


class AreaAdapters:
    """Exact mappings; existing project-global Area version UUIDs stay intact."""
    PREFIX = "design-atlas-v1.binding."

    def __init__(self, catalog):
        from area_power.common.db import Database
        self.catalog = catalog
        self.db = Database()

    def rows(self, conn):
        from psycopg import sql
        return conn.execute(sql.SQL("SELECT * FROM {} WHERE record_type IN ('project','block','version') ORDER BY id").format(self.db.table("tracking_record"))).fetchall()

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
                if not project["can_write"]:
                    continue
                p = self.match(rows, "project", project["id"])
                project["area_id"] = str(p["id"]) if p else None
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
        project, block, version = catalog.scope(body.project_id, body.block_id, body.version_id, write=True)
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
                        old = self.db.update_record(conn, old["id"], catalog.actor.user_id,
                            expected_row_version=old["row_version"],
                            payload={**old["payload"], "sanity_" + kind + "_id": native_id})
                        rows[:] = [old if r["id"] == old["id"] else r for r in rows]
                    return old
                identifier = uuid4()
                fields = {"id": identifier, "entity_id": identifier}
                if kind == "block":
                    fields.update(project_id=parent["id"], parent_id=parent["id"], block_id=identifier)
                new = self.db.insert_record(conn, kind,
                    {"name": native["payload"]["name"], "sanity_" + kind + "_id": native_id},
                    catalog.actor.user_id, **fields)
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
                    v = self.db.insert_record(conn, "version",
                        {"name": vid, "sanity_version_id": vid,
                         "previous_version_id": str(previous_row["id"]) if previous_row else None},
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
