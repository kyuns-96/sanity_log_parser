"""Request-local Area authorization over the host's locked access snapshot.

Only this adapter knows host IDs. Area UUIDs and the numerical worker remain
independent; an embedded/frozen row never constitutes an authorization grant.
"""
from __future__ import annotations

from uuid import UUID

from common.sanity_common import ReviewError
from area_power.common.errors import DomainError


RECORD_REFERENCES = {
    "parent_id", "previous_id", "version_id", "previous_version_id",
    "baseline_version_id", "current_version_id", "valid_from_version_id",
    "valid_through_version_id", "applies_from_version_id", "applies_through_version_id",
    "block_id", "baseline_block_id", "current_block_id", "snapshot_id",
    "baseline_snapshot_id", "current_snapshot_id", "mapping_id", "target_id",
    "change_id", "comparison_id", "metric_result_id", "ai_run_id", "alert_id",
    "extraction_id", "job_id", "previous_job_id", "explicit_attempt_job_id",
    "rerun_ai_run_id",
}
UPLOAD_REFERENCES = {
    "upload_id", "baseline_upload_id", "current_upload_id", "source_upload_id",
    "revalidated_upload_id",
}
PROJECT_REFERENCES = {"project_id", "baseline_project_id", "current_project_id"}
# mapping_ids in metric evidence are mapping-rule UUIDs, not record references.
RECORD_REFERENCE_LISTS = {"target_ids", "change_ids", "extraction_ids"}
# These contract fields are observations, not links into the application graph.
# Their arbitrary tool/constraint keys may legitimately be named "version_id".
OPAQUE_FIELDS = {
    "analysis_conditions", "input_contract", "root_summary", "validation_report",
    "error_detail", "constraint_context", "structural_metadata",
    "conditions", "current_conditions", "baseline_conditions", "policy",
}


def hidden():
    raise ReviewError("RESOURCE_NOT_FOUND", "Area resource is unavailable")


