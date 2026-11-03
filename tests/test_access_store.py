"""Small, real-PostgreSQL coverage of production permissions and immutable audit."""
from contextlib import contextmanager
from uuid import uuid4


from common.sanity_common import ReviewError
from common.sanity_db import Database, json_value, stable_uuid
from oa.sanity_access import AccessStore, access_lock
from oa.sanity_auth import Actor
from tests.support import PgTestCase, ROOT

ADMIN = Actor("synthetic.admin", "합성 관리자", "sso")
OWNER = Actor("synthetic.owner", "합성 블록 담당자", "sso")
TOP = Actor("synthetic.top", "합성 프로젝트 담당자", "sso")
OTHER = Actor("synthetic.other", "합성 다른 사용자", "sso")


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


    def test_legacy_grants_are_project_members_with_sql_scoped_read_and_write(self):
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
        for actor, expected in ((OWNER, [a1, a2]), (TOP, [a1, a2]), (OTHER, []), (ADMIN, [])):
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
        self.assertEqual("block.A2", self.call("require_bundle", TOP.user_id, a2, write=True)["block_id"])
        self.assertEqual("block.A2", self.call("require_bundle", OWNER.user_id, a2, write=True)["block_id"])
        self.assert_code("RESOURCE_NOT_FOUND", lambda: self.call("require_bundle", OWNER.user_id, b1, write=True))
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
