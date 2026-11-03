"""Dashboard authentication, boundaries and real PostgreSQL review workflow."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for name, file in (("dashboard_api_tests_target", "dashboard_api.py"), ("dashboard_fixtures", "test_oa.py")):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
api = sys.modules["dashboard_api_tests_target"]
fixture = sys.modules["dashboard_fixtures"]

from common.sanity_common import digest, pack_json
from common.sanity_db import Database, Queue
from vwp_bridge.sanity_exchange import respond_evidence
from tests.support import PgTestCase

TOKEN = "synthetic-dashboard-test-token-not-a-secret"
SETTINGS = {"SANITY_AUTH_MODE": "local_token", "SANITY_DASHBOARD_TOKEN": TOKEN, "SANITY_DASHBOARD_REVIEWER": "test-reviewer"}


class DashboardBoundaryTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {**SETTINGS, "SANITY_DB_ACCESS": "dbaas_http"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(api.create_app(), base_url="http://127.0.0.1", raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def auth(self):
        self.client.headers["Authorization"] = "Bearer " + TOKEN

    def test_authentication_required_for_data_and_mutations(self):
        self.assertEqual(200, self.client.get("/api/health").status_code)
        for method, path in (("get", "/api/session"), ("get", "/api/bundles"), ("post", "/api/receive")):
            result = getattr(self.client, method)(path, **({"json": {}} if method == "post" else {}))
            self.assertEqual(401, result.status_code)
            self.assertEqual({"code": "UNAUTHORIZED"}, result.json())
        self.client.headers["Authorization"] = "Bearer invalid"
        self.assertEqual(401, self.client.get("/api/session").status_code)

    def test_missing_configuration_never_enables_anonymous_access(self):
        with patch.dict(os.environ, {"SANITY_DASHBOARD_TOKEN": "short"}):
            self.assertEqual(503, self.client.get("/api/session").status_code)

    def test_http_mode_does_not_fallback_or_claim_write_support(self):
        self.auth()
        result = self.client.get("/api/session").json()
        self.assertFalse(result["writable"])
        self.assertEqual("test-reviewer", result["reviewer"])
        with patch.object(api, "Database", wraps=Database) as database:
            result = self.client.get("/api/bundles")
            self.assertEqual(503, result.status_code)
            self.assertEqual({"code": "DBAAS_SQL_CONTRACT_REQUIRED"}, result.json())
            database.assert_called_once_with("oa")

    def test_invalid_payloads_do_not_echo_secrets(self):
        self.auth()
        path = "/api/bundles/b-" + "a" * 64 + "/reviews"
        for body in ({"reason": "sensitive-input"}, {"reviewer": "forged-reviewer"}):
            result = self.client.post(path, json=body)
            self.assertEqual(400, result.status_code)
            self.assertEqual({"code": "INVALID_REQUEST"}, result.json())
        self.assertEqual(400, self.client.get("/api/bundles?limit=101").status_code)
        self.assertEqual(400, self.client.get("/api/bundles?offset=-1").status_code)
        self.assertEqual(400, self.client.get("/api/bundles/b-invalid").status_code)

    def test_request_size_content_type_host_and_no_cors(self):
        self.auth()
        result = self.client.post("/api/receive", content=b"x" * 65537, headers={"Content-Type": "application/json"})
        self.assertEqual(413, result.status_code)
        self.assertEqual(415, self.client.post("/api/receive", content="{}").status_code)
        self.assertEqual(400, self.client.get("/api/health", headers={"Host": "evil.example"}).status_code)
        result = self.client.get("/api/session", headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", result.headers)
        self.assertEqual("no-store", result.headers["cache-control"])
        self.assertEqual("nosniff", result.headers["x-content-type-options"])

    def test_internal_error_is_redacted(self):
        self.auth()
        with patch.object(api, "Database", side_effect=RuntimeError("secret DSN password")):
            result = self.client.get("/api/bundles")
        self.assertEqual(500, result.status_code)
        self.assertEqual({"code": "INTERNAL_ERROR"}, result.json())


class DashboardDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, SETTINGS)
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(api.create_app(), base_url="http://127.0.0.1", raise_server_exceptions=False,
                                 headers={"Authorization": "Bearer " + TOKEN})
        self.addCleanup(self.client.close)

    def receive(self, blocked=False):
        value = copy.deepcopy(fixture.payload())
        if blocked:
            value["global_blockers"] = ["INVENTORY_UNCONFIRMED"]
        value.pop("bundle_id")
        value["bundle_id"] = "b-" + digest(value)
        with Database("vwp") as db, db.transaction():
            Queue(db, "vwp_to_oa").enqueue({"message_id": value["bundle_id"], "message_type": "ANALYSIS_BUNDLE",
                "project": value["run"]["project"], "run_id": value["run"]["run_id"], "bundle_id": value["bundle_id"],
                "payload": {"schema_version": 1, "inventory_sha256": value["inventory"]["sha256"],
                            "producer_version": value["producer"]["version"]}, **pack_json(value)})
        result = self.client.post("/api/receive", json={})
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual("RECEIVED", result.json()["status"])
        self.bundle_id = value["bundle_id"]
        self.path = "/api/bundles/" + self.bundle_id
        return value

    def command(self, **changes):
        return {"unit_id": "u-numeric", "conclusion": "FAIL", "reason": "근거를 확인함",
                "request_id": str(uuid.uuid4()), "expected_revision": 0, **changes}

    def test_full_receive_review_report_and_history(self):
        self.receive()
        listed = self.client.get("/api/bundles?limit=1").json()
        self.assertEqual(1, listed["total"])
        self.assertEqual(self.bundle_id, listed["items"][0]["bundle_id"])
        detail = self.client.get(self.path).json()
        self.assertEqual(1, detail["unit_total"])
        self.assertEqual("INCOMPLETE", detail["coverage"]["review_status"])
        self.assertEqual(0, detail["review_revision"])
        group = self.client.get(self.path + "/units/u-numeric/groups").json()
        self.assertEqual("r-1", group["items"][0]["examples"][0]["record_id"])
        command = self.command()
        result = self.client.post(self.path + "/reviews", json=command)
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual("COMPLETE", result.json()["coverage"]["review_status"])
        self.assertEqual("FAIL", result.json()["coverage"]["check_status"])
        repeated = self.client.post(self.path + "/reviews", json=command)
        self.assertEqual("ALREADY_REVIEWED", repeated.json()["status"])
        detail = self.client.get(self.path).json()
        self.assertEqual("test-reviewer", detail["units"][0]["decision"]["reviewer"])
        self.assertEqual(2, detail["history_total"])
        self.assertEqual(1, detail["history"][0]["revision"])
        self.assertEqual("test-reviewer", detail["history"][0]["actor"])
        report = self.client.get(self.path + "/report")
        self.assertEqual(200, report.status_code)
        self.assertNotIn("Jira", report.text)
        self.assertIn("attachment", report.headers["content-disposition"])
        self.assertEqual("NO_MESSAGE", self.client.post("/api/receive", json={}).json()["status"])

    def test_revision_conflict_blocker_and_known_failure_guards(self):
        self.receive()
        response = self.client.post(self.path + "/reviews", json=self.command(conclusion="PASS"))
        self.assertEqual({"code": "FAIL_CANNOT_PASS"}, response.json())
        command = self.command()
        self.assertEqual(200, self.client.post(self.path + "/reviews", json=command).status_code)
        response = self.client.post(self.path + "/reviews", json=self.command())
        self.assertEqual(409, response.status_code)
        self.assertEqual({"code": "REVISION_CONFLICT"}, response.json())
        response = self.client.post(self.path + "/reviews", json={**command, "reason": "다른 내용"})
        self.assertEqual({"code": "REQUEST_CONFLICT"}, response.json())
        self.receive(blocked=True)
        self.assertEqual({"code": "REVIEW_BLOCKED"}, self.client.post(self.path + "/reviews", json=self.command()).json())

    def test_client_cannot_choose_reviewer_or_read_another_unit(self):
        self.receive()
        self.assertEqual(400, self.client.post(self.path + "/reviews", json=self.command(reviewer="forged")).status_code)
        self.assertEqual(404, self.client.get(self.path + "/units/u-other/groups").status_code)
        self.assertEqual([], self.client.get(self.path + "?unit_offset=1&unit_limit=1").json()["units"])

    def test_review_cannot_hide_corrupted_current_report(self):
        self.receive()
        damaged = b"corrupted but hash-consistent report"
        self.admin_query("UPDATE {oa} SET payload_bytes=%s,payload_sha256=%s WHERE record_type='REPORT'",
                         (damaged, hashlib.sha256(damaged).hexdigest()))
        result = self.client.post(self.path + "/reviews", json=self.command())
        self.assertEqual(409, result.status_code)
        self.assertEqual({"code": "STATE_CORRUPT"}, result.json())
        self.assertEqual(1, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='REVIEW_EVENT'").fetchone()[0])

    def test_out_of_scope_evidence_is_rejected_without_changing_review(self):
        self.receive()
        request_id = str(uuid.uuid4())
        command = {"unit_id": "u-numeric", "record_ids": ["r-1"], "reason": "추가 확인", "request_id": request_id}
        result = self.client.post(self.path + "/evidence", json=command)
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual("ALREADY_REQUESTED", self.client.post(self.path + "/evidence", json=command).json()["status"])
        requests = self.client.get(self.path + "/requests").json()
        self.assertEqual(1, requests["total"])
        self.assertIsNone(requests["items"][0]["response_status"])
        response_path = "/api/evidence/" + request_id
        self.assertEqual(404, self.client.get(response_path).status_code)
        self.assertEqual("REJECTED", respond_evidence(request_id)["result"])
        self.assertEqual(409, self.client.get(response_path).status_code)
        result = self.client.post(response_path + "/receive", json={})
        self.assertEqual("REJECTED", result.json()["result"])
        self.assertEqual({"result": "REJECTED", "record_count": 0, "records": []}, self.client.get(response_path).json())
        self.assertEqual("RECEIVED", self.client.get(self.path + "/requests").json()["items"][0]["response_status"])

    def test_concurrent_stale_review_creates_only_one_revision(self):
        self.receive()
        def submit(command):
            with TestClient(api.create_app(), base_url="http://127.0.0.1", raise_server_exceptions=False,
                            headers={"Authorization": "Bearer " + TOKEN}) as client:
                return client.post(self.path + "/reviews", json=command).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, [self.command(reason="첫 의견"), self.command(reason="두 번째 의견")]))
        self.assertEqual([200, 409], sorted(results))
        self.assertEqual(1, self.client.get(self.path).json()["review_revision"])

    def test_accepted_evidence_preview_is_bounded_and_revalidated(self):
        value = self.receive()
        rid = str(uuid.uuid4())
        result = self.client.post(self.path + "/evidence", json={"unit_id": "u-numeric", "record_ids": ["r-1"],
                                  "reason": "긴 원문 미리보기", "request_id": rid})
        self.assertEqual(200, result.status_code)
        raw = "합성 원문 " * 1000
        data = {"schema_version": 1, "request_id": "request-" + rid, "bundle_id": self.bundle_id,
                "review_unit_id": "u-numeric", "records": [{"record_id": "r-1", "file_id": "f-1",
                "review_unit_id": "u-numeric", "check_status": "FAIL", "raw": raw,
                "raw_sha256": hashlib.sha256(raw.encode()).hexdigest()}]}
        with Database("vwp") as db, db.transaction():
            Queue(db, "vwp_to_oa").enqueue({"message_id": "response-" + rid, "message_type": "EVIDENCE_RESPONSE",
                "project": value["run"]["project"], "run_id": value["run"]["run_id"], "bundle_id": self.bundle_id,
                "correlation_id": "request-" + rid, "payload": {"result": "FULFILLED"}, **pack_json(data)})
        path = "/api/evidence/" + rid
        self.assertEqual(409, self.client.get(path).status_code)
        self.assertEqual("FULFILLED", self.client.post(path + "/receive", json={}).json()["result"])
        record = self.client.get(path).json()["records"][0]
        self.assertEqual(raw[:4000], record["raw"])
        self.assertTrue(record["raw_truncated"])
        data["records"][0]["raw_sha256"] = "0" * 64
        forged = pack_json(data)
        self.admin_query("UPDATE {vwp_to_oa} SET payload_gzip=%s,payload_sha256=%s,uncompressed_bytes=%s WHERE message_id=%s",
                         (forged["payload_gzip"], forged["payload_sha256"], forged["uncompressed_bytes"], "response-" + rid))
        self.assertEqual({"code": "INVALID_RESPONSE"}, self.client.get(path).json())