def identifier(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        hidden()


class AreaAccess:
    def __init__(self, actor):
        self.actor = actor
        self.development = actor.auth_method == "local_token"
        if self.development:
            # Explicit host development authentication only, never SSO fallback.
            # Parent host explicitly selected token development mode. Keep the
            # original unbound Area catalog usable without inventing host grants.
            projects, blocks = [], []
            self.is_admin = False
        else:
            # authorized_actor holds the host shared access lock for the request.
            store = getattr(actor, "access", None)
            if store is None:
                raise ReviewError("AUTHORIZATION_NOT_IMPLEMENTED", "Area access is not configured")
            snapshot = store.snapshot(actor.user_id)
            allowed = {grant["project_id"] for grant in snapshot["grants"]}
            catalog = store.catalog()
            projects = [row for row in catalog["projects"] if row["id"] in allowed]
            blocks = [row for row in catalog["blocks"] if row["project_id"] in allowed]
            self.is_admin = snapshot["is_admin"]
        self.projects = {row["id"]: {"id": row["id"], "name": row["name"], "blocks": []}
                         for row in projects}
        self.blocks = {}
        for row in blocks:
            project = self.projects.get(row["project_id"])
            if project is not None:
                block = {"id": row["id"], "name": row["name"]}
                project["blocks"].append(block)
                self.blocks[row["id"]] = row["project_id"]
        for project in self.projects.values():
            project["blocks"].sort(key=lambda row: row["id"])
        self.version_bindings = {}
        if not self.development:
            from oa.sanity_workspace import record_id
            rows = store.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                                    ("design-atlas-v1.binding.%",)).fetchall()
            for row in rows:
                binding = row["payload"]
                project_id, block_id = binding.get("project_id"), binding.get("block_id")
                if project_id not in self.projects:
                    continue
                if binding.get("namespace") != "design-atlas-v1" or self.blocks.get(block_id) != project_id:
                    hidden()
                version_id = binding.get("version_id")
                native = store.db.execute("SELECT * FROM {oa} WHERE record_type='WS_VERSION' AND workspace_key=%s",
                                          (version_id,)).fetchone()
                if (not native or native["workspace_project_ref"] != record_id("WS_PROJECT", project_id)
                        or native["workspace_block_ref"] != record_id("WS_BLOCK", block_id)
                        or native["payload"].get("project_id") != project_id or native["payload"].get("block_id") != block_id):
                    hidden()
                key = str(identifier(binding.get("area_version_id")))
                self.version_bindings.setdefault(key, []).append(binding)

    def public(self):
        return {"actor": self.actor.user_id, "is_admin": self.is_admin,
                "projects": sorted(self.projects.values(), key=lambda row: row["id"]),
                "local_mode": self.development}

    def host_project(self, value):
        if not isinstance(value, str) or value not in self.projects:
            hidden()
        return value

    def project(self, db, conn, value):
        row = db.record(conn, identifier(value), kind="project")
        return self.host_project(row["payload"].get("sanity_project_id"))

    def block(self, db, conn, value, project_id=None):
        row = db.record(conn, identifier(value), kind="block")
        if project_id is not None and str(row.get("project_id")) != str(project_id):
            hidden()
        host_project = self.project(db, conn, row.get("project_id"))
        host_block = row["payload"].get("sanity_block_id")
        if not isinstance(host_block, str) or self.blocks.get(host_block) != host_project:
            hidden()

    def version(self, db, conn, value, project_id=None, block_id=None):
        row = db.record(conn, identifier(value), kind="version")
        if project_id is not None and str(row.get("project_id")) != str(project_id):
            hidden()
        host_project = self.project(db, conn, row.get("project_id"))
        matches = self.version_bindings.get(str(row["id"]), [])
        for binding in matches:
            if (binding["project_id"] == host_project
                    and identifier(binding["area_project_id"]) == row["project_id"]
                    and (block_id is None or identifier(binding["area_block_id"]) == identifier(block_id))):
                self.block(db, conn, binding["area_block_id"], row["project_id"])
                bound_block = db.record(conn, identifier(binding["area_block_id"]), kind="block")
                if bound_block["payload"].get("sanity_block_id") == binding["block_id"]:
                    return
        hidden()

    def row(self, db, conn, row, seen=None, *, upload=False):
        if self.development:
            return
        seen = set() if seen is None else seen
        key = ("upload" if upload else "record", str(row.get("id")))
        if key in seen:
            return
        seen.add(key)
        if not upload and row.get("record_type") == "project":
            self.host_project(row.get("payload", {}).get("sanity_project_id"))
        elif row.get("project_id") is not None:
            self.project(db, conn, row["project_id"])
        elif not row.get("parent_id") and not row.get("upload_id"):
            hidden()
        if (row.get("block_id") is not None
                and not (row.get("record_type") == "block" and row.get("id") == row["block_id"])):
            self.block(db, conn, row["block_id"], row.get("project_id"))
        # New catalog blocks are checked before their first persistent row exists.
        if not upload and row.get("record_type") == "block":
            host_project = self.project(db, conn, row.get("project_id"))
            host_block = row.get("payload", {}).get("sanity_block_id")
            if not isinstance(host_block, str) or self.blocks.get(host_block) != host_project:
                hidden()
        if not upload and row.get("record_type") == "version":
            self.version(db, conn, row.get("id"), row.get("project_id"))
        if row.get("version_id") is not None and row.get("block_id") is not None:
            self.version(db, conn, row["version_id"], row.get("project_id"), row["block_id"])
        self.value(db, conn, row, seen, self_id=row.get("id"))

    def value(self, db, conn, value, seen=None, *, self_id=None):
        if self.development:
            return
        seen = set() if seen is None else seen
        if isinstance(value, (list, tuple)):
            for item in value:
                self.value(db, conn, item, seen)
            return
        if not isinstance(value, dict):
            return
        if value.get("version_id") is not None and value.get("block_id") is not None:
            self.version(db, conn, value["version_id"], value.get("project_id"), value["block_id"])
        # Frozen catalog copies retain their immutable content. Their IDs must
        # resolve to an explicitly approved current binding, never act as grants.
        if value.get("record_type") == "project":
            self.project(db, conn, value.get("id"))
        elif value.get("record_type") == "block":
            self.block(db, conn, value.get("id"), value.get("project_id"))
        elif value.get("record_type") == "version":
            self.version(db, conn, value.get("id"), value.get("project_id"))
        for name, item in value.items():
            if item is None or name in OPAQUE_FIELDS:
                continue
            if name in PROJECT_REFERENCES:
                self.project(db, conn, item)
            elif name in RECORD_REFERENCES:
                reference = identifier(item)
                if self_id is None or reference != identifier(self_id):
                    row = db.record(conn, reference)
                    self.row(db, conn, row, seen)
            elif name in UPLOAD_REFERENCES:
                row = db.upload(conn, identifier(item))
                self.row(db, conn, row, seen, upload=True)
            elif name in RECORD_REFERENCE_LISTS:
                if not isinstance(item, (list, tuple)):
                    hidden()
                for reference in item:
                    self.row(db, conn, db.record(conn, identifier(reference)), seen)
            else:
                self.value(db, conn, item, seen)


