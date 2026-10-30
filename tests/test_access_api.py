"""The production access router uses authenticated identities and fixed errors."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from tests.http_support import TestClient

from common.sanity_common import ReviewError
from common.sanity_db import json_value, stable_uuid
from oa import access_api
from oa.sanity_auth import Actor
from tests.support import PgTestCase, ROOT

ADMIN = Actor("synthetic.admin", "합성 관리자", "sso")
USER = Actor("synthetic.user", "합성 사용자", "sso")
ACTION_HEADERS = {"X-Sanity-Request": "1", "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def application(actor):
    app = FastAPI()
    def authenticated(request: Request):
        # These synthetic credentials never reach a real company adapter.
        if request.headers.get("authorization") == "Bearer synthetic-admin":
            return ADMIN
        return actor
    app.include_router(access_api.create_access_router(authenticated))
    @app.exception_handler(ReviewError)
    async def fixed_error(request, exc):
        status = (403 if exc.code in {"FORBIDDEN", "ACCOUNT_DISABLED", "ACCOUNT_NOT_LINKED"}
                  else 404 if exc.code.endswith("NOT_FOUND") else 503 if exc.code.startswith("AUTHORIZATION_")
                  else 409 if exc.code in {"REVISION_CONFLICT", "REQUEST_CONFLICT"} else 400)
        return JSONResponse({"code": exc.code}, status_code=status)
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"code": "INVALID_REQUEST"}, status_code=400)
    return app


class AccessApiBoundaryTests(unittest.TestCase):
    def test_disabled_configuration_and_local_tokens_do_not_open_management(self):
        for actor, settings, code in ((USER, {}, "AUTHORIZATION_NOT_IMPLEMENTED"),
                (Actor("local-reviewer", "합성", "local_token"), {"SANITY_ADMIN_USER_ID": "local-reviewer"}, "FORBIDDEN")):
            with patch.object(access_api, "load_environment", return_value=settings), patch.object(access_api, "Database") as db:
                with TestClient(application(actor)) as client:
                    response = client.get("/api/access/me")
                self.assertEqual({"code": code}, response.json())
                db.assert_not_called()

    def test_mutation_client_header_and_cross_origin_are_rejected_before_database(self):
        body = {"request_id": str(uuid4()), "role": "TOP", "project_id": "synthetic.project", "block_id": None, "reason": "합성 요청"}
        with patch.object(access_api, "Database") as db, TestClient(application(USER)) as client:
            for headers in ({}, {"X-Sanity-Request": "1", "Origin": "http://other.test"},
                            {"X-Sanity-Request": "1", "Sec-Fetch-Site": "cross-site"}):
                response = client.post("/api/access/requests", json=body, headers=headers)
                self.assertEqual(403, response.status_code)
                self.assertEqual({"code": "FORBIDDEN"}, response.json())
            db.assert_not_called()

    def test_request_models_reject_forged_actor_unknown_fields_and_noninteger_revision(self):
        with patch.object(access_api, "Database") as db, TestClient(application(USER)) as client:
            bodies = [{"request_id": str(uuid4()), "role": "TOP", "project_id": "project", "reason": "합성", "user_id": ADMIN.user_id},
                      {"request_id": str(uuid4()), "role": "TOP", "project_id": "project", "reason": "합성", "is_admin": True}]
            for body in bodies:
                self.assertEqual({"code": "INVALID_REQUEST"}, client.post("/api/access/requests", json=body, headers=ACTION_HEADERS).json())
            path = "/api/admin/requests/" + str(uuid4()) + "/decision"
            body = {"command_id": str(uuid4()), "decision": "APPROVE", "reason": "합성", "expected_revision": True}
            self.assertEqual({"code": "INVALID_REQUEST"}, client.post(path, json=body, headers=ACTION_HEADERS).json())
            db.assert_not_called()


class AccessApiDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ("008_submissions.sql", "009_sso_identity.sql"):
            self.admin.execute((ROOT / "db" / name).read_text().replace("public.", self.schema + "."))
        timestamp = "2026-10-22T00:00:00.000000Z"
        for actor in (ADMIN, USER):
            self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload,state) VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",
                             (stable_uuid("sso-identity-v1", "SSO_IDENTITY", actor.user_id), actor.user_id,
                              json_value({"issuer": "https://synthetic.example.test/idp", "subject": actor.user_id,
                                          "display_name": actor.reviewer, "first_seen_at": timestamp}),
                              json_value({"enabled": True, "last_seen_at": timestamp})))
        configured = patch.object(access_api, "load_environment", return_value={"SANITY_ADMIN_USER_ID": ADMIN.user_id})
        configured.start()
        self.addCleanup(configured.stop)
        self.client = TestClient(application(USER), raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def post(self, path, body, *, admin=False):
        return self.client.post(path, json=body, headers={**ACTION_HEADERS, **({"Authorization": "Bearer synthetic-admin"} if admin else {})})

    def test_welcome_admin_creation_approval_and_revocation_use_the_live_account(self):
        self.assertEqual([], self.client.get("/api/access/me").json()["grants"])
        project = {"request_id": str(uuid4()), "project_id": "project.A", "name": "합성 프로젝트", "description": ""}
        self.assertEqual(403, self.post("/api/admin/projects", project).status_code)
        response = self.post("/api/admin/projects", project, admin=True)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("ALREADY_CREATED", self.post("/api/admin/projects", project, admin=True).json()["status"])
        block = {"request_id": str(uuid4()), "block_id": "block.A", "name": "합성 블록"}
        self.assertEqual(200, self.post("/api/admin/projects/project.A/blocks", block, admin=True).status_code)
        catalog = self.client.get("/api/access/catalog").json()
        self.assertEqual("project.A", catalog["projects"][0]["id"])
        self.assertEqual("block.A", catalog["blocks"][0]["id"])
        request_id = str(uuid4())
        request = {"request_id": request_id, "role": "OWNER", "project_id": "project.A", "block_id": "block.A", "reason": "업무 배정"}
        pending = self.post("/api/access/requests", request)
        self.assertEqual(200, pending.status_code, pending.text)
        self.assertEqual(USER.user_id, pending.json()["requests"][0]["user_id"])
        self.assertEqual("PENDING", pending.json()["requests"][0]["status"])
        decision = {"command_id": str(uuid4()), "decision": "APPROVE", "reason": "확인", "expected_revision": pending.json()["revision"]}
        path = "/api/admin/requests/" + request_id + "/decision"
        self.assertEqual(403, self.post(path, decision).status_code)
        approved = self.post(path, decision, admin=True)
        self.assertEqual(200, approved.status_code, approved.text)
        self.assertEqual(request_id, self.client.get("/api/access/me").json()["grants"][0]["grant_id"])
        revoke = {"command_id": str(uuid4()), "reason": "업무 종료", "expected_revision": approved.json()["revision"]}
        result = self.post("/api/admin/grants/" + request_id + "/revoke", revoke, admin=True)
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual([], self.client.get("/api/access/me").json()["grants"])

    def test_disabled_target_cannot_be_approved_but_pending_request_can_be_rejected(self):
        self.post("/api/admin/projects", {"request_id": str(uuid4()), "project_id": "project", "name": "합성"}, admin=True)
        request_id = str(uuid4())
        pending = self.post("/api/access/requests", {"request_id": request_id, "role": "TOP", "project_id": "project", "reason": "업무"}).json()
        self.admin_query("UPDATE {oa} SET state=jsonb_set(state,'{{enabled}}','false') WHERE record_type='SSO_IDENTITY' AND workspace_key=%s", (USER.user_id,))
        self.assertEqual(403, self.client.get("/api/access/me").status_code)
        path = "/api/admin/requests/" + request_id + "/decision"
        command = {"command_id": str(uuid4()), "decision": "APPROVE", "reason": "확인", "expected_revision": pending["revision"]}
        approved = self.post(path, command, admin=True)
        self.assertEqual({"code": "ACCOUNT_DISABLED"}, approved.json())
        rejected = self.post(path, {**command, "decision": "REJECT", "command_id": str(uuid4())}, admin=True)
        self.assertEqual(200, rejected.status_code, rejected.text)
        self.assertEqual([], rejected.json()["grants"])
        self.assertEqual("REJECTED", rejected.json()["requests"][0]["status"])
