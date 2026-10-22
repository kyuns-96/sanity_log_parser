"""Production workspace grants and first PoR with no synthetic users or seed."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import tempfile
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from common.sanity_common import ReviewError
from common.sanity_db import Database, json_value, stable_uuid
from oa.sanity_access import AccessStore, access_lock
from oa.sanity_auth import Actor
from oa.sanity_production_workspace import ProductionWorkspaceStore
from oa.sanity_workspace_sources import submissions
from tests.support import PgTestCase, ROOT


ADMIN = Actor("production.test.admin", "Test administrator", "sso")
OWNER = Actor("production.test.owner", "Test owner", "sso")
TOP = Actor("production.test.top", "Test project reviewer", "sso")


class ProductionWorkspaceTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ("008_submissions.sql", "009_sso_identity.sql"):
            self.admin.execute((ROOT / "db" / name).read_text().replace("public.", self.schema + "."))
        timestamp = "2026-10-22T00:00:00.000000Z"
        for actor in (ADMIN, OWNER, TOP):
            self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload,state) "
                "VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",
                (stable_uuid("sso-identity-v1", "SSO_IDENTITY", actor.user_id), actor.user_id,
                 json_value({"issuer": "https://synthetic.example.test/idp", "subject": actor.user_id,
                             "display_name": actor.reviewer, "first_seen_at": timestamp}),
                 json_value({"enabled": True, "last_seen_at": timestamp})))
        with self.store(management=True) as store:
            access = store.authorization
            access.create_project(ADMIN, uuid4(), "project.A", "Project A")
            access.create_project(ADMIN, uuid4(), "project.B", "Project B")
            for project, block in (("project.A", "block.A1"), ("project.A", "block.A2"), ("project.B", "block.B1")):
                access.create_block(ADMIN, uuid4(), project, block, block)
        self.owner_grant = self.grant(OWNER, "OWNER", "block.A1")
        self.grant(TOP, "TOP")
        with self.store() as store:
            for project, block in (("project.A", "block.A1"), ("project.A", "block.A2"), ("project.B", "block.B1")):
                version = {"id": block + ".v1", "project_id": project, "block_id": block,
                           "net_ver": "N1", "revision": "R1", "layout": "PRE", "source_pre_id": None, "ecos": [None]}
                store.put("WS_VERSION", version["id"], version, project=project, block=block)

    @contextmanager
    def store(self, *, management=False, readonly=False):
        with Database("oa") as db, db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED " + ("READ ONLY" if readonly else "READ WRITE"))
            access_lock(db, exclusive=management)
            yield ProductionWorkspaceStore(db, AccessStore(db, ADMIN.user_id))

    def grant(self, actor, role, block=None):
        request = uuid4()
        with self.store(management=True) as store:
            access = store.authorization
            response = access.request(actor, request, role, "project.A", block, "Synthetic test assignment")
            access.decide(ADMIN, request, uuid4(), "APPROVE", "Synthetic test approval", response["revision"])
        return str(request)

    def assert_code(self, code, call):
        with self.assertRaises(ReviewError) as caught:
            call()
        self.assertEqual(code, caught.exception.code)

    def test_owner_top_and_admin_use_current_grants_without_synthetic_accounts(self):
        with self.store(readonly=True) as store:
            block, _ = store.access(OWNER.user_id, "block.A1", write=True)
            self.assertTrue(store.can_write(OWNER.user_id, block))
            block, _ = store.access(TOP.user_id, "block.A2")
            self.assertFalse(store.can_write(TOP.user_id, block))
            self.assert_code("FORBIDDEN", lambda: store.access(TOP.user_id, "block.A1", write=True))
            self.assert_code("RESOURCE_NOT_FOUND", lambda: store.access(OWNER.user_id, "block.A2"))
            self.assert_code("RESOURCE_NOT_FOUND", lambda: store.access(ADMIN.user_id, "block.A1"))
            self.assertEqual("USER", store.user(ADMIN.user_id)["role"])
            self.assertEqual("OWNER", store.user(OWNER.user_id)["role"])
            self.assert_code("FORBIDDEN", lambda: store.login("synthetic", "synthetic"))
            self.assert_code("FORBIDDEN", lambda: store.session("synthetic"))
            self.assert_code("FORBIDDEN", lambda: store.logout("synthetic"))
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type IN ('WS_USER','WS_SESSION')").fetchone()[0])
        with self.store(management=True) as store:
            access = store.authorization
            access.revoke(ADMIN, self.owner_grant, uuid4(), "Synthetic revocation", access.snapshot(ADMIN.user_id)["revision"])
        with self.store(readonly=True) as store:
            self.assert_code("RESOURCE_NOT_FOUND", lambda: store.access(OWNER.user_id, "block.A1"))

    def test_overview_and_versions_are_readonly_and_display_real_grants(self):
        with self.store(readonly=True) as store:
            overview = store.overview(OWNER.user_id)
            self.assertEqual("PRODUCTION_SSO", overview["data_origin"])
            self.assertEqual(["project.A"], [project["id"] for project in overview["projects"]])
            project = overview["projects"][0]
            self.assertEqual([TOP.user_id], [user["id"] for user in project["tops"]])
            self.assertEqual(["block.A1"], [block["id"] for block in project["blocks"]])
            self.assertEqual([OWNER.user_id], [user["id"] for user in project["blocks"][0]["owners"]])
            self.assertTrue(project["blocks"][0]["can_assign_por"])
            self.assertIsNone(project["blocks"][0]["por"]["version"])
            self.assertEqual([], store.overview(ADMIN.user_id)["projects"])
            self.assertEqual(2, len(store.overview(TOP.user_id)["projects"][0]["blocks"]))
            self.assertFalse(store.versions(TOP.user_id, "block.A1")["can_assign_por"])
            self.assertTrue(store.versions(OWNER.user_id, "block.A1")["can_assign_por"])
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_POR'").fetchone()[0])

    def test_first_por_is_initialized_only_on_valid_write_and_retries_are_idempotent(self):
        request = uuid4()
        with self.store() as store:
            self.assert_code("FORBIDDEN", lambda: store.assign_por(TOP.user_id, "block.A1", "block.A1.v1", 0, request, "Test reason"))
            self.assert_code("NOT_FOUND", lambda: store.assign_por(OWNER.user_id, "block.A1", "block.B1.v1", 0, request, "Test reason"))
            self.assert_code("INVALID_REQUEST", lambda: store.assign_por(OWNER.user_id, "block.A1", "block.A1.v1", 0, request, " "))
            self.assertEqual(0, store.db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='WS_POR'").fetchone()["n"])
            first = store.assign_por(OWNER.user_id, "block.A1", "block.A1.v1", 0, request, "Test reason")
            retry = store.assign_por(OWNER.user_id, "block.A1", "block.A1.v1", 0, request, "Test reason")
            self.assertEqual(first, retry)
            self.assertEqual(1, first["revision"])
            self.assertEqual(OWNER.user_id, first["assigned_by"]["id"])
        with self.store(readonly=True) as store:
            self.assertEqual("block.A1.v1", store.por("block.A1")["version"]["id"])
            self.assertEqual(1, len(store.history(TOP.user_id, "block.A1")["items"]))
        rows = self.admin_query("SELECT record_type,payload FROM {oa} WHERE record_type IN ('WS_BLOCK','WS_PROJECT')").fetchall()
        self.assertTrue(all(row[1].get("owners", row[1].get("tops")) == [] for row in rows))

    def test_concurrent_first_por_assignment_has_one_revision_and_one_event(self):
        def assign():
            try:
                with self.store() as store:
                    store.assign_por(OWNER.user_id, "block.A1", "block.A1.v1", 0, uuid4(), "Concurrent test")
                return "SAVED"
            except ReviewError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: assign(), range(2)))
        self.assertCountEqual(["SAVED", "REVISION_CONFLICT"], results)
        self.assertEqual(1, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_POR_EVENT'").fetchone()[0])

    def test_disabled_identity_is_rejected_and_hidden_from_assignment_display(self):
        self.admin_query("UPDATE {oa} SET state=jsonb_set(state,'{{enabled}}','false'::jsonb) WHERE record_type='SSO_IDENTITY' AND workspace_key=%s", (OWNER.user_id,))
        with self.store(readonly=True) as store:
            self.assert_code("ACCOUNT_DISABLED", lambda: store.access(OWNER.user_id, "block.A1"))
            overview = store.overview(TOP.user_id)
            self.assertEqual([], overview["projects"][0]["blocks"][0]["owners"])

    def prepare_source(self):
        from common.sanity_parsers import ParserRegistry
        from devtools.sanity_submission_fixture import fixture
        from vwp.submission_cli import SubmissionClient
        from vwp_bridge.submission_worker import SubmissionWorker

        directory = tempfile.TemporaryDirectory(prefix="production-source-fixture-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source = fixture(root)
        actor = Actor(source["actor"].user_id, "Synthetic source submitter", "sso")
        timestamp = "2026-10-22T00:00:00.000000Z"
        self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload,state) "
            "VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",
            (stable_uuid("sso-identity-v1", "SSO_IDENTITY", actor.user_id), actor.user_id,
             json_value({"issuer": "https://synthetic.example.test/idp", "subject": actor.user_id,
                         "display_name": actor.reviewer, "first_seen_at": timestamp}),
             json_value({"enabled": True, "last_seen_at": timestamp})))
        request = uuid4()
        with self.store(management=True) as store:
            access = store.authorization
            access.create_project(ADMIN, uuid4(), "project-1", "Synthetic source project")
            access.create_block(ADMIN, uuid4(), "project-1", "project-1-cpu", "Synthetic source block")
            response = access.request(actor, request, "OWNER", "project-1", "project-1-cpu", "Synthetic source assignment")
            access.decide(ADMIN, request, uuid4(), "APPROVE", "Synthetic source approval", response["revision"])
        client = SubmissionClient(source["client_config"], source["provider"], source["authority"], state_root=root / "state")
        sent = client.submit(source["target"], confirm=lambda _: True, preview=lambda _: None)
        with Database("vwp") as db:
            SubmissionWorker(db, source["bridge_config"], source["authority"], ParserRegistry()).once()
        return actor, str(request), sent["submission_id"]

    def test_production_source_receives_with_sso_grants_and_returns_operating_page_link(self):
        from devtools.sanity_submission_fixture import FixtureIdentity
        from oa.submission_receiver import SourceReceiver

        actor, _, submission_id = self.prepare_source()
        with Database("oa") as db:
            receiver = SourceReceiver(db, FixtureIdentity(),
                workspace=ProductionWorkspaceStore(db, AccessStore(db, ADMIN.user_id)))
            results = []
            while True:
                result = receiver.receive_one()
                if result["status"] == "NO_MESSAGE":
                    break
                results.append(result)
        self.assertEqual("OA_STORED", results[-1]["status"])
        with self.store(readonly=True) as store:
            listing = submissions(store.db, actor.user_id, "project-1-cpu", workspace=store)
            self.assertEqual(1, listing["total"])
            item = listing["items"][0]
            self.assertEqual(submission_id, item["id"])
            self.assertEqual("READY", item["review_readiness"])
            link = urlsplit(item["workspace_links"][0])
            self.assertEqual("/", link.path)
            self.assertEqual(["workbench"], parse_qs(link.query)["page"])
            self.assertEqual(["project-1-cpu"], parse_qs(link.query)["block"])
            self.assertIsNone(store.por("project-1-cpu")["version"])
            report = store.db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
                                     "AND payload->>'source_storage'='PARTS_V2' ORDER BY workspace_key LIMIT 1").fetchone()["payload"]
            hydrated = store.report(actor.user_id, "project-1-cpu", report["version_id"], report["id"])
            self.assertEqual(4, len(hydrated["records"]))
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type IN ('WS_USER','WS_SESSION')").fetchone()[0])

    def test_long_lived_production_receiver_rechecks_revoked_grant_between_pages(self):
        from devtools.sanity_submission_fixture import FixtureIdentity
        from oa.submission_receiver import SourceReceiver

        actor, grant, submission_id = self.prepare_source()
        with Database("oa") as db:
            receiver = SourceReceiver(db, FixtureIdentity(),
                workspace=ProductionWorkspaceStore(db, AccessStore(db, ADMIN.user_id)))
            manifest = db.execute("SELECT message_id FROM {vwp_to_oa} WHERE message_type='SOURCE_MANIFEST'").fetchone()["message_id"]
            self.assertEqual("IMPORTING", receiver.receive_one(manifest)["status"])
            with self.store(management=True) as store:
                access = store.authorization
                access.revoke(ADMIN, grant, uuid4(), "Synthetic revoke between source pages", access.snapshot(ADMIN.user_id)["revision"])
            self.assert_code("RESOURCE_NOT_FOUND", receiver.receive_one)
        row = self.admin_query("SELECT state FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_key=%s",
                               (submission_id,)).fetchone()[0]
        self.assertEqual("REJECTED", row["delivery_state"])
        self.assertEqual(["RESOURCE_NOT_FOUND"], row["receipt"]["blockers"])
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_REPORT'").fetchone()[0])
        with self.store(readonly=True) as store:
            self.assert_code("RESOURCE_NOT_FOUND", lambda: submissions(store.db, actor.user_id, "project-1-cpu", workspace=store))

    def test_sso_migration_does_not_bypass_missing_source_schema_constraint(self):
        from devtools.sanity_submission_fixture import FixtureIdentity
        from oa.submission_receiver import SourceReceiver

        self.admin_query("ALTER TABLE {oa_to_vwp} DROP CONSTRAINT sanity_oa_message_scope_v3")
        with Database("oa") as db:
            workspace = ProductionWorkspaceStore(db, AccessStore(db, ADMIN.user_id))
            self.assert_code("SUBMISSION_SCHEMA_REQUIRED",
                             lambda: SourceReceiver(db, FixtureIdentity(), workspace=workspace))
