"""Project membership policy with immutable pre-policy grant/receipt fixtures."""
from contextlib import contextmanager
from uuid import uuid4

from common.sanity_common import ReviewError
from common.sanity_db import Database, json_value, stable_uuid
from oa.sanity_access import AccessStore, PREFIX, access_lock
from tests.support import PgTestCase, ROOT
from tests.test_access_store import ADMIN, OWNER, TOP, OTHER


class ProjectMembershipTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ("008_submissions.sql", "009_sso_identity.sql"):
            self.admin.execute((ROOT / "db" / name).read_text().replace("public.", self.schema + "."))
        timestamp = "2026-10-22T00:00:00.000000Z"
        for actor in (ADMIN, OWNER, TOP, OTHER):
            self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload,state) "
                "VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",
                (stable_uuid("sso-identity-v1", "SSO_IDENTITY", actor.user_id), actor.user_id,
                 json_value({"issuer": "https://synthetic.example.test/idp", "subject": actor.user_id,
                             "display_name": actor.reviewer, "first_seen_at": timestamp}),
                 json_value({"enabled": True, "last_seen_at": timestamp})))
        for project in ("project.A", "project.B", "project.empty"):
            self.call("create_project", ADMIN, uuid4(), project, "Synthetic " + project, exclusive=True)
        for project, block in (("project.A", "block.A1"), ("project.A", "block.A2"), ("project.B", "block.B1")):
            self.call("create_block", ADMIN, uuid4(), project, block, "Synthetic " + block, exclusive=True)

    @contextmanager
    def store(self, *, exclusive=False):
        with Database("oa") as db, db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED " + ("READ WRITE" if exclusive else "READ ONLY"))
            access_lock(db, exclusive=exclusive)
            yield AccessStore(db, ADMIN.user_id)

    def call(self, method, *args, exclusive=False, **kwargs):
        with self.store(exclusive=exclusive) as store:
            return getattr(store, method)(*args, **kwargs)

    def assert_code(self, code, callback):
        with self.assertRaises(ReviewError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def legacy_event(self, kind, actor, data):
        """Seed a frozen valid old ledger, never bypass a live mutation guard."""
        revision = self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CONFIG' "
                                    "AND workspace_key LIKE %s", (PREFIX + "%",)).fetchone()[0] + 1
        command_id = str(uuid4())
        key = PREFIX + command_id
        event = {"namespace": "oa-rbac-v1", "schema_version": 1, "revision": revision,
                 "kind": kind, "command_id": command_id, "actor_id": actor.user_id,
                 "created_at": "2026-10-22T00:00:00.000000Z", "data": data}
        self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload) "
                         "VALUES (%s,'WS_CONFIG',%s,%s)",
                         (stable_uuid("workspace-v1", "WS_CONFIG", key), key, json_value(event)))
        return command_id, revision

    def legacy_grant(self, actor, role, project="project.A", block=None):
        request_id, revision = self.legacy_event("REQUEST", actor,
            {"role": role, "project_id": project, "block_id": block, "reason": "Frozen synthetic request"})
        self.legacy_event("DECISION", ADMIN,
            {"request_id": request_id, "decision": "APPROVE", "reason": "Frozen synthetic approval", "expected_revision": revision})
        return request_id

    def grant(self, actor, project="project.A"):
        request_id = str(uuid4())
        requested = self.call("request", actor, request_id, "MEMBER", project, None, "Synthetic project request", exclusive=True)
        self.call("decide", ADMIN, request_id, uuid4(), "APPROVE", "Synthetic approval", requested["revision"], exclusive=True)
        return request_id

    def test_old_grants_collapse_and_one_project_revocation_removes_all_anchors_atomically(self):
        ids = [self.legacy_grant(OWNER, "TOP"), self.legacy_grant(OWNER, "OWNER", block="block.A1"),
               self.legacy_grant(OWNER, "OWNER", block="block.A2")]
        other_project = self.legacy_grant(OWNER, "TOP", project="project.B")
        other_person = self.legacy_grant(TOP, "OWNER", block="block.A1")
        before = dict(self.admin_query("SELECT workspace_key,payload FROM {oa} WHERE record_type='WS_CONFIG'").fetchall())
        grants = {grant["project_id"]: grant for grant in self.call("snapshot", OWNER.user_id)["grants"]}
        self.assertEqual({"project.A", "project.B"}, set(grants))
        self.assertEqual(min(ids), grants["project.A"]["grant_id"])
        self.assertTrue(all(g["role"] == "MEMBER" and g["block_id"] is None for g in grants.values()))
        self.assertEqual(3, len(self.call("admin_snapshot", ADMIN)["grants"]))
        self.assertEqual({"block.A1", "block.A2"}, {s["block_id"] for s in self.call("_scopes", TOP.user_id, write=True)})
        revision = self.call("snapshot", OWNER.user_id)["revision"]
        command = str(uuid4())
        self.assert_code("REVISION_CONFLICT", lambda: self.call("revoke", ADMIN, min(ids), command,
            "Synthetic project removal", revision - 1, exclusive=True))
        self.assert_code("FORBIDDEN", lambda: self.call("revoke", OTHER, min(ids), command,
            "Synthetic project removal", revision, exclusive=True))
        result = self.call("revoke", ADMIN, min(ids), command, "Synthetic project removal", revision, exclusive=True)
        self.assertEqual(revision + 1, result["revision"])
        repeated = self.call("revoke", ADMIN, min(ids), command, "Synthetic project removal", revision, exclusive=True)
        self.assertEqual(result, repeated)
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("revoke", ADMIN, min(ids), command,
            "Changed content", revision, exclusive=True))
        self.assertEqual([other_project], [g["grant_id"] for g in self.call("snapshot", OWNER.user_id)["grants"]])
        self.assertEqual([other_person], [g["grant_id"] for g in self.call("snapshot", TOP.user_id)["grants"]])
        self.assertEqual({"block.B1"}, {s["block_id"] for s in self.call("_scopes", OWNER.user_id, write=True)})
        after = dict(self.admin_query("SELECT workspace_key,payload FROM {oa} WHERE record_type='WS_CONFIG'").fetchall())
        self.assertEqual(len(before) + 1, len(after))
        self.assertTrue(all(after[key] == value for key, value in before.items()))
        self.assertEqual("REVOKE_PROJECT", after[PREFIX + command]["kind"])

    def test_new_member_inherits_future_blocks_and_does_not_grant_other_projects_or_admin_access(self):
        self.grant(OWNER, "project.empty")
        self.assertTrue(self.call("can_read_business", OWNER.user_id))
        self.assertTrue(self.call("can_write_business", OWNER.user_id))
        self.assertEqual([], self.call("_scopes", OWNER.user_id, write=True))
        self.call("create_block", ADMIN, uuid4(), "project.empty", "block.future", "Synthetic future block", exclusive=True)
        self.assertEqual([{"schema_version": 1, "project_id": "project.empty", "block_id": "block.future"}],
                         self.call("_scopes", OWNER.user_id, write=True))
        self.assertEqual(self.call("_scopes", OWNER.user_id), self.call("_scopes", OWNER.user_id, write=True))
        self.assertFalse(self.call("can_read_business", OTHER.user_id))
        self.assertFalse(self.call("can_write_business", ADMIN.user_id))
        self.assert_code("FORBIDDEN", lambda: self.call("admin_snapshot", OWNER))
        self.admin_query("UPDATE {oa} SET state=jsonb_set(state,'{{enabled}}','false') "
                         "WHERE record_type='SSO_IDENTITY' AND workspace_key=%s", (OWNER.user_id,))
        self.assert_code("ACCOUNT_DISABLED", lambda: self.call("can_write_business", OWNER.user_id))

    def test_member_scope_duplicates_and_legacy_scope_validation_are_strict(self):
        for role, project, block, code in (("MEMBER", "project.A", "block.A1", "INVALID_REQUEST"),
                ("MEMBER", "project.missing", None, "RESOURCE_NOT_FOUND"),
                ("OWNER", "project.A", "block.B1", "RESOURCE_NOT_FOUND"),
                ("TOP", "project.A", "block.A1", "INVALID_REQUEST"),
                ("ADMIN", "project.A", None, "INVALID_REQUEST")):
            with self.subTest(role=role, project=project, block=block):
                self.assert_code(code, lambda: self.call("request", OWNER, uuid4(), role, project, block,
                                                        "Synthetic request", exclusive=True))
        request_id = str(uuid4())
        pending = self.call("request", OWNER, request_id, "MEMBER", "project.A", None, "Synthetic request", exclusive=True)
        self.assertEqual(pending, self.call("request", OWNER, request_id, "MEMBER", "project.A", None,
                                           "Synthetic request", exclusive=True))
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("request", OWNER, uuid4(), "OWNER", "project.A",
            "block.A2", "Duplicate project", exclusive=True))
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("request", OWNER, request_id, "MEMBER", "project.B",
            None, "Synthetic request", exclusive=True))
        self.call("decide", ADMIN, request_id, uuid4(), "APPROVE", "Synthetic approval", pending["revision"], exclusive=True)
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("request", OWNER, uuid4(), "TOP", "project.A", None,
            "Duplicate legacy shape", exclusive=True))
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("request", OWNER, uuid4(), "MEMBER", "project.A", None,
            "Duplicate member", exclusive=True))

    def test_old_revoke_replays_one_grant_and_its_exact_receipt_remains_idempotent(self):
        old = self.legacy_grant(OWNER, "TOP")
        survivor = self.legacy_grant(OWNER, "OWNER", block="block.A1")
        revision = self.call("snapshot", OWNER.user_id)["revision"]
        command, _ = self.legacy_event("REVOKE", ADMIN,
            {"grant_id": old, "reason": "Frozen revoke", "expected_revision": revision})
        before = self.call("admin_snapshot", ADMIN)
        self.assertEqual([survivor], [g["grant_id"] for g in self.call("snapshot", OWNER.user_id)["grants"]])
        self.assertEqual(before, self.call("revoke", ADMIN, old, command, "Frozen revoke", revision, exclusive=True))
        self.assertEqual(2, len(self.call("_scopes", OWNER.user_id, write=True)))
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("revoke", ADMIN, old, command,
            "Changed frozen receipt", revision, exclusive=True))

    def test_old_pending_block_request_cannot_duplicate_an_existing_project_membership(self):
        grant = self.legacy_grant(OWNER, "TOP")
        request_id, revision = self.legacy_event("REQUEST", OWNER,
            {"role": "OWNER", "project_id": "project.A", "block_id": "block.A2", "reason": "Frozen pending block request"})
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("decide", ADMIN, request_id, uuid4(), "APPROVE",
            "Already a project member", revision, exclusive=True))
        result = self.call("decide", ADMIN, request_id, uuid4(), "REJECT", "Duplicate project membership",
                           revision, exclusive=True)
        self.assertEqual([grant], [g["grant_id"] for g in self.call("snapshot", OWNER.user_id)["grants"]])
        request = next(r for r in result["requests"] if r["request_id"] == request_id)
        self.assertEqual("REJECTED", request["status"])
