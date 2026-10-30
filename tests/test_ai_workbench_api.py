"""Production workbench boundary tests with synthetic stores and no model calls."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from common.sanity_common import ReviewError
from oa import ai_workbench_api as api
from oa.access_api import require_mutation_request
from oa.sanity_auth import Actor


BASE = "/api/workbench/blocks/synthetic.block/versions/synthetic.version/reports/synthetic.report"
HEADERS = {"X-Sanity-Request": "1", "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
TARGET = ("synthetic.user", "synthetic.block", "synthetic.version", "synthetic.report")


class ProductionWorkbenchApiTests(unittest.TestCase):
    def setUp(self):
        self.db = object()
        self.access = SimpleNamespace(db=self.db)
        self.actor = SimpleNamespace(user_id=TARGET[0], auth_method="sso", access=self.access)
        self.ws = Mock(db=self.db)
        self.ws.authorization.is_admin.return_value = False
        self.ws.user.return_value = {"id": TARGET[0], "name": "합성 사용자", "username": TARGET[0], "role": "OWNER"}
        self.service = Mock()
        self.capability = {"available": True, "supported": True, "enabled": True,
                           "model": "synthetic-model", "provider": "OA_OPENAI_COMPATIBLE",
                           "remaining": None, "synthetic_only": False, "job": None}
        self.service.capability.side_effect = lambda *args, **kwargs: deepcopy(self.capability)
        self.service.request.return_value = {"id": str(uuid4()), "status": "PENDING"}
        self.pipeline = Mock()
        self.pipeline.view_paged.side_effect = lambda *args: {"revision": 3, "corporate_ai_connected": False}
        self.pipeline.records_page.return_value = {"items": [], "total": 0}
        self.pipeline.groups_page.return_value = {"items": [], "total": 0}
        self.cluster = Mock()
        self.cluster.change.return_value = {"id": str(uuid4()), "revision": 4, "groups": [], "rationales": {}}
        self.pipeline.change.return_value = deepcopy(self.cluster.change.return_value)
        self.review = Mock()
        self.review.view.side_effect = lambda *args: {
            "revision": 2, "proposal": {"schema_version": 2, "evidence_ids": ["log-1"],
            "criterion_ids": ["criterion-1"], "model_metadata": {"model_called": True},
            "group_snapshot": {"id": "group-a", "record_ids": ["log-1", "log-2"]}}}
        self.review.save.return_value = {"status": "REVIEW_SAVED", "event":
                                       {"revision": 3, "conclusion": "FAIL", "reason": "사람의 정정"}}
        self.factories = {}
        for name, result in (("ProductionWorkspaceStore", self.ws), ("jobs", self.service),
                             ("WorkspacePipelineStore", self.pipeline), ("WorkspaceClusteringStore", self.cluster),
                             ("WorkspaceGroupReviewStore", self.review)):
            patcher = patch.object(api, name, return_value=result)
            self.factories[name] = patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()

        def authorized_actor(request: Request):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                require_mutation_request(request)
            return self.actor

        app.include_router(api.create_workbench_router(authorized_actor))

        @app.exception_handler(ReviewError)
        async def error(request, exc):
            status = {"FORBIDDEN": 403, "RESOURCE_NOT_FOUND": 404, "NOT_FOUND": 404,
                      "SOURCE_FILE_ON_VWP": 409, "REVISION_CONFLICT": 409,
                      "AI_NOT_CONFIGURED": 503}.get(exc.code, 400)
            return JSONResponse({"code": exc.code}, status_code=status)

        @app.exception_handler(RequestValidationError)
        async def invalid(request, exc):
            return JSONResponse({"code": "INVALID_REQUEST"}, status_code=400)

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_model_choice_is_forwarded_for_classification_and_group_judgment(self):
        for suffix, extra in (("/clustering/ai", {}),
                              ("/groups/group-a/review/ai", {"expected_cluster_revision": 2})):
            self.service.request.reset_mock()
            request_id = uuid4()
            response = self.client.post(BASE + suffix, headers=HEADERS, json={
                "request_id": str(request_id), "expected_revision": 0,
                "model_id": "synthetic-second-model", **extra})
            self.assertEqual(202, response.status_code)
            self.service.request.assert_called_once_with(*TARGET, request_id=request_id,
                expected_revision=0, model_id="synthetic-second-model",
                **({"group_id": "group-a", **extra} if extra else {}))

    def test_model_choice_cannot_supply_private_routing_credentials_or_invalid_types(self):
        for extra in ({"model_id": ""}, {"model_id": "a" * 201}, {"model_id": ["model"]},
                      {"model_id": 123}, {"endpoint": "https://arbitrary.example.test/api"},
                      {"fallback_url": "https://arbitrary.example.test/api"}, {"token": "synthetic-private"}):
            response = self.client.post(BASE + "/clustering/ai", headers=HEADERS,
                json={"request_id": str(uuid4()), "expected_revision": 0, **extra})
            self.assertEqual(400, response.status_code)
            self.assertEqual({"code": "INVALID_REQUEST"}, response.json())
        self.service.request.assert_not_called()

    def test_capability_returns_only_model_choices_and_no_endpoint(self):
        self.capability["models"] = ["synthetic-model", "synthetic-second-model"]
        response = self.client.get(BASE + "/clustering/ai")
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.capability["models"], response.json()["models"])
        self.assertNotIn("endpoint", response.json())

    def test_only_authorized_sso_actor_can_open_workbench(self):
        for actor in (Actor("local-reviewer", "합성", "local_token"),
                      Actor(TARGET[0], "합성", "sso")):
            self.actor = actor
            response = self.client.get("/api/workbench/session")
            self.assertEqual(403, response.status_code)
            self.assertEqual({"code": "FORBIDDEN"}, response.json())
        self.factories["ProductionWorkspaceStore"].assert_not_called()

    def test_session_uses_canonical_user_and_existing_authorized_transaction(self):
        response = self.client.get("/api/workbench/session")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"user": self.ws.user.return_value, "csrf": "", "expires_at": "",
                          "environment": "PRODUCTION_SSO", "is_admin": False}, response.json())
        self.ws.user.assert_called_once_with(TARGET[0])
        self.factories["ProductionWorkspaceStore"].assert_called_once_with(self.db, self.access)

    def test_monitoring_and_cancellation_keep_the_authenticated_actor_and_mutation_guards(self):
        with patch('oa.sanity_ai_monitor.Monitor') as monitor:
            monitor.return_value.items.return_value={"items":[],"worker":{"site":"vwp","online":False}}
            response=self.client.get('/api/workbench/ai/jobs?user_id=someone-else')
            self.assertEqual(200,response.status_code)
            monitor.return_value.items.assert_called_once_with(TARGET[0])
        identity=str(uuid4())
        self.service.cancel.return_value={"id":identity,"status":"CANCELLED"}
        path='/api/workbench/ai/jobs/'+identity+'/cancel'
        self.assertEqual(403,self.client.post(path,json={}).status_code)
        self.service.cancel.assert_not_called()
        response=self.client.post(path,headers=HEADERS,json={"user_id":"someone-else"})
        self.assertEqual(200,response.status_code)
        self.service.cancel.assert_called_once_with(TARGET[0],identity)
        self.actor=SimpleNamespace(user_id=TARGET[0],auth_method='local_token',access=self.access)
        self.assertEqual(403,self.client.get('/api/workbench/ai/jobs').status_code)

    def test_job_deletion_keeps_sso_origin_and_server_actor_guards(self):
        identity = str(uuid4())
        path = '/api/workbench/ai/jobs/' + identity + '/delete'
        self.service.delete.return_value = {'id': identity, 'deleted': True}
        for headers in ({}, {'X-Sanity-Request': '1', 'Origin': 'http://different.test'},
                        {'X-Sanity-Request': '1', 'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(403, self.client.post(path, headers=headers, json={}).status_code)
        self.service.delete.assert_not_called()
        response = self.client.post(path, headers=HEADERS, json={'user_id': 'someone-else'})
        self.assertEqual(200, response.status_code)
        self.assertEqual({'id': identity, 'deleted': True}, response.json())
        self.service.delete.assert_called_once_with(TARGET[0], identity)
        self.assertEqual(400, self.client.post('/api/workbench/ai/jobs/not-a-uuid/delete', headers=HEADERS, json={}).status_code)
        self.actor = SimpleNamespace(user_id=TARGET[0], auth_method='local_token', access=self.access)
        self.assertEqual(403, self.client.post(path, headers=HEADERS, json={}).status_code)

    def test_session_exposes_only_the_server_administrator_flag_for_the_same_user(self):
        self.ws.authorization.is_admin.return_value = True
        response = self.client.get("/api/workbench/session?is_admin=false&user_id=someone-else")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["is_admin"])
        self.ws.authorization.is_admin.assert_called_once_with(TARGET[0])
        self.assertEqual("OWNER", response.json()["user"]["role"])

    def test_mutation_keeps_outer_origin_and_client_request_guards(self):
        body = {"request_id": str(uuid4()), "expected_revision": 3}
        for headers in ({}, {"X-Sanity-Request": "1", "Origin": "http://different.test"},
                        {"X-Sanity-Request": "1", "Sec-Fetch-Site": "cross-site"}):
            response = self.client.post(BASE + "/clustering/ai", json=body, headers=headers)
            self.assertEqual(403, response.status_code)
        self.factories["ProductionWorkspaceStore"].assert_not_called()
        self.service.request.assert_not_called()

    def test_ai_enqueue_only_registers_pinned_job_under_authenticated_user(self):
        request_id = str(uuid4())
        response = self.client.post(BASE + "/clustering/ai", headers=HEADERS,
                                    json={"request_id": request_id, "expected_revision": 3})
        self.assertEqual(202, response.status_code)
        self.assertEqual("PENDING", response.json()["status"])
        self.service.request.assert_called_once_with(*TARGET, request_id=UUID(request_id), expected_revision=3)
        scope = self.factories["jobs"].call_args.args[0]
        self.assertIs(self.db, scope.db)
        self.assertIs(self.ws, scope.ws)
        self.cluster.change.assert_not_called()
        self.review.propose.assert_not_called()

    def test_ai_commands_reject_spoofed_identity_mode_and_noninteger_revisions(self):
        body = {"request_id": str(uuid4()), "expected_revision": 3}
        invalid = [dict(body, user_id="other"), dict(body, mode="CRITERIA"),
                   dict(body, model="caller-model"), dict(body, expected_revision=True),
                   dict(body, expected_revision="3"), dict(body, extraData={"clientName": "forged"}),
                   dict(body, client_api_key="forged-key"), dict(body, session_id="forged-session"),
                   dict(body, headers={"x-client-api-key": "forged-key"})]
        for value in invalid:
            response = self.client.post(BASE + "/clustering/ai", headers=HEADERS, json=value)
            self.assertEqual({"code": "INVALID_REQUEST"}, response.json())
        review_path = BASE + "/groups/group-a/review/ai"
        for value in (body, dict(body, expected_cluster_revision=True), dict(body, expected_cluster_revision=0)):
            self.assertEqual(400, self.client.post(review_path, headers=HEADERS, json=value).status_code)
        self.service.request.assert_not_called()

    def test_group_job_and_human_save_are_separate_and_pin_partition(self):
        path = BASE + "/groups/group-a/review"
        request_id = str(uuid4())
        body = {"request_id": request_id, "expected_revision": 2, "expected_cluster_revision": 7}
        response = self.client.post(path + "/ai", headers=HEADERS, json=body)
        self.assertEqual(202, response.status_code)
        self.service.request.assert_called_once_with(*TARGET, group_id="group-a", request_id=UUID(request_id),
                                                    expected_revision=2, expected_cluster_revision=7)
        self.review.save.assert_not_called()
        response = self.client.post(path + "?compact=true", headers=HEADERS,
                                    json={**body, "conclusion": "FAIL", "reason": "사람의 정정"})
        self.assertEqual(200, response.status_code)
        self.assertEqual("REVIEW_SAVED", response.json()["status"])
        self.assertEqual("FAIL", response.json()["event"]["conclusion"])
        self.factories["WorkspaceGroupReviewStore"].assert_called_once_with(self.db, "group-a", 7, ws=self.ws)
        self.assertEqual(TARGET, self.review.save.call_args.args)
        self.assertNotIn("expected_cluster_revision", self.review.save.call_args.kwargs)

    def test_paged_workbench_and_review_keep_production_ai_and_direct_citations(self):
        value = self.client.get(BASE + "/workbench").json()
        self.assertEqual(self.capability, value["ai"])
        self.assertTrue(value["corporate_ai_connected"])
        self.factories["WorkspacePipelineStore"].assert_called_once_with(self.db, ws=self.ws)
        value = self.client.get(BASE + "/groups/group-a/review?compact=true").json()
        self.assertEqual(self.capability, value["ai"])
        self.assertEqual(["log-1"], value["proposal"]["evidence_ids"])
        self.assertEqual(["criterion-1"], value["proposal"]["criterion_ids"])
        self.assertEqual(2, value["proposal"]["group_snapshot"]["record_count"])
        self.assertNotIn("record_ids", value["proposal"]["group_snapshot"])
        self.service.capability.assert_called_with(*TARGET, group_id="group-a")

    def test_historical_pages_keep_revision_and_event_pins_and_page_bounds(self):
        event_id = str(uuid4())
        path = BASE + "/clustering/records"
        response = self.client.get(path, params={"expected_revision": 3, "event_id": event_id,
                                   "group_id": "group-a", "offset": 10, "limit": 5, "search": "합성"})
        self.assertEqual(200, response.status_code)
        self.pipeline.records_page.assert_called_once_with(*TARGET, expected_revision=3, event_id=event_id,
                                                           group_id="group-a", offset=10, limit=5, search="합성")
        for params in ({"limit": 5}, {"expected_revision": -1}, {"expected_revision": 3, "limit": 101}):
            self.assertEqual(400, self.client.get(path, params=params).status_code)
        self.assertEqual(1, self.pipeline.records_page.call_count)

    def test_original_v2_file_remains_on_vwp_and_scope_failure_is_preserved(self):
        self.ws.report.return_value = {"source_storage": "PARTS_V2", "raw_report": "must not be downloaded"}
        response = self.client.get(BASE + "/source")
        self.assertEqual(409, response.status_code)
        self.assertEqual({"code": "SOURCE_FILE_ON_VWP"}, response.json())
        self.ws.report.assert_called_once_with(*TARGET, hydrate_source=False)
        self.ws.report.side_effect = ReviewError("RESOURCE_NOT_FOUND", "synthetic denied scope")
        response = self.client.get(BASE)
        self.assertEqual(404, response.status_code)
        self.assertEqual({"code": "RESOURCE_NOT_FOUND"}, response.json())

    def test_production_has_no_synthetic_login_reference_engine_or_report_judgment(self):
        paths = ["/api/workbench/login", "/api/workbench/logout", BASE + "/review",
                 BASE + "/review-proposals", BASE + "/clustering/proposals", BASE + "/clustering/tasks",
                 BASE + "/clustering/tasks/step", BASE + "/groups/group-a/review-proposals"]
        for path in paths:
            self.assertEqual(404, self.client.post(path, headers=HEADERS, json={}).status_code, path)
        self.factories["ProductionWorkspaceStore"].assert_not_called()

    def test_por_and_submissions_use_production_workspace(self):
        self.ws.por.return_value = {"revision": 0, "version": None}
        path = "/api/workbench/blocks/synthetic.block"
        self.assertEqual(200, self.client.get(path + "/por").status_code)
        self.ws.access.assert_called_once_with(TARGET[0], "synthetic.block")
        with patch.object(api, "submissions", return_value={"items": [], "total": 0}) as submission_list:
            self.assertEqual(200, self.client.get(path + "/submissions?limit=10").status_code)
            submission_list.assert_called_once_with(self.db, TARGET[0], "synthetic.block", offset=0, limit=10, workspace=self.ws)

    def test_rationale_accept_cannot_smuggle_unreviewed_content(self):
        body = {"request_id": str(uuid4()), "expected_revision": 3, "action": "ACCEPT", "reason": "확인"}
        path = BASE + "/groups/group-a/rationale"
        response = self.client.post(path, headers=HEADERS, json={**body, "reviewed": True})
        self.assertEqual(400, response.status_code)
        self.cluster.change.assert_not_called()
        response = self.client.post(path, headers=HEADERS, json=body)
        self.assertEqual(200, response.status_code)
        self.assertEqual({"kind": "ACCEPT", "group_id": "group-a"},
                          self.cluster.change.call_args.kwargs["rationale_operation"])


if __name__ == "__main__":
    unittest.main()
