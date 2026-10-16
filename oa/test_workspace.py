"""Real PostgreSQL workspace authorization, persistence and PoR invariants."""
import copy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import unittest
import uuid

from fastapi.testclient import TestClient

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database, json_value, stable_uuid
from oa.sanity_workspace import SYNTHETIC_PASSWORD, WorkspaceStore, record_id, seed_workspace, token_hash
from tests.support import PgTestCase

spec = importlib.util.spec_from_file_location("workspace_api_tests", Path(__file__).with_name("workspace_api.py"))
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
ORIGIN = "http://127.0.0.1"


def fixture():
    users = [{"id": f"user-{i:02d}", "name": f"Fixture {i}", "role": "TOP" if i < 4 else "블록 담당자"} for i in range(1, 11)]
    projects = [{"id": f"project-{i}", "name": name, "top_user": f"user-{i:02d}"} for i, name in enumerate(("Atlas", "Boreal", "Cygnus"), 1)]
    owners = (4, 5, 6, 7, 8, 9, 10, 4, 5)
    blocks, items = [], []
    for pi, project in enumerate(projects):
        for bi, name in enumerate(("CPU", "GPU", "DDR")):
            bid = project["id"] + "-" + name.lower()
            blocks.append({"id": bid, "project_id": project["id"], "name": name, "owner": f"user-{owners[pi * 3 + bi]:02d}", "revision": "SYNTHETIC_" + bid})
            index = pi * 3 + bi + 1
            items.append({"id": f"sample-{index:04d}", "project_id": project["id"], "block_id": bid,
                          "item_id": "gca_constraints", "family": "GCA", "rule": "CLK_0003", "check_status": "FAIL", "parse_status": "COMPLETE",
                          "parser": "test_parser", "record_count": 1, "records": [], "blockers": [],
                          "analysis_ready": True, "raw_report": "Synthetic test report for " + bid})
    data = {"schema_version": 1, "summary": {"origin": "SYNTHETIC_PUBLIC_REFERENCE", "actual_tools_run": False,
             "real_ai_called": False, "db_ingestion_verified": True}, "users": users, "projects": projects, "blocks": blocks, "items": items}
    data["dataset_sha256"] = digest(data)
    return data


class WorkspaceDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        with Database("oa") as db, db.transaction():
            seed_workspace(db, fixture())
        self.client = self.client_for()

    def client_for(self, username=None):
        client = TestClient(api.create_app(origins=(ORIGIN,)), base_url=ORIGIN,
                            raise_server_exceptions=False, headers={"Origin": ORIGIN})
        self.addCleanup(client.close)
        if username:
            response = client.post("/workspace/api/login", json={"username": username, "password": SYNTHETIC_PASSWORD})
            self.assertEqual(200, response.status_code, response.text)
            client.headers["X-CSRF-Token"] = response.json()["csrf"]
        return client

    def login(self, username="user04"):
        response = self.client.post("/workspace/api/login", json={"username": username, "password": SYNTHETIC_PASSWORD})
        self.assertEqual(200, response.status_code, response.text)
        self.client.headers["X-CSRF-Token"] = response.json()["csrf"]
        return response

    def command(self, **changes):
        return {"version_id": "project-1-cpu-post-1", "expected_revision": 0,
                "request_id": str(uuid.uuid4()), "reason": "확인한 버전을 블록 기준으로 지정", **changes}

    def test_login_cookies_and_session_are_server_managed_without_password_disclosure(self):
        response = self.login()
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie); self.assertIn("samesite=strict", cookie)
        value = self.client.get("/workspace/api/session")
        self.assertEqual(200, value.status_code)
        self.assertEqual("user-04", value.json()["user"]["id"])
        self.assertNotIn("password", value.text)
        self.assertNotIn("salt", value.text)
        self.assertEqual("no-store", value.headers["cache-control"])

    def test_login_failure_and_unauthenticated_data_never_use_a_demo_fallback(self):
        for username in ("user04", "user99"):
            result = self.client.post("/workspace/api/login", json={"username": username, "password": "wrong"})
            self.assertEqual(401, result.status_code)
            self.assertEqual({"code": "UNAUTHORIZED"}, result.json())
        for path in ("/overview", "/blocks/project-1-cpu/versions", "/blocks/project-1-cpu/history"):
            self.assertEqual(401, self.client.get("/workspace/api" + path).status_code)

    def test_overview_is_filtered_by_current_server_assignments(self):
        self.login()
        data = self.client.get("/workspace/api/overview").json()
        self.assertEqual([("Atlas", ["CPU"]), ("Cygnus", ["GPU"])], [(p["name"], [b["name"] for b in p["blocks"]]) for p in data["projects"]])
        self.assertEqual(403, self.client.get("/workspace/api/blocks/project-1-gpu/versions").status_code)
        top = self.client_for("user01").get("/workspace/api/overview").json()
        self.assertEqual(3, len(top["projects"][0]["blocks"]))
        self.assertFalse(any(block["can_assign_por"] for block in top["projects"][0]["blocks"]))

    def test_owner_saves_por_with_audit_and_top_reads_the_same_persisted_version(self):
        self.login()
        result = self.client.post("/workspace/api/blocks/project-1-cpu/por", json=self.command())
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual(1, result.json()["revision"])
        top = self.client_for("user01")
        versions = top.get("/workspace/api/blocks/project-1-cpu/versions").json()
        self.assertEqual("project-1-cpu-post-1", versions["por"]["version"]["id"])
        history = top.get("/workspace/api/blocks/project-1-cpu/history").json()["items"]
        self.assertEqual(1, len(history)); self.assertEqual("user-04", history[0]["actor"]["id"])
        self.assertIn("블록 기준", history[0]["reason"])
        self.assertIsNone(top.get("/workspace/api/blocks/project-1-gpu/versions").json()["por"]["version"])

    def test_top_and_foreign_owner_cannot_designate_a_block_por(self):
        for username in ("user01", "user05"):
            client = self.client_for(username)
            result = client.post("/workspace/api/blocks/project-1-cpu/por", json=self.command())
            self.assertEqual(403, result.status_code)

    def test_foreign_project_block_or_version_cannot_be_submitted_through_the_api(self):
        self.login()
        for vid in ("project-2-cpu-post-1", "project-1-gpu-post-1", "project-3-gpu-post-1"):
            result = self.client.post("/workspace/api/blocks/project-1-cpu/por", json=self.command(version_id=vid))
            self.assertEqual(404, result.status_code)
        with Database("oa") as db, db.transaction(readonly=True):
            self.assertEqual(0, WorkspaceStore(db).por("project-1-cpu")["revision"])

    def test_duplicate_uuid_returns_exact_result_and_changed_content_conflicts(self):
        self.login()
        command = self.command()
        first = self.client.post("/workspace/api/blocks/project-1-cpu/por", json=command)
        second = self.client.post("/workspace/api/blocks/project-1-cpu/por", json=command)
        self.assertEqual(first.json(), second.json())
        changed = self.client.post("/workspace/api/blocks/project-1-cpu/por", json={**command, "reason": "다른 내용"})
        self.assertEqual(409, changed.status_code)
        self.assertEqual(1, len(self.client.get("/workspace/api/blocks/project-1-cpu/history").json()["items"]))

    def test_simultaneous_changes_allow_one_revision_and_preserve_the_winner(self):
        def save(vid):
            try:
                with Database("oa") as db, db.transaction():
                    return WorkspaceStore(db).assign_por("user-04", "project-1-cpu", vid, 0, uuid.uuid4(), "동시 변경 검증")["version"]["id"]
            except ReviewError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(save, ("project-1-cpu-post-0", "project-1-cpu-post-1")))
        self.assertEqual(1, outcomes.count("REVISION_CONFLICT"))
        with Database("oa") as db, db.transaction(readonly=True):
            result = WorkspaceStore(db).por("project-1-cpu")
            self.assertEqual(1, result["revision"])
            self.assertIn(result["version"]["id"], outcomes)

    def test_por_does_not_rewrite_reports_and_unreceived_versions_stay_empty(self):
        self.login()
        with Database("oa") as db, db.transaction(readonly=True):
            before = WorkspaceStore(db).get("WS_REPORT", "sample-0001")["payload"]
        self.assertEqual(200, self.client.post("/workspace/api/blocks/project-1-cpu/por", json=self.command(version_id="project-1-cpu-pre-0")).status_code)
        with Database("oa") as db, db.transaction(readonly=True):
            self.assertEqual(before, WorkspaceStore(db).get("WS_REPORT", "sample-0001")["payload"])
            block = WorkspaceStore(db).overview("user-04")["projects"][0]["blocks"][0]
            self.assertEqual(0, block["total"])
            self.assertEqual("project-1-cpu-pre-0", block["report_version"]["id"])
        empty = self.client.get("/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-pre-0/reports").json()
        self.assertEqual((0, {}, []), (empty["total"], empty["counts"], empty["items"]))
        available = self.client.get("/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports?eco=chip_finish").json()
        self.assertEqual(1, available["total"])

    def test_same_request_uuid_cannot_race_across_two_owned_blocks(self):
        request_id = uuid.uuid4()
        def save(pair):
            bid, vid = pair
            try:
                with Database("oa") as db, db.transaction():
                    return WorkspaceStore(db).assign_por("user-04", bid, vid, 0, request_id, "동일 요청 범위 검증")["status"]
            except ReviewError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, (("project-1-cpu", "project-1-cpu-post-1"), ("project-3-gpu", "project-3-gpu-post-1"))))
        self.assertCountEqual(["POR_ASSIGNED", "REQUEST_CONFLICT"], results)

    def test_report_detail_search_and_eco_are_scoped_on_the_server(self):
        self.login()
        base = "/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports"
        self.assertEqual(1, self.client.get(base + "?eco=chip_finish&search=CLK_0003").json()["total"])
        self.assertEqual(0, self.client.get(base + "?eco=eco01").json()["total"])
        self.assertEqual(400, self.client.get(base + "?eco=foreign_eco").status_code)
        self.assertEqual(404, self.client.get(base + "/sample-0002").status_code)
        self.assertEqual("Synthetic test report for project-1-cpu", self.client.get(base + "/sample-0001").json()["raw_report"])
        self.assertEqual(0, self.client.get(base + "?eco=chip_finish&search=%25").json()["total"])

    def test_sanity_items_are_counted_before_pagination_and_filtered_by_item_not_tool(self):
        self.login()
        with Database("oa") as db, db.transaction():
            store = WorkspaceStore(db)
            source = store.get("WS_REPORT", "sample-0001")["payload"]
            for index in range(40):
                item = {**source, "id": f"timing-{index:03d}", "item_id": "gca_timing",
                        "check_status": "PASS" if index % 2 else "FAIL"}
                if index == 0:
                    item.update(check_status="UNKNOWN", parse_status="MISSING", raw_report=None, record_count=0)
                store.put("WS_REPORT", item["id"], item, project="project-1", block="project-1-cpu")
            unclassified = {key: value for key, value in source.items() if key != "item_id"}
            unclassified["id"] = "unclassified-report"
            store.put("WS_REPORT", unclassified["id"], unclassified, project="project-1", block="project-1-cpu")
        base = "/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports?eco=chip_finish"
        page = self.client.get(base + "&item_id=gca_timing&offset=25&limit=10").json()
        groups = {group["item_id"]: group for group in page["groups"]}
        self.assertEqual(40, page["total"])
        self.assertEqual(10, len(page["items"]))
        self.assertEqual({"gca_timing"}, {item["item_id"] for item in page["items"]})
        self.assertEqual({"": 1, "gca_constraints": 1, "gca_timing": 40}, {key: group["total"] for key, group in groups.items()})
        self.assertEqual(["GCA"], groups["gca_timing"]["families"])
        self.assertEqual({"PASS": 20, "FAIL": 19, "UNKNOWN": 1}, groups["gca_timing"]["counts"])
        self.assertEqual({"COMPLETE": 39, "MISSING": 1}, groups["gca_timing"]["parse_counts"])
        missing = self.client.get(base + "&item_id=gca_timing&status=MISSING").json()
        self.assertEqual(["timing-000"], [item["id"] for item in missing["items"]])
        self.assertEqual(page["groups"], missing["groups"])
        self.assertEqual(1, self.client.get(base + "&item_id=gca_constraints").json()["total"])
        self.assertEqual(1, self.client.get(base + "&item_id=").json()["total"])
        self.assertEqual(0, self.client.get(base + "&item_id=gca_").json()["total"])
        self.assertEqual([], self.client.get(base.replace("chip_finish", "eco01")).json()["groups"])
        self.assertEqual(403, self.client.get(base.replace("project-1-cpu", "project-1-gpu")).status_code)

    def test_csrf_origin_body_and_forged_user_are_rejected(self):
        self.login()
        path, command = "/workspace/api/blocks/project-1-cpu/por", self.command()
        self.assertEqual(403, self.client.post(path, json=command, headers={"X-CSRF-Token": "wrong"}).status_code)
        self.assertEqual(403, self.client.post(path, json=command, headers={"Origin": "https://foreign.invalid"}).status_code)
        self.assertEqual(400, self.client.post(path, json={**command, "user_id": "user-01"}).status_code)
        self.assertEqual(400, self.client.post(path, json={**command, "expected_revision": True}).status_code)
        self.assertEqual(400, self.client.post(path, json={**command, "reason": "  "}).status_code)
        self.assertEqual(413, self.client.post(path, content=b'x'*16385, headers={"Content-Type": "application/json"}).status_code)
        self.assertEqual(400, self.client.get("/workspace/api/overview", headers={"Host": "foreign.invalid"}).status_code)

    def test_logout_and_deactivation_invalidate_already_issued_sessions(self):
        self.login()
        old_cookie = self.client.cookies.get(api.COOKIE)
        self.assertEqual(200, self.client.post("/workspace/api/logout", json={}).status_code)
        self.client.cookies.set(api.COOKIE, old_cookie, path="/workspace")
        self.assertEqual(401, self.client.get("/workspace/api/overview").status_code)
        self.client.cookies.clear(); self.login()
        with Database("oa") as db, db.transaction():
            db.execute("UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() WHERE record_id=%s",
                       (json_value({"active": False}), record_id("WS_USER", "user-04")))
        self.assertEqual(401, self.client.get("/workspace/api/overview").status_code)

    def test_existing_workspace_is_never_reseeded_or_silently_reset(self):
        self.login()
        self.client.post("/workspace/api/blocks/project-1-cpu/por", json=self.command())
        with Database("oa") as db, db.transaction():
            self.assertEqual("ALREADY_INITIALIZED", seed_workspace(db, fixture())["status"])
            self.assertEqual(1, WorkspaceStore(db).por("project-1-cpu")["revision"])
        changed = fixture(); changed["items"][0]["rule"] = "OTHER"; changed["dataset_sha256"] = digest({k:v for k,v in changed.items() if k != "dataset_sha256"})
        with self.assertRaisesRegex(ReviewError, "Existing workspace"):
            with Database("oa") as db, db.transaction(): seed_workspace(db, changed)

    def test_schema_keeps_legacy_scope_required_and_workspace_has_no_fake_bundle(self):
        with Database("oa") as db, db.transaction(readonly=True):
            self.assertEqual(0, db.execute("SELECT count(*) AS n FROM {oa} WHERE workspace_key IS NOT NULL AND (bundle_id IS NOT NULL OR run_id IS NOT NULL)").fetchone()["n"])
            self.assertEqual(4, len(db.conn.execute("SELECT relname FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid WHERE n.nspname=%s AND c.relkind='r'", (self.schema,)).fetchall()))
        import psycopg2
        with self.assertRaises(psycopg2.IntegrityError):
            with self.admin.transaction():
                self.admin_query("INSERT INTO {oa} (record_id,record_type,payload) VALUES (%s,'RECEIVED_BUNDLE',%s)", (uuid.uuid4(), json_value({})))

    def test_secure_cookie_is_set_for_https_and_expired_session_is_rejected(self):
        with TestClient(api.create_app(origins=("https://127.0.0.1",)), base_url="https://127.0.0.1", headers={"Origin": "https://127.0.0.1"}) as client:
            login = client.post("/workspace/api/login", json={"username": "user04", "password": SYNTHETIC_PASSWORD})
            self.assertIn("Secure", login.headers["set-cookie"])
            self.assertEqual(200, client.get("/workspace/api/session").status_code)
            key = token_hash(client.cookies.get(api.COOKIE))
            self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,ARRAY['expires_at'],to_jsonb(%s::text)) "
                             "WHERE record_type='WS_SESSION' AND workspace_key=%s", ("2000-01-01T00:00:00+00:00", key))
            self.assertEqual(401, client.get("/workspace/api/session").status_code)
