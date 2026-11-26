"""The native WS catalog for Design Atlas; Area/Power sync adapters live in ``oa.atlas_area_sync``.

No identity registration, DDL, name matching, or writes during catalog reads.
Native version payloads remain byte-for-byte compatible with source intake;
Atlas declarations and numerical ancestry live in a separate WS_CONFIG record.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from common.sanity_common import ReviewError
from common.sanity_db import lock_key
from common.sanity_submission import source_version
from oa.atlas_area_sync import (
    AreaAdapters,
    AreaPreparationError,
    ensure_area_project_block,
    resolve_project_sync_metadata,
    sync_all_area_project_blocks,
    try_auto_prepare_area_scope,
)
from oa.sanity_workspace import WorkspaceStore


def fail(code: str = "REQUEST_CONFLICT"):
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
        if block and (
            block["payload"]["project_id"] != project_id
            or block["workspace_project_ref"] != project["record_id"]
        ):
            fail("RESOURCE_NOT_FOUND")
        if block and not self.local:
            if manage and self.can_manage:
                reg_blocks = {(b["project_id"], b["id"]) for b in self.access.catalog()["blocks"]}
                if (project_id, block_id) not in reg_blocks:
                    fail("RESOURCE_NOT_FOUND")
            elif {"schema_version": 1, "project_id": project_id, "block_id": block_id} not in self.access.allowed_scopes(
                self.actor.user_id, write=write
            ):
                fail("RESOURCE_NOT_FOUND")
        version = self.ws.get("WS_VERSION", version_id) if version_id else None
        if version and (
            version["workspace_project_ref"] != project["record_id"]
            or version["workspace_block_ref"] != block["record_id"]
            or version["payload"].get("project_id") != project_id
            or version["payload"].get("block_id") != block_id
        ):
            fail("RESOURCE_NOT_FOUND")
        return project, block, version

    def metadata(self, version_id):
        row = self.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
            ("atlas.version." + version_id,),
        ).fetchone()
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
        self.ws.put(
            "WS_CONFIG",
            "atlas.version." + body.version_id,
            {"previous_version_id": body.previous_version_id},
        )

    def command(self, request_id, operation, data, action):
        self.lock()
        key = "atlas.command." + str(request_id)
        expected = {"actor_id": self.actor.user_id, "operation": operation, "data": data}
        existing = self.db.execute(
            "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (key,)
        ).fetchone()
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
            self.ws.put(
                "WS_PROJECT",
                project_id,
                {"id": project_id, "project_id": project_id, "name": body.name, "description": body.description, "tops": []},
            )
            return {"status": "CREATED", "project_id": project_id}

        return self.command(
            body.request_id, "PROJECT", body.model_dump(mode="json", exclude={"request_id"}, exclude_none=True), create
        )

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
            self.ws.put(
                "WS_BLOCK",
                block_id,
                {"id": block_id, "project_id": body.project_id, "name": body.name, "owners": []},
                project=body.project_id,
            )
            return {"status": "CREATED", "project_id": body.project_id, "block_id": block_id}

        return self.command(body.request_id, "BLOCK", body.model_dump(mode="json", exclude={"request_id"}), create)

    @staticmethod
    def identity(body):
        return source_version(
            {"source_namespace": body.source_namespace, "project_key": body.project_id, "block_key": body.block_id},
            body.model_dump(),
        )

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
            if (
                pre["layout"] != "PRE"
                or pre["net_ver"] != body.net_ver
                or pre.get("source_namespace") != body.source_namespace
            ):
                fail("INVALID_REQUEST")
        if body.previous_version_id:
            self.scope(body.project_id, body.block_id, body.previous_version_id)
            if body.previous_version_id == body.version_id:
                fail("INVALID_REQUEST")
        payload = {
            "id": body.version_id,
            "project_id": body.project_id,
            "block_id": body.block_id,
            "net_ver": body.net_ver,
            "revision": body.revision,
            "layout": body.layout,
            "eco_num": body.eco_num,
            "ecos": [],
            "source_pre_id": body.source_pre_id,
            "source_namespace": body.source_namespace,
        }
        metadata = {"previous_version_id": body.previous_version_id, "declared_ecos": [body.eco_num]}

        def create():
            old = self.db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_VERSION' AND workspace_key=%s", (body.version_id,)
            ).fetchone()
            if old:
                if old["payload"] != payload or self.metadata(body.version_id) != metadata:
                    fail()
                return {"status": "ALREADY_CREATED", "version_id": body.version_id}
            self.ws.put("WS_VERSION", body.version_id, payload, project=body.project_id, block=body.block_id)
            self.ws.put("WS_CONFIG", "atlas.version." + body.version_id, metadata)
            return {
                "status": "CREATED",
                "project_id": body.project_id,
                "block_id": body.block_id,
                "version_id": body.version_id,
            }

        return self.command(body.request_id, "VERSION", body.model_dump(mode="json", exclude={"request_id"}), create)

    def catalog(self):
        allowed = None if self.local else {p["id"] for p in self.access.catalog()["projects"]}
        writable = None if self.local else {g["project_id"] for g in self.access.snapshot(self.actor.user_id)["grants"]}
        blocks = self.ws.rows("WS_BLOCK")
        versions = self.ws.rows("WS_VERSION")
        process_by_project = {
            row["payload"]["project_id"]: row["payload"]["process"]
            for row in self.db.execute(
                "SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                ("atlas.project-process.%",),
            ).fetchall()
        }
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
        meta_by_version = {
            row["workspace_key"][len("atlas.version."):]: (row["payload"] or {})
            for row in self.db.execute(
                "SELECT workspace_key, payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                ("atlas.version.%",),
            ).fetchall()
        }
        ecos_by_version_ref: dict[object, list] = {}
        for row in self.db.execute(
            "SELECT DISTINCT workspace_version_ref, payload->>'eco' AS eco "
            "FROM {oa} WHERE record_type='WS_REPORT' AND workspace_version_ref IS NOT NULL "
            "ORDER BY workspace_version_ref, eco"
        ).fetchall():
            ecos_by_version_ref.setdefault(row["workspace_version_ref"], []).append(row["eco"])

        blocks_by_project_ref: dict[object, list] = {}
        for block in blocks:
            blocks_by_project_ref.setdefault(block["workspace_project_ref"], []).append(block)
        versions_by_block_ref: dict[object, list] = {}
        for version in versions:
            versions_by_block_ref.setdefault(version["workspace_block_ref"], []).append(version)

        empty_por = {"revision": 0, "version_id": None, "assigned_by": None, "assigned_at": None}
        result = []
        for project in self.ws.rows("WS_PROJECT"):
            pid = project["workspace_key"]
            if allowed is not None and pid not in allowed:
                continue
            value = project["payload"]
            item = {
                "id": pid,
                "name": value["name"],
                "description": value.get("description", ""),
                "process": process_by_project.get(pid),
                "status": "completed" if status_by_project.get(pid) == "completed" else "active",
                "can_write": writable is None or pid in writable,
                "blocks": [],
            }
            for block in blocks_by_project_ref.get(project["record_id"], ()):
                bid = block["workspace_key"]
                child = {
                    "id": bid,
                    "name": block["payload"]["name"],
                    "por": por_by_block.get(bid, dict(empty_por)),
                    "versions": [],
                }
                for version in versions_by_block_ref.get(block["record_id"], ()):
                    vp = version["payload"]
                    if vp.get("source_namespace") is None:
                        hydrated = vp
                    else:
                        received = ecos_by_version_ref.get(version["record_id"], [])
                        if vp.get("eco_num") is not None:
                            hydrated = {**vp, "ecos": received}
                        else:
                            hydrated = {**vp, "ecos": received if vp["layout"] == "POST" else [None]}
                    child["versions"].append({**hydrated, **meta_by_version.get(version["workspace_key"], {})})
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
        if (
            type(expected_revision) is not int
            or expected_revision < 0
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 500
        ):
            fail("INVALID_REQUEST")
        project, block, version = self.scope(project_id, block_id, version_id, write=True, manage=self.can_manage)
        actor_info = {"id": self.actor.user_id, "name": getattr(self.actor, "reviewer", None) or self.actor.user_id}
        return self.ws.assign_por_record(
            project,
            block,
            version,
            user_id=self.actor.user_id,
            actor_info=actor_info,
            expected_revision=expected_revision,
            request_id=request_id,
            reason=reason,
            production_lock=True,
        )


__all__ = [
    "AreaAdapters",
    "AreaPreparationError",
    "AtlasCatalog",
    "ensure_area_project_block",
    "fail",
    "resolve_project_sync_metadata",
    "sync_all_area_project_blocks",
    "try_auto_prepare_area_scope",
]