class AuthorizedDatabase:
    """Apply current access on reads, nested references, and transactional writes."""
    def __init__(self, db, access):
        self.raw, self.access = db, access

    def connect(self):
        return self.raw.connect()

    def table(self, name):
        return self.raw.table(name)

    def authorize(self, conn, row, *, upload=False):
        self.access.row(self.raw, conn, row, upload=upload)
        return row

    def references(self, conn, value):
        self.access.value(self.raw, conn, value)

    def record(self, conn, value, **kwargs):
        return self.authorize(conn, self.raw.record(conn, value, **kwargs))

    def latest_record(self, conn, kind, entity_id, **kwargs):
        return self.authorize(conn, self.raw.latest_record(conn, kind, entity_id, **kwargs))

    def upload(self, conn, value, **kwargs):
        return self.authorize(conn, self.raw.upload(conn, value, **kwargs), upload=True)

    def _visible(self, conn, loader, args, filters, limit, offset, *, upload=False):
        self.raw._page(limit, offset)
        self.references(conn, filters)
        items, skipped, scanned = [], 0, 0
        while True:
            rows = loader(conn, *args, **filters, limit=500, offset=scanned)
            for row in rows:
                try:
                    self.authorize(conn, row, upload=upload)
                except ReviewError as exc:
                    if exc.code != "RESOURCE_NOT_FOUND":
                        raise
                    continue
                except DomainError as exc:
                    if exc.code != "not_found":
                        raise
                    continue
                if skipped < offset:
                    skipped += 1
                else:
                    items.append(row)
                    if len(items) == limit:
                        return items
            if len(rows) < 500:
                return items
            scanned += len(rows)

    def records(self, conn, record_type=None, *, limit=100, offset=0, **filters):
        return self._visible(conn, self.raw.records, (record_type,), filters, limit, offset)

    def uploads(self, conn, *, limit=100, offset=0, **filters):
        return self._visible(conn, self.raw.uploads, (), filters, limit, offset, upload=True)

    def insert_record(self, conn, record_type, payload, actor, **fields):
        row = {**fields, "record_type": record_type, "payload": payload}
        self.authorize(conn, row)
        return self.raw.insert_record(conn, record_type, payload, actor, **fields)

    def update_record(self, conn, value, actor, **fields):
        previous = self.record(conn, value)
        self.authorize(conn, {**previous, **fields})
        return self.raw.update_record(conn, value, actor, **fields)

    def enqueue(self, conn, job_kind, **fields):
        self.authorize(conn, {"record_type": "job", **fields})
        row = self.raw.enqueue(conn, job_kind, **fields)
        return self.authorize(conn, row)
