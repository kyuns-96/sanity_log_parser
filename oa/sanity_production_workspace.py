"""Production SSO authority for the shared report, clustering and review stores.

The caller owns a fresh READ COMMITTED transaction and its shared access_lock.
No synthetic account, session or embedded project membership is consulted.
"""
from __future__ import annotations

from common.sanity_common import ReviewError
from common.sanity_db import lock_key, uuid_value
from oa.sanity_access import AccessStore
from oa.sanity_workspace import WorkspaceStore, record_id


class ProductionWorkspaceStore(WorkspaceStore):
    case_policy = "OA_RETRIEVAL_V1"

    def __init__(self, db, access: AccessStore):
        if not isinstance(access, AccessStore) or access.db is not db:
            raise ReviewError("AUTHORIZATION_CONFIG", "Workspace authority must share its transaction")
        super().__init__(db)
        self.authorization = access

    def login(self, username, password):
        raise ReviewError("FORBIDDEN", "Production login uses the SSO authentication service")

    def session(self, token):
        raise ReviewError("FORBIDDEN", "Production sessions use the SSO authentication service")

    def logout(self, token):
        raise ReviewError("FORBIDDEN", "Production logout uses the SSO authentication service")

    def user(self, user_id):
        self.authorization.require_ready()
        identity = self.authorization._identity(user_id)
        grants = self.authorization.snapshot(user_id)["grants"]
        roles = {grant["role"] for grant in grants}
        role = "OWNER" if "OWNER" in roles else "TOP" if "TOP" in roles else "USER"
        return {"id": user_id, "name": identity["display_name"], "username": user_id, "role": role}

    def access(self, user_id, block_id, *, write=False):
        self.authorization.require_ready()
        readable = self.authorization._scopes(user_id)
        scope = next((value for value in readable if value["block_id"] == block_id), None)
        if scope is None:
            raise ReviewError("RESOURCE_NOT_FOUND", "Block is not available")
        if write and scope not in self.authorization._scopes(user_id, write=True):
            raise ReviewError("FORBIDDEN", "Block ownership is required")
        block = self.get("WS_BLOCK", scope["block_id"])
        project = self.get("WS_PROJECT", scope["project_id"])
        if (block["record_id"] != record_id("WS_BLOCK", block_id)
                or project["record_id"] != record_id("WS_PROJECT", scope["project_id"])
                or block["workspace_project_ref"] != project["record_id"]
                or block["payload"]["project_id"] != project["workspace_key"]):
            raise ReviewError("STATE_CORRUPT", "Registered workspace scope differs")
        return block, project

    def can_write(self, user_id, block):
        block_id = block["workspace_key"]
        self.access(user_id, block_id)
        return any(scope["block_id"] == block_id
                   for scope in self.authorization._scopes(user_id, write=True))

    def case_reference_scopes(self, user_id):
        """Global registered confirmed knowledge, independent of raw grants."""
        self.authorization.require_ready()
        self.authorization._identity(user_id)
        registry = self.authorization._state()
        registered = {(scope["project_id"], scope["block_id"]) for scope in self.registered_case_scopes()}
        expected = {(block["project_id"], block["id"]) for block in registry["blocks"].values()}
        if expected != registered:
            raise ReviewError("STATE_CORRUPT", "Registered workspace scope differs")
        return [{"project_id": project, "block_id": block} for project, block in sorted(expected)]

    def por(self, block_id):
        row = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_POR' AND workspace_key=%s",
                              (block_id,)).fetchone()
        if row is None:
            return {"revision": 0, "version": None, "assigned_by": None, "assigned_at": None}
        block = self.get("WS_BLOCK", block_id)
        if (row["record_id"] != record_id("WS_POR", block_id)
                or row["workspace_block_ref"] != block["record_id"]
                or row["workspace_project_ref"] != block["workspace_project_ref"]):
            raise ReviewError("STATE_CORRUPT", "PoR block scope differs")
        return super().por(block_id)

    def versions(self, user_id, block_id):
        block, _ = self.access(user_id, block_id)
        rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_VERSION' AND workspace_block_ref=%s "
                               "ORDER BY payload->>'net_ver' DESC,payload->>'layout' DESC",
                               (block["record_id"],)).fetchall()
        return {"items": [self.version_ecos(row) for row in rows], "por": self.por(block_id),
                "can_assign_por": self.can_write(user_id, block)}

    def overview(self, user_id):
        from oa.sanity_workspace_sources import CURRENT_SOURCES_SQL

        user = self.user(user_id)
        readable = self.authorization._scopes(user_id)
        readable_blocks = {scope["block_id"] for scope in readable}
        writable_blocks = {scope["block_id"] for scope in self.authorization._scopes(user_id, write=True)}
        registry = self.authorization._state()
        people = {}

        def assigned_people(role, project_id, block_id=None):
            result = []
            members = sorted({grant["user_id"] for grant in registry["grants"].values()
                              if grant["role"] == role and grant["project_id"] == project_id
                              and grant["block_id"] == block_id})
            for member in members:
                if member not in people:
                    try:
                        people[member] = self.user(member)
                    except ReviewError as error:
                        if error.code != "ACCOUNT_DISABLED":
                            raise
                        people[member] = None
                if people[member] is not None:
                    result.append(people[member])
            return result

        projects = []
        for project in sorted(registry["projects"].values(), key=lambda value: value["id"]):
            blocks = []
            for value in sorted(registry["blocks"].values(), key=lambda value: value["id"]):
                if value["project_id"] != project["id"] or value["id"] not in readable_blocks:
                    continue
                block_id = value["id"]
                por = self.por(block_id)
                report_version = por["version"]
                if report_version is None:
                    received = self.db.execute("SELECT v.* FROM {oa} v WHERE v.record_type='WS_VERSION' "
                        "AND v.workspace_block_ref=%s AND EXISTS (SELECT 1 FROM {oa} r WHERE r.record_type='WS_REPORT' "
                        "AND r.workspace_version_ref=v.record_id) ORDER BY v.payload->>'net_ver' DESC,v.created_at DESC LIMIT 1",
                        (record_id("WS_BLOCK", block_id),)).fetchone()
                    report_version = self.version_ecos(received) if received else None
                rows = self.db.execute("SELECT payload->>'check_status' AS check_status,count(*) AS count FROM {oa} "
                    "WHERE record_type='WS_REPORT' AND workspace_block_ref=%s AND workspace_version_ref=%s "
                    "AND " + CURRENT_SOURCES_SQL + " GROUP BY payload->>'check_status'",
                    (record_id("WS_BLOCK", block_id),
                     record_id("WS_VERSION", report_version["id"]) if report_version else None)).fetchall()
                counts = {row["check_status"]: row["count"] for row in rows}
                blocks.append({**value, "owners": assigned_people("OWNER", project["id"], block_id),
                               "can_assign_por": block_id in writable_blocks, "por": por,
                               "report_version": report_version, "counts": counts, "total": sum(counts.values())})
            if blocks:
                projects.append({**project, "tops": assigned_people("TOP", project["id"]), "blocks": blocks})
        return {"user": user, "projects": projects, "data_origin": "PRODUCTION_SSO"}

    def assign_por(self, user_id, block_id, version_id, expected_revision, request_id, reason):
        block, project = self.access(user_id, block_id, write=True)
        self.version(user_id, block_id, version_id)
        request_id = str(uuid_value(request_id))
        if (type(expected_revision) is not int or expected_revision < 0
                or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500):
            raise ReviewError("INVALID_REQUEST", "Version, expected revision and reason are required")
        self.db.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key("production-por-" + block_id),))
        exists = self.db.execute("SELECT 1 FROM {oa} WHERE record_type='WS_POR' AND workspace_key=%s",
                                 (block_id,)).fetchone()
        if not exists:
            if expected_revision != 0:
                raise ReviewError("REVISION_CONFLICT", "PoR changed; reload the current version")
            self.put("WS_POR", block_id, {"block_id": block_id},
                     project=project["workspace_key"], block=block["workspace_key"])
        return super().assign_por(user_id, block_id, version_id, expected_revision, request_id, reason)
