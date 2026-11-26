"""Production SSO authority for the shared report, clustering and review stores.

The caller owns a fresh READ COMMITTED transaction and its shared access_lock.
No synthetic account, session or embedded project membership is consulted.
"""
from __future__ import annotations

from common.sanity_common import ReviewError
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
        identity = self.authorization.active_identity(user_id)
        grants = self.authorization.snapshot(user_id)["grants"]
        role = "MEMBER" if grants else "USER"
        return {"id": user_id, "name": identity["display_name"], "username": user_id, "role": role}

    def access(self, user_id, block_id, *, write=False):
        self.authorization.require_ready()
        readable = self.authorization.allowed_scopes(user_id)
        scope = next((value for value in readable if value["block_id"] == block_id), None)
        if scope is None:
            raise ReviewError("RESOURCE_NOT_FOUND", "Block is not available")
        if write and scope not in self.authorization.allowed_scopes(user_id, write=True):
            raise ReviewError("FORBIDDEN", "Project membership is required")
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
                   for scope in self.authorization.allowed_scopes(user_id, write=True))

    def case_reference_scopes(self, user_id):
        """Global registered confirmed knowledge, independent of raw grants."""
        self.authorization.require_ready()
        self.authorization.active_identity(user_id)
        registry = self.authorization.registry_state()
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

    def overview(self, user_id):
        user = self.user(user_id)
        readable = self.authorization.allowed_scopes(user_id)
        readable_blocks = {scope["block_id"] for scope in readable}
        writable_blocks = {scope["block_id"] for scope in self.authorization.allowed_scopes(user_id, write=True)}
        registry = self.authorization.registry_state()

        projects = []
        for project in sorted(registry["projects"].values(), key=lambda value: value["id"]):
            blocks = []
            for value in sorted(registry["blocks"].values(), key=lambda value: value["id"]):
                if value["project_id"] != project["id"] or value["id"] not in readable_blocks:
                    continue
                block_id = value["id"]
                por = self.por(block_id)
                report_version, counts, total = self._block_report_summary(record_id("WS_BLOCK", block_id), por)
                # Keep the response shape for older dashboards without enumerating
                # everyone who can access this project on each block card.
                blocks.append({**value, "owners": [],
                               "can_assign_por": block_id in writable_blocks, "por": por,
                               "report_version": report_version, "counts": counts, "total": total})
            projects.append({**project, "tops": [], "blocks": blocks})
        return {"user": user, "projects": projects, "data_origin": "PRODUCTION_SSO"}

    def assign_por(self, user_id, block_id, version_id, expected_revision, request_id, reason):
        block, project = self.access(user_id, block_id, write=True)
        version = self.version(user_id, block_id, version_id)
        return self.assign_por_record(
            project,
            block,
            version,
            user_id=user_id,
            expected_revision=expected_revision,
            request_id=request_id,
            reason=reason,
            production_lock=True,
        )
