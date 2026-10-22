"""Small, real-PostgreSQL coverage of production permissions and immutable audit."""
from contextlib import contextmanager
from copy import deepcopy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from starlette.requests import Request

from common.sanity_common import ReviewError
from common.sanity_db import Database, json_value, stable_uuid
from oa.sanity_access import AccessStore, PREFIX, access_lock, administrator_id, require_mutation_request
from oa.sanity_auth import Actor
from tests.support import PgTestCase, ROOT

ADMIN = Actor("synthetic.admin", "합성 관리자", "sso")
OWNER = Actor("synthetic.owner", "합성 블록 담당자", "sso")
TOP = Actor("synthetic.top", "합성 프로젝트 담당자", "sso")
OTHER = Actor("synthetic.other", "합성 다른 사용자", "sso")


class AccessConfigurationTests(unittest.TestCase):
    def assert_code(self, code, callback):
        with self.assertRaises(ReviewError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def test_optional_administrator_id_is_exact_and_not_normalized(self):
        self.assertIsNone(administrator_id({}))
        self.assertIsNone(administrator_id({"SANITY_ADMIN_USER_ID": ""}))
        self.assertEqual("Case.Sensitive", administrator_id({"SANITY_ADMIN_USER_ID": "Case.Sensitive"}))
        for value in (" ", "user ", "user\n", 1, ["admin"], "x" * 101):
            self.assert_code("AUTHORIZATION_CONFIG", lambda: administrator_id({"SANITY_ADMIN_USER_ID": value}))

    def test_mutation_guard_rejects_cross_site_and_forged_origin(self):
        def request(**headers):
            return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/access/requests",
                            "query_string": b"", "headers": [(k.replace("_", "-").encode(), v.encode())
                                for k, v in {"host": "sanity.example.test", **headers}.items()]})
        require_mutation_request(request(x_sanity_request="1"))
        require_mutation_request(request(x_sanity_request="1", origin="https://sanity.example.test", sec_fetch_site="same-origin"))
        for changed in ({}, {"x_sanity_request": "0"}, {"x_sanity_request": "1", "origin": "null"},
                        {"x_sanity_request": "1", "origin": "https://other.example.test"},
                        {"x_sanity_request": "1", "origin": "https://sanity.example.test/path"},
                        {"x_sanity_request": "1", "sec_fetch_site": "cross-site"},
                        {"x_sanity_request": "1", "sec_fetch_site": "same-site"}):
            self.assert_code("FORBIDDEN", lambda: require_mutation_request(request(**changed)))


class AccessStoreTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ("008_submissions.sql", "009_sso_identity.sql"):
            self.admin.execute((ROOT / "db" / name).read_text().replace("public.", self.schema + "."))
        for actor in (ADMIN, OWNER, TOP, OTHER):
            self.identity(actor)

    def identity(self, actor, enabled=True):
        timestamp = "2026-10-22T00:00:00.000000Z"
        self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload,state) "
                         "VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",
                         (stable_uuid("sso-identity-v1", "SSO_IDENTITY", actor.user_id), actor.user_id,
                          json_value({"issuer": "https://synthetic.example.test/idp", "subject": actor.user_id,
                                      "display_name": actor.reviewer, "first_seen_at": timestamp}),
                          json_value({"enabled": enabled, "last_seen_at": timestamp})))

    @contextmanager
    def store(self, *, write=False, admin_id=ADMIN.user_id):
        with Database("oa") as db, db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED " + ("READ WRITE" if write else "READ ONLY"))
            access_lock(db, exclusive=write)
            yield AccessStore(db, admin_id)

    def call(self, method, *args, exclusive=False, **kwargs):
        with self.store(write=exclusive) as store:
            return getattr(store, method)(*args, **kwargs)

    def assert_code(self, code, callback):
        with self.assertRaises(ReviewError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def registry(self):
        self.call("create_project", ADMIN, uuid4(), "project.A", "합성 프로젝트 A", exclusive=True)
        self.call("create_project", ADMIN, uuid4(), "project.B", "합성 프로젝트 B", exclusive=True)
        for project, block in (("project.A", "block.A1"), ("project.A", "block.A2"), ("project.B", "block.B1")):
            self.call("create_block", ADMIN, uuid4(), project, block, "합성 " + block, exclusive=True)

    def grant(self, actor, role, project="project.A", block=None):
        request_id = uuid4()
        result = self.call("request", actor, request_id, role, project, block, "합성 업무 배정 요청", exclusive=True)
        self.call("decide", ADMIN, request_id, uuid4(), "APPROVE", "합성 승인 근거", result["revision"], exclusive=True)
        return str(request_id)

    def bundle(self, scope, suffix):
        bundle_id = "b-" + suffix * 64
        self.admin_query("INSERT INTO {oa}(record_id,record_type,project,run_id,bundle_id,payload) "
                         "VALUES (%s,'RECEIVED_BUNDLE','same-free-text','run-1',%s,%s)",
                         (uuid4(), bundle_id, json_value({"bundle": {"run": {"scope": scope}}})))
        return bundle_id

    def test_new_accounts_and_admin_have_no_implicit_business_access(self):
        before = self.admin_query("SELECT record_id,payload,state,version,created_at,updated_at FROM {oa} "
                                  "WHERE record_type='SSO_IDENTITY' ORDER BY workspace_key").fetchall()
        self.registry()
        for actor in (ADMIN, OWNER):
            self.call("require_actor", actor)
            snapshot = self.call("snapshot", actor.user_id)
            self.assertEqual([], snapshot["grants"])
            self.assertEqual([], snapshot["requests"])
            self.assertFalse(self.call("can_read_business", actor.user_id))
            self.assertFalse(self.call("can_write_business", actor.user_id))
        self.assertTrue(self.call("snapshot", ADMIN.user_id)["is_admin"])
        self.assertEqual(2, len(self.call("catalog")["projects"]))
        self.assert_code("FORBIDDEN", lambda: self.call("admin_snapshot", OWNER))
        self.assert_code("FORBIDDEN", lambda: self.call("require_actor", Actor(ADMIN.user_id, "forged", "local_token")))
        with self.store(admin_id=None) as store:
            self.assert_code("AUTHORIZATION_NOT_IMPLEMENTED", lambda: store.require_actor(ADMIN))
        after = self.admin_query("SELECT record_id,payload,state,version,created_at,updated_at FROM {oa} "
                                 "WHERE record_type='SSO_IDENTITY' ORDER BY workspace_key").fetchall()
        self.assertEqual(before, after)

    def test_registry_retries_are_exact_and_parent_ids_remain_case_sensitive(self):
        command = uuid4()
        first = self.call("create_project", ADMIN, command, "ProjectA", "합성 프로젝트", "설명", exclusive=True)
        retry = self.call("create_project", ADMIN, command, "ProjectA", "합성 프로젝트", "설명", exclusive=True)
        self.assertEqual(first["revision"], retry["revision"])
        self.assertEqual("ALREADY_CREATED", retry["status"])
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("create_project", ADMIN, command, "ProjectA", "다른 이름", "설명", exclusive=True))
        self.assert_code("FORBIDDEN", lambda: self.call("create_project", OWNER, uuid4(), "other", "다른 프로젝트", exclusive=True))
        self.assert_code("RESOURCE_NOT_FOUND", lambda: self.call("create_block", ADMIN, uuid4(), "projecta", "block1", "블록", exclusive=True))
        self.call("create_block", ADMIN, uuid4(), "ProjectA", "block1", "블록", exclusive=True)
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("create_block", ADMIN, uuid4(), "ProjectA", "block1", "블록", exclusive=True))
        with self.store() as store:
            rows = store.db.execute("SELECT record_type,payload,state,version,created_at,updated_at FROM {oa} "
                                    "WHERE record_type IN ('WS_CONFIG','WS_PROJECT','WS_BLOCK')").fetchall()
            for row in rows:
                self.assertEqual({}, row["state"])
                self.assertEqual(0, row["version"])
                self.assertEqual(row["created_at"], row["updated_at"])
                if row["record_type"] == "WS_PROJECT":
                    self.assertEqual([], row["payload"]["tops"])
                if row["record_type"] == "WS_BLOCK":
                    self.assertEqual([], row["payload"]["owners"])

    def test_top_and_owner_have_sql_scoped_lists_and_exact_bundle_permissions(self):
        self.registry()
        self.grant(OWNER, "OWNER", block="block.A1")
        self.grant(TOP, "TOP")
        a1 = self.bundle({"schema_version": 1, "project_id": "project.A", "block_id": "block.A1"}, "a")
        a2 = self.bundle({"schema_version": 1, "project_id": "project.A", "block_id": "block.A2"}, "b")
        b1 = self.bundle({"schema_version": 1, "project_id": "project.B", "block_id": "block.B1"}, "c")
        malformed = [None, {"schema_version": 1, "project_id": "project.A", "block_id": "block.B1"},
                     {"schema_version": 1, "project_id": "project.A", "block_id": "block.A1", "extra": True},
                     {"schema_version": True, "project_id": "project.A", "block_id": "block.A1"},
                     {"schema_version": 1.0, "project_id": "project.A", "block_id": "block.A1"}]
        for scope, suffix in zip(malformed, "def01"):
            self.bundle(scope, suffix)
        for actor, expected in ((OWNER, [a1]), (TOP, [a1, a2]), (OTHER, []), (ADMIN, [])):
            with self.store() as store:
                predicate, params = store.read_predicate(actor.user_id)
                rows = store.db.execute("SELECT r.bundle_id FROM {oa} r WHERE r.record_type='RECEIVED_BUNDLE' AND "
                                        + predicate + " ORDER BY r.bundle_id", params).fetchall()
                self.assertEqual(expected, [r["bundle_id"] for r in rows])
                count = store.db.execute("SELECT count(*) AS n FROM {oa} r WHERE r.record_type='RECEIVED_BUNDLE' AND "
                                         + predicate, params).fetchone()["n"]
                self.assertEqual(len(expected), count)
        self.assertEqual("block.A1", self.call("require_bundle", OWNER.user_id, a1, write=True)["block_id"])
        self.assertEqual("block.A2", self.call("require_bundle", TOP.user_id, a2)["block_id"])
        self.assert_code("FORBIDDEN", lambda: self.call("require_bundle", TOP.user_id, a2, write=True))
        self.assert_code("RESOURCE_NOT_FOUND", lambda: self.call("require_bundle", OWNER.user_id, a2))
        self.assert_code("RESOURCE_NOT_FOUND", lambda: self.call("require_bundle", TOP.user_id, b1))
        self.assert_code("RESOURCE_NOT_FOUND", lambda: self.call("require_bundle", OWNER.user_id, "b-" + "d" * 64))
        self.assert_code("AUTHORIZATION_CONFIG", lambda: self.call("read_predicate", OWNER.user_id, "r);DROP"))

    def test_approval_self_approval_rejection_conflicts_and_revocation(self):
        self.registry()
        request_id, command_id = uuid4(), uuid4()
        requested = self.call("request", ADMIN, request_id, "OWNER", "project.A", "block.A1", "본인 업무 배정", exclusive=True)
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("request", ADMIN, uuid4(), "OWNER", "project.A", "block.A1", "중복 요청", exclusive=True))
        self.assert_code("REVISION_CONFLICT", lambda: self.call("decide", ADMIN, request_id, command_id, "APPROVE", "승인", 0, exclusive=True))
        approved = self.call("decide", ADMIN, request_id, command_id, "APPROVE", "승인", requested["revision"], exclusive=True)
        repeated = self.call("decide", ADMIN, request_id, command_id, "APPROVE", "승인", requested["revision"], exclusive=True)
        self.assertEqual(approved, repeated)
        self.assertTrue(self.call("can_write_business", ADMIN.user_id))
        self.assert_code("REQUEST_CONFLICT", lambda: self.call("decide", ADMIN, request_id, command_id, "REJECT", "변경", requested["revision"], exclusive=True))
        self.call("revoke", ADMIN, request_id, uuid4(), "업무 종료", approved["revision"], exclusive=True)
        self.assertFalse(self.call("can_read_business", ADMIN.user_id))
        history = self.call("snapshot", ADMIN.user_id)
        self.assertEqual("APPROVED", history["requests"][0]["status"])
        self.assertEqual([], history["grants"])
        rejected_id = uuid4()
        pending = self.call("request", OWNER, rejected_id, "TOP", "project.A", None, "프로젝트 접근 요청", exclusive=True)
        self.call("decide", ADMIN, rejected_id, uuid4(), "REJECT", "업무 범위 확인 필요", pending["revision"], exclusive=True)
        self.assertEqual("REJECTED", self.call("snapshot", OWNER.user_id)["requests"][0]["status"])

    def test_inactive_identity_and_invalid_registered_scope_fail_closed(self):
        self.registry()
        grant_id = self.grant(OWNER, "OWNER", block="block.A1")
        self.admin_query("UPDATE {oa} SET state=jsonb_set(state,'{{enabled}}','false') WHERE record_type='SSO_IDENTITY' AND workspace_key=%s",
                         (OWNER.user_id,))
        self.assert_code("ACCOUNT_DISABLED", lambda: self.call("require_actor", OWNER))
        self.assert_code("ACCOUNT_DISABLED", lambda: self.call("can_read_business", OWNER.user_id))
        self.assert_code("ACCOUNT_NOT_LINKED", lambda: self.call("require_actor", Actor("unknown", "합성", "sso")))
        self.assert_code("RESOURCE_NOT_FOUND", lambda: self.call("validate_scope", {"schema_version": 1, "project_id": "project.A", "block_id": "block.B1"}))
        with self.store(admin_id=None) as store:
            store.require_ready(ingestion=True)
            scope = store.validate_scope({"schema_version": 1, "project_id": "project.A", "block_id": "block.A1"})
            self.assertEqual("block.A1", scope["block_id"])
            self.assert_code("AUTHORIZATION_NOT_IMPLEMENTED", lambda: store.require_actor(ADMIN))
        self.assertEqual(grant_id, self.call("admin_snapshot", ADMIN)["grants"][0]["grant_id"])

    def test_permission_history_survives_explicit_administrator_rotation(self):
        self.registry()
        self.grant(OWNER, "OWNER", block="block.A1")
        with self.store(admin_id=OTHER.user_id) as store:
            store.require_actor(OWNER)
            self.assertTrue(store.can_write_business(OWNER.user_id))
            self.assertFalse(store.is_admin(ADMIN.user_id))
            self.assertTrue(store.is_admin(OTHER.user_id))
            self.assert_code("FORBIDDEN", lambda: store.admin_snapshot(ADMIN))

    def test_required_unique_definition_is_checked_instead_of_index_name(self):
        self.admin_query("DROP INDEX " + self.schema + ".sanity_workspace_key_uq")
        self.admin_query("CREATE INDEX sanity_workspace_key_uq ON {oa}(record_type,workspace_key) WHERE workspace_key IS NOT NULL")
        self.assert_code("AUTHORIZATION_SCHEMA", lambda: self.call("require_ready"))

    def test_audit_replay_rejects_gaps_and_duplicate_pending_requests(self):
        self.registry()
        request_id = uuid4()
        self.call("request", OWNER, request_id, "OWNER", "project.A", "block.A1", "업무 요청", exclusive=True)
        row = self.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s",
                               (PREFIX + str(request_id),)).fetchone()[0]
        duplicate = deepcopy(row)
        duplicate["revision"] += 1
        duplicate["command_id"] = str(uuid4())
        key = PREFIX + duplicate["command_id"]
        self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload) VALUES (%s,'WS_CONFIG',%s,%s)",
                         (stable_uuid("workspace-v1", "WS_CONFIG", key), key, json_value(duplicate)))
        self.assert_code("AUTHORIZATION_STATE_CORRUPT", lambda: self.call("snapshot", OWNER.user_id))
        self.admin_query("DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key=%s", (key,))
        row["revision"] += 1
        self.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_CONFIG' AND workspace_key=%s", (json_value(row), PREFIX + str(request_id)))
        self.assert_code("AUTHORIZATION_STATE_CORRUPT", lambda: self.call("snapshot", OWNER.user_id))

    def test_revocation_waits_for_shared_business_lock_and_next_request_observes_it(self):
        self.registry()
        grant_id = self.grant(OWNER, "OWNER", block="block.A1")
        revision = self.call("snapshot", OWNER.user_id)["revision"]
        attempted = threading.Event()
        def revoke():
            attempted.set()
            return self.call("revoke", ADMIN, grant_id, uuid4(), "담당 회수", revision, exclusive=True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.store() as store:
                self.assertTrue(store.can_write_business(OWNER.user_id))
                future = executor.submit(revoke)
                self.assertTrue(attempted.wait(2))
                self.assertFalse(future.done())
            future.result(timeout=5)
        self.assertFalse(self.call("can_write_business", OWNER.user_id))
