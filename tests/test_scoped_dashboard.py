"""Real PostgreSQL SSO dashboard scope, permission and revocation boundaries."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from threading import Event
import time
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from common.sanity_common import ReviewError, digest, pack_json
from common.sanity_db import Database, Queue, json_value, stable_uuid
from oa import dashboard_api as api
from oa.sanity_auth import AuthError, LocalUser, SSOIdentityProvider, UserDirectory, VerifiedIdentity
from oa.sanity_cluster_store import ClusterStore
from oa.sanity_exchange import receive_evidence, request_evidence
from oa.sanity_oa_store import OAStore
from oa.test_oa import payload as bundle_fixture
from tests.support import PgTestCase, ROOT

ISSUER = "https://synthetic-idp.example.test/entity"
USERS = ("synthetic.admin", "synthetic.owner", "synthetic.top", "synthetic.other")
ADMIN, OWNER, TOP, OTHER = USERS
STAMP = "2026-10-22T00:00:00.000000Z"


class VerifiedFixtureProvider(SSOIdentityProvider):
    """Fixed test credentials, without authorization roles or header identity."""
    def authenticate(self, request):
        token = request.cookies.get("synthetic_session")
        for index, user in enumerate(USERS):
            if token == "synthetic-verified-session-" + str(index):
                return VerifiedIdentity(ISSUER, user)
        raise AuthError("UNAUTHORIZED")


class FixtureDirectory(UserDirectory):
    def resolve(self, identity):
        if identity.issuer == ISSUER and identity.subject in USERS:
            return LocalUser(identity.subject, "합성 표시 이름 " + identity.subject)
        return None


class ScopedDashboardTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for filename in ("008_submissions.sql", "009_sso_identity.sql"):
            self.admin.execute((ROOT / "db" / filename).read_text().replace("public.", self.schema + "."))
        env = patch.dict(os.environ, {"SANITY_AUTH_MODE": "sso", "SANITY_ADMIN_USER_ID": ADMIN})
        env.start()
        self.addCleanup(env.stop)
        for user in USERS:
            identity = {"issuer": ISSUER, "subject": user, "display_name": "합성 표시 이름 " + user,
                        "first_seen_at": STAMP}
            state = {"enabled": True, "last_seen_at": STAMP}
            self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload,state) "
                             "VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",
                             (stable_uuid("sso-identity-v1", "SSO_IDENTITY", user), user,
                              json_value(identity), json_value(state)))
        self.app = api.create_app(auth_mode="sso", identity_provider=VerifiedFixtureProvider(),
                                  user_directory=FixtureDirectory())
        self.clients = {user: self.new_client(user) for user in USERS}
        for client in self.clients.values():
            self.addCleanup(client.close)

    def new_client(self, user):
        return TestClient(self.app, base_url="http://127.0.0.1", raise_server_exceptions=False,
                          cookies={"synthetic_session": "synthetic-verified-session-" + str(USERS.index(user))})

    def post(self, user, path, body):
        return self.clients[user].post(path, json=body, headers={"X-Sanity-Request": "1"})

    def success(self, response):
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def register(self):
        for project in ("project-a", "project-b"):
            self.success(self.post(ADMIN, "/api/admin/projects", {
                "request_id": str(uuid.uuid4()), "project_id": project, "name": "합성 " + project}))
        for project, block in (("project-a", "block-a1"), ("project-a", "block-a2"), ("project-b", "block-b1")):
            self.success(self.post(ADMIN, "/api/admin/projects/" + project + "/blocks", {
                "request_id": str(uuid.uuid4()), "block_id": block, "name": "합성 " + block}))

    def test_workbench_administrator_flag_does_not_grant_business_access(self):
        admin = self.success(self.clients[ADMIN].get('/api/workbench/session'))
        self.assertTrue(admin['is_admin'])
        self.assertEqual('USER', admin['user']['role'])
        self.assertEqual([], self.success(self.clients[ADMIN].get('/api/workbench/overview'))['projects'])
        self.assertFalse(self.success(self.clients[OWNER].get('/api/workbench/session'))['is_admin'])
        self.register()
        self.grant(ADMIN, 'OWNER', block='block-a1')
        self.grant(OWNER, 'OWNER', block='block-a1')
        for user, is_admin in ((ADMIN, True), (OWNER, False)):
            value = self.success(self.clients[user].get('/api/workbench/session?is_admin=true&user_id=someone-else'))
            self.assertIs(is_admin, value['is_admin'])
            self.assertEqual(user, value['user']['id'])
            self.assertEqual('OWNER', value['user']['role'])
        self.assertEqual(403, self.clients[OWNER].get('/api/admin/access').status_code)

    def grant(self, user, role, project="project-a", block=None):
        request_id = str(uuid.uuid4())
        value = self.success(self.post(user, "/api/access/requests", {
            "request_id": request_id, "role": role, "project_id": project, "block_id": block,
            "reason": "합성 업무 권한 요청"}))
        self.success(self.post(ADMIN, "/api/admin/requests/" + request_id + "/decision", {
            "command_id": str(uuid.uuid4()), "decision": "APPROVE", "reason": "합성 승인",
            "expected_revision": value["revision"]}))
        return request_id

    def revoke_body(self):
        revision = self.success(self.clients[ADMIN].get("/api/admin/access"))["revision"]
        return {"command_id": str(uuid.uuid4()), "reason": "합성 권한 회수", "expected_revision": revision}

    def receive(self, *, project="project-a", block="block-a1", scoped=True):
        value = bundle_fixture()
        value["run"].update(run_id="run-" + uuid.uuid4().hex, project="same-free-source-project", design="same-design")
        if scoped:
            value["run"]["scope"] = {"schema_version": 1, "project_id": project, "block_id": block}
        value.pop("bundle_id")
        value["bundle_id"] = "b-" + digest(value)
        with Database("oa") as db, db.transaction():
            OAStore(db).receive(value, {"interface": "synthetic-scope-test", "ai": {"status": "NOT_CONFIGURED"}})
        return value

    def base(self, value):
        return "/api/bundles/" + value["bundle_id"]

    def review_body(self):
        return {"unit_id": "u-numeric", "conclusion": "FAIL", "reason": "합성 검토 근거",
                "request_id": str(uuid.uuid4()), "expected_revision": 0}

    def evidence_body(self, request_id=None):
        return {"unit_id": "u-numeric", "record_ids": ["r-1"], "reason": "합성 원문 확인",
                "request_id": request_id or str(uuid.uuid4())}

    def fulfilled_evidence(self, value):
        """Seed a genuine validated exchange without any company or AI service."""
        rid = str(uuid.uuid4())
        request_evidence(value["bundle_id"], "u-numeric", ["r-1"], "합성 원문 확인", rid)
        raw = "synthetic private report evidence"
        data = {"schema_version": 1, "request_id": "request-" + rid, "bundle_id": value["bundle_id"],
                "review_unit_id": "u-numeric", "records": [{"record_id": "r-1", "file_id": "f-1",
                "review_unit_id": "u-numeric", "check_status": "FAIL", "raw": raw,
                "raw_sha256": hashlib.sha256(raw.encode()).hexdigest()}]}
        with Database("vwp") as db, db.transaction():
            Queue(db, "vwp_to_oa").enqueue({"message_id": "response-" + rid, "message_type": "EVIDENCE_RESPONSE",
                "project": value["run"]["project"], "run_id": value["run"]["run_id"],
                "bundle_id": value["bundle_id"], "correlation_id": "request-" + rid,
                "payload": {"result": "FULFILLED"}, **pack_json(data)})
        self.assertEqual("FULFILLED", receive_evidence(rid)["result"])
        return rid

    def cluster_fixture(self, value):
        with Database("oa") as db, db.transaction():
            return ClusterStore(db, value["bundle_id"], "u-numeric", lock=True).request(
                uuid.uuid4(), 0, "", [], "RULES", OWNER)["job_id"]

    def test_admin_starts_without_business_grants_and_can_approve_own_top_request(self):
        self.register()
        value = self.receive()
        session = self.success(self.clients[ADMIN].get("/api/session"))
        self.assertTrue(session["is_admin"])
        self.assertTrue(session["authorization_ready"])
        self.assertFalse(session["can_read_business"])
        self.assertFalse(session["writable"])
        self.assertFalse(session["can_receive"])
        self.assertEqual([], self.success(self.clients[ADMIN].get("/api/bundles"))["items"])
        self.assertEqual(404, self.clients[ADMIN].get(self.base(value)).status_code)
        self.assertEqual(403, self.post(ADMIN, "/api/receive", {}).status_code)
        self.grant(ADMIN, "TOP")
        session = self.success(self.clients[ADMIN].get("/api/session"))
        self.assertTrue(session["can_read_business"])
        self.assertFalse(session["writable"])
        self.assertEqual(200, self.clients[ADMIN].get(self.base(value)).status_code)
        self.assertEqual(403, self.post(ADMIN, self.base(value) + "/reviews", self.review_body()).status_code)

    def test_listing_filters_before_count_and_pagination_using_canonical_scope(self):
        self.register()
        self.grant(TOP, "TOP")
        self.grant(OWNER, "OWNER", block="block-a1")
        first = self.receive()
        self.receive(project="project-b", block="block-b1")
        sibling = self.receive(block="block-a2")
        second = self.receive()
        self.receive(scoped=False)
        self.receive(project="project-b", block="block-b1")
        owner_pages = [self.success(self.clients[OWNER].get("/api/bundles", params={"limit": 1, "offset": offset}))
                       for offset in range(3)]
        self.assertEqual([2, 2, 2], [page["total"] for page in owner_pages])
        self.assertEqual([1, 1, 0], [len(page["items"]) for page in owner_pages])
        self.assertEqual({first["bundle_id"], second["bundle_id"]},
                         {page["items"][0]["bundle_id"] for page in owner_pages[:2]})
        top = self.success(self.clients[TOP].get("/api/bundles"))
        self.assertEqual(3, top["total"])
        self.assertEqual({first["bundle_id"], second["bundle_id"], sibling["bundle_id"]},
                         {row["bundle_id"] for row in top["items"]})
        self.assertEqual({"same-free-source-project"}, {row["project"] for row in top["items"]})

    def test_top_reads_own_scope_but_writes_are_403_and_foreign_routes_are_404(self):
        self.register()
        self.grant(TOP, "TOP")
        own = self.receive()
        foreign = self.receive(project="project-b", block="block-b1")
        own_rid, foreign_rid = self.fulfilled_evidence(own), self.fulfilled_evidence(foreign)
        jid = self.cluster_fixture(own)
        tails = ("", "/report", "/requests", "/units/u-numeric/groups", "/units/u-numeric/clustering",
                 "/units/u-numeric/clustering/" + jid + "/members")
        for tail in tails:
            with self.subTest(read=tail):
                self.assertEqual(200, self.clients[TOP].get(self.base(own) + tail).status_code)
                self.assertEqual(404, self.clients[TOP].get(self.base(foreign) + tail).status_code)
        writes = (("/reviews", self.review_body()), ("/evidence", self.evidence_body()),
                  ("/units/u-numeric/clustering", {"request_id": str(uuid.uuid4()), "expected_revision": 0}),
                  ("/units/u-numeric/clustering/actions", {"request_id": str(uuid.uuid4()), "expected_revision": 0,
                    "action": "DEFER", "job_id": jid, "reason": "합성 보류"}),
                  ("/units/u-numeric/clustering/" + jid + "/receive", {}))
        for tail, body in writes:
            with self.subTest(write=tail):
                self.assertEqual(403, self.post(TOP, self.base(own) + tail, body).status_code)
                self.assertEqual(404, self.post(TOP, self.base(foreign) + tail, body).status_code)
        for tail in ("", "/records/r-1"):
            self.assertEqual(200, self.clients[TOP].get("/api/evidence/" + own_rid + tail).status_code)
            self.assertEqual(404, self.clients[TOP].get("/api/evidence/" + foreign_rid + tail).status_code)
        self.assertEqual(403, self.post(TOP, "/api/evidence/" + own_rid + "/receive", {}).status_code)
        self.assertEqual(404, self.post(TOP, "/api/evidence/" + foreign_rid + "/receive", {}).status_code)

    def test_owner_reviews_and_cluster_commands_record_stable_user_id(self):
        self.register()
        self.grant(OWNER, "OWNER", block="block-a1")
        value = self.receive()
        self.success(self.post(OWNER, self.base(value) + "/reviews", self.review_body()))
        detail = self.success(self.clients[OWNER].get(self.base(value)))
        self.assertEqual(OWNER, detail["history"][0]["actor"])
        self.assertEqual(OWNER, detail["units"][0]["decision"]["reviewer"])
        self.assertTrue(all(detail["permissions"].values()))
        cluster = self.success(self.post(OWNER, self.base(value) + "/units/u-numeric/clustering", {
            "request_id": str(uuid.uuid4()), "expected_revision": 0, "mode": "RULES", "rules": []}))
        reviewer = self.admin_query("SELECT payload->'command'->>'reviewer' FROM {oa_to_vwp} WHERE message_id=%s",
                                    (cluster["job_id"],)).fetchone()[0]
        self.assertEqual(OWNER, reviewer)
        session = self.success(self.clients[OWNER].get("/api/session"))
        self.assertNotEqual(OWNER, session["reviewer"])

    def test_revoke_hides_existing_bundle_requests_and_full_evidence_on_next_request(self):
        self.register()
        grant = self.grant(OWNER, "OWNER", block="block-a1")
        value = self.receive()
        rid = self.fulfilled_evidence(value)
        self.assertEqual(200, self.clients[OWNER].get("/api/evidence/" + rid + "/records/r-1").status_code)
        self.success(self.post(ADMIN, "/api/admin/grants/" + grant + "/revoke", self.revoke_body()))
        self.assertEqual(0, self.success(self.clients[OWNER].get("/api/bundles"))["total"])
        for path in (self.base(value), self.base(value) + "/requests", "/api/evidence/" + rid,
                     "/api/evidence/" + rid + "/records/r-1"):
            self.assertEqual(404, self.clients[OWNER].get(path).status_code, path)
        self.assertEqual(404, self.post(OWNER, "/api/evidence/" + rid + "/receive", {}).status_code)
        self.assertEqual(404, self.post(OWNER, self.base(value) + "/evidence", self.evidence_body()).status_code)
        session = self.success(self.clients[OWNER].get("/api/session"))
        self.assertFalse(session["can_read_business"])
        self.assertFalse(session["writable"])

    def test_response_id_alias_cannot_expose_another_blocks_evidence(self):
        self.register()
        self.grant(OWNER, "OWNER", block="block-a1")
        own, foreign = self.receive(), self.receive(block="block-a2")
        self.assertEqual(404, self.clients[OWNER].get(self.base(foreign)).status_code)
        raw = "SYNTHETIC-FOREIGN-BLOCK-PRIVATE-EVIDENCE"
        for already_received in (True, False):
            with self.subTest(already_received=already_received):
                own_id, foreign_id = str(uuid.uuid4()), str(uuid.uuid4())
                self.success(self.post(OWNER, self.base(own) + "/evidence", self.evidence_body(own_id)))
                request_evidence(foreign["bundle_id"], "u-numeric", ["r-1"], "합성 외부 블록 요청", foreign_id)
                body = {"schema_version": 1, "request_id": "request-" + foreign_id,
                        "bundle_id": foreign["bundle_id"], "review_unit_id": "u-numeric",
                        "records": [{"record_id": "r-1", "file_id": "f-1", "review_unit_id": "u-numeric",
                                     "check_status": "FAIL", "raw": raw,
                                     "raw_sha256": hashlib.sha256(raw.encode()).hexdigest()}]}
                # The FK is valid: correlation and metadata refer to the foreign
                # request. Only the response's lookup ID aliases the owned one.
                with Database("vwp") as db, db.transaction():
                    Queue(db, "vwp_to_oa").enqueue({"message_id": "response-" + own_id,
                        "message_type": "EVIDENCE_RESPONSE", "project": foreign["run"]["project"],
                        "run_id": foreign["run"]["run_id"], "bundle_id": foreign["bundle_id"],
                        "correlation_id": "request-" + foreign_id, "payload": {"result": "FULFILLED"},
                        **pack_json(body)})
                if already_received:
                    # Reproduce a response acknowledged by an older worker; its
                    # payload still needs validation on both existing read paths.
                    with Database("oa") as db:
                        queue = Queue(db, "vwp_to_oa")
                        message = queue.claim(("EVIDENCE_RESPONSE",), "response-" + own_id)
                        self.assertIsNotNone(message)
                        with db.transaction():
                            queue.finish(message)
                    responses = [self.clients[OWNER].get("/api/evidence/" + own_id + tail)
                                 for tail in ("", "/records/r-1")]
                else:
                    responses = [self.post(OWNER, "/api/evidence/" + own_id + "/receive", {})]
                    status = self.admin_query("SELECT status FROM {vwp_to_oa} WHERE message_id=%s",
                                              ("response-" + own_id,)).fetchone()[0]
                    self.assertEqual("REJECTED", status)
                for response in responses:
                    self.assertEqual(400, response.status_code, response.text)
                    self.assertEqual({"code": "INVALID_RESPONSE"}, response.json())
                    self.assertNotIn(raw, response.text)

    def test_mutations_require_explicit_header_and_reject_cross_site_requests(self):
        self.register()
        self.grant(OWNER, "OWNER", block="block-a1")
        value = self.receive()
        path = self.base(value) + "/reviews"
        for headers in ({}, {"X-Sanity-Request": "1", "Sec-Fetch-Site": "cross-site"},
                        {"X-Sanity-Request": "1", "Origin": "https://foreign.example.test"}):
            self.assertEqual(403, self.clients[OWNER].post(path, json=self.review_body(), headers=headers).status_code)
        create = {"request_id": str(uuid.uuid4()), "project_id": "project-c", "name": "합성 프로젝트"}
        self.assertEqual(403, self.clients[ADMIN].post("/api/admin/projects", json=create).status_code)
        self.assertEqual(403, self.post(OTHER, "/api/admin/projects", create).status_code)
        self.success(self.clients[OWNER].post(path, json=self.review_body(), headers={
            "X-Sanity-Request": "1", "Origin": "http://127.0.0.1", "Sec-Fetch-Site": "same-origin"}))

    def test_scoped_receive_rejects_missing_registry_and_wrong_project_block_pair_atomically(self):
        with self.assertRaises(ReviewError) as error:
            self.receive()
        self.assertEqual("RESOURCE_NOT_FOUND", error.exception.code)
        self.register()
        for project, block in (("unknown-project", "block-a1"), ("project-a", "unknown-block"),
                               ("project-b", "block-a1")):
            with self.subTest(project=project, block=block), self.assertRaises(ReviewError) as error:
                self.receive(project=project, block=block)
            self.assertEqual("RESOURCE_NOT_FOUND", error.exception.code)
        count = self.admin_query("SELECT count(*) FROM {oa} WHERE record_type IN "
                                 "('RECEIVED_BUNDLE','REVIEW_UNIT','REVIEW_EVENT','REPORT')").fetchone()[0]
        self.assertEqual(0, count)

    def test_unconfigured_sso_authorization_and_provider_stay_fail_closed(self):
        with patch.dict(os.environ, {"SANITY_ADMIN_USER_ID": ""}):
            session = self.success(self.clients[ADMIN].get("/api/session"))
            self.assertFalse(session["authorization_ready"])
            result = self.clients[ADMIN].get("/api/bundles")
            self.assertEqual(503, result.status_code)
            self.assertEqual("AUTHORIZATION_NOT_IMPLEMENTED", result.json()["code"])
        with TestClient(api.create_app(auth_mode="sso"), base_url="http://127.0.0.1",
                        raise_server_exceptions=False) as client:
            result = client.get("/api/bundles")
            self.assertEqual(503, result.status_code)
            self.assertEqual("AUTH_SSO_NOT_CONFIGURED", result.json()["code"])

    def test_revoke_waits_for_authorized_helper_write_then_blocks_subsequent_write(self):
        self.register()
        grant = self.grant(OWNER, "OWNER", block="block-a1")
        value = self.receive()
        revoke_body = self.revoke_body()
        entered, release = Event(), Event()
        original = api.request_evidence

        def paused_helper(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic helper was not released")
            return original(*args, **kwargs)

        def send(user, path, body):
            with self.new_client(user) as client:
                return client.post(path, json=body, headers={"X-Sanity-Request": "1"})

        with patch.object(api, "request_evidence", side_effect=paused_helper), ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(send, OWNER, self.base(value) + "/evidence", self.evidence_body())
            try:
                self.assertTrue(entered.wait(2), "Authorized request did not reach its helper")
                revoke = pool.submit(send, ADMIN, "/api/admin/grants/" + grant + "/revoke", revoke_body)
                waiting, deadline = False, time.monotonic() + 2
                while time.monotonic() < deadline:
                    waiting = bool(self.admin.execute("SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                        "AND NOT granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database())").fetchone()[0])
                    if waiting:
                        break
                    Event().wait(0.01)
                self.assertTrue(waiting, "Revocation did not wait on the business request's shared lock")
                self.assertFalse(revoke.done())
            finally:
                release.set()
            self.success(request.result(timeout=5))
            self.success(revoke.result(timeout=5))
        self.assertEqual(1, self.admin_query("SELECT count(*) FROM {oa_to_vwp} WHERE bundle_id=%s "
                                            "AND message_type='EVIDENCE_REQUEST'", (value["bundle_id"],)).fetchone()[0])
        self.assertEqual(404, self.post(OWNER, self.base(value) + "/evidence", self.evidence_body()).status_code)
