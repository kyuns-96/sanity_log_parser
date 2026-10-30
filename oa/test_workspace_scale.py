"""Average (10,000) and larger (100,000) single-report sequential review."""
import copy
import json
from pathlib import Path
import resource
import time
from unittest.mock import patch
import uuid

from tests.http_support import TestClient

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database
from devtools.sanity_large_samples import large_report
from oa.sanity_workspace import seed_workspace, SYNTHETIC_PASSWORD, WorkspaceStore
from oa.test_workspace import fixture, api, ORIGIN
from tests.support import PgTestCase

BASE = "/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/"


class WorkspaceScaleTests(PgTestCase):
    def setUp(self):
        super().setUp()
        data = fixture()
        count = 100000 if "100000" in self._testMethodName else 10000 if "10000" in self._testMethodName else 20
        heavy = count >= 10000
        unique = "unique" in self._testMethodName
        source = large_report("GCA", report_id="large-source", count=count,
                              groups=count if unique else 100 if heavy else 2)
        source.update(project_id="project-1", block_id="project-1-cpu")
        data["items"].append(source)
        if not heavy:
            target = copy.deepcopy(source)
            target["id"] = "large-target"
            for record in target["records"]: record["id"] = record["id"].replace("source", "target")
            data["items"].append(target)
        data["dataset_sha256"] = digest({key: value for key, value in data.items() if key != "dataset_sha256"})
        with Database("oa") as db, db.transaction(): seed_workspace(db, data)
        self.client = self.login()

    def login(self, user="user04"):
        client = TestClient(api.create_app(origins=(ORIGIN,)), base_url=ORIGIN, headers={"Origin": ORIGIN}, raise_server_exceptions=False)
        self.addCleanup(client.close)
        response = client.post("/workspace/api/login", json={"username": user, "password": SYNTHETIC_PASSWORD})
        self.assertEqual(200, response.status_code, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        return client

    def get(self, suffix, report="large-source"):
        response = self.client.get(BASE+report+suffix)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def post(self, suffix, body, report="large-source", status=200):
        response = self.client.post(BASE+report+suffix, json=body)
        self.assertEqual(status, response.status_code, response.text)
        return response.json()

    def task(self):
        return self.post("/clustering/tasks", {"expected_revision": 0, "request_id": str(uuid.uuid4())})

    def finish(self, task):
        while task["status"] in ("PENDING", "RUNNING"):
            task = self.post("/clustering/tasks/step", {"task_id": task["id"], "expected_chunk": task["next_chunk"]})
        self.assertEqual("COMPLETED", task["status"], task)
        return task

    def test_10000_records_resume_without_duplicate_work_and_only_pages_cross_the_api(self):
        original = self.get("")
        por = self.client.get("/workspace/api/blocks/project-1-cpu/versions").json()["por"]
        task = self.task()
        body = {"task_id": task["id"], "expected_chunk": 0}
        first = self.post("/clustering/tasks/step", body)
        self.assertEqual((500, 1), (first["processed"], first["next_chunk"]))
        self.assertEqual(first, self.post("/clustering/tasks/step", body))
        self.client = self.login()  # Fresh application/session, same persisted task.
        resumed = self.get("/workbench")["task"]
        self.assertEqual(first, resumed)
        done = self.finish(resumed)
        self.assertEqual((10000, 20), (done["processed"], len(done["chunks"])))
        view = self.get("/workbench")
        self.assertLess(len(json.dumps(view)), 40000)
        self.assertNotIn("records", view["report"])
        self.assertNotIn("groups", view["current"])
        self.assertEqual((10000, 100, False), (view["record_count"], view["summary"]["total"], view["summary"]["confirmed"]))
        page = self.get("/clustering/groups?expected_revision=1&limit=25")
        self.assertEqual((25, 100), (len(page["items"]), page["total"]))
        self.assertNotIn("record_ids", page["items"][0])
        last = self.get("/clustering/records?expected_revision=1&offset=9950&limit=50")
        self.assertEqual((50, 10000, "large-source-e-10000"), (len(last["items"]), last["total"], last["items"][-1]["id"]))
        operation = {"kind": "MOVE", "record_ids": ["large-source-e-00001", "large-source-e-10000"], "new_group_id": "manual-cross-page", "label": "서로 다른 페이지에서 확인한 그룹"}
        self.post("/clustering/operations", {"expected_revision": 1, "request_id": str(uuid.uuid4()), "reason": "전체 원본 위치 확인", "operation": operation})
        merged = self.get("/clustering/records?expected_revision=2&group_id=manual-cross-page&limit=1")
        self.assertEqual((2, 1), (merged["total"], len(merged["items"])))
        cluster = self.get("/clustering")["current"]
        ids = [key for group in cluster["groups"] for key in group["record_ids"]]
        self.assertEqual(10000, len(ids)); self.assertEqual(10000, len(set(ids)))
        self.assertEqual(original, self.get(""))
        self.assertEqual(por, self.client.get("/workspace/api/blocks/project-1-cpu/versions").json()["por"])

    def test_10000_unique_groups_support_last_page_edit_and_independent_group_review(self):
        self.finish(self.task())
        page = self.get("/clustering/groups?expected_revision=1&offset=9975&limit=25")
        self.assertEqual((10000, 25), (page["total"], len(page["items"])))
        gid = page["items"][-1]["id"]
        body = {"expected_revision": 1, "request_id": str(uuid.uuid4()), "reason": "마지막 그룹의 근거 확인",
                "operation": {"kind": "RENAME", "destination_id": gid, "label": "마지막 페이지 그룹"}}
        saved = self.post("/clustering/operations", body)
        self.assertLess(len(json.dumps(saved)), 10000)
        self.assertEqual(saved, self.post("/clustering/operations", body))
        self.post(f"/groups/{gid}/rationale", {"action":"ACCEPT", "expected_revision":2,
            "request_id":str(uuid.uuid4()), "reason":"새 이름에도 기존 분류 근거가 적합함을 확인"})
        self.post("/clustering?compact=true", {"action": "CONFIRM", "reason": "전체 10,000개 그룹을 검토했습니다.", "expected_revision": 3, "request_id": str(uuid.uuid4())})
        judgment = {"expected_revision": 0, "expected_cluster_revision": 4, "request_id": str(uuid.uuid4()), "conclusion": "PASS", "reason": "허용 조건과 원문을 확인했습니다."}
        result = self.post(f"/groups/{gid}/review?compact=true", judgment)
        self.assertNotIn("record_ids", result["event"]["group_snapshot"])
        self.assertEqual(1, result["event"]["group_snapshot"]["record_count"])
        view = self.get("/workbench")
        self.assertEqual((10000, 1, 9999), (view["summary"]["total"], view["summary"]["reviewed"], view["summary"]["pending"]))
        self.assertFalse(view["summary"]["complete"])
        self.assertIsNone(self.get("/review")["current"])
        self.assertEqual("FAIL", self.get("")["check_status"])
        self.assertEqual(9999, self.get("/clustering/groups?expected_revision=4&status=UNREVIEWED")["total"])

    def test_100000_unique_groups_finish_all_records_resume_and_review_the_final_group(self):
        original = self.get("/workbench")
        por = self.client.get("/workspace/api/blocks/project-1-cpu/versions").json()["por"]
        began = time.monotonic()
        task = self.task()
        self.assertEqual(5000, task["chunk_size"])
        first = self.post("/clustering/tasks/step", {"task_id": task["id"], "expected_chunk": 0})
        self.assertEqual((5000, 1), (first["processed"], first["next_chunk"]))
        self.client = self.login()
        self.assertEqual(first, self.get("/workbench")["task"])
        done = self.finish(first)
        self.assertEqual((100000, 20), (done["processed"], len(done["chunks"])))
        self.assertEqual(list(range(0, 100000, 5000)), [part["start"] for part in done["chunks"]])
        self.assertEqual(100000, done["chunks"][-1]["stop"])
        classification_seconds = time.monotonic()-began
        view = self.get("/workbench")
        self.assertEqual((100000, 100000), (view["record_count"], view["summary"]["total"]))
        self.assertLess(len(json.dumps(view)), 40000)
        last = self.get("/clustering/records?expected_revision=1&offset=99950&limit=50")
        self.assertEqual((100000, 50, "large-source-e-100000"),
                         (last["total"], len(last["items"]), last["items"][-1]["id"]))
        groups = self.get("/clustering/groups?expected_revision=1&offset=99975&limit=25")
        self.assertEqual((100000, 25), (groups["total"], len(groups["items"])))
        gid = groups["items"][-1]["id"]
        self.post("/clustering?compact=true", {"action": "CONFIRM", "expected_revision": 1,
            "request_id": str(uuid.uuid4()), "reason": "100,000건 전체의 고유 그룹 배정 확인"})
        proposal = self.post(f"/groups/{gid}/review-proposals?compact=true",
                             {"expected_revision": 0, "expected_cluster_revision": 2, "request_id": str(uuid.uuid4())})
        self.assertEqual("DEFER", proposal["conclusion"])
        result = self.post(f"/groups/{gid}/review?compact=true", {"expected_revision": 0,
            "expected_cluster_revision": 2, "request_id": str(uuid.uuid4()), "conclusion": "PASS",
            "reason": "마지막 그룹 원문과 허용 조건을 확인했습니다."})
        self.assertEqual(1, result["event"]["group_snapshot"]["record_count"])
        view = self.get("/workbench")
        self.assertEqual((1, 99999, False), (view["summary"]["reviewed"], view["summary"]["pending"], view["summary"]["complete"]))
        self.assertEqual(original["source_sha256"], view["source_sha256"])
        self.assertEqual("FAIL", view["report"]["check_status"])
        self.assertEqual(por, self.client.get("/workspace/api/blocks/project-1-cpu/versions").json()["por"])
        with Database("oa") as db, db.transaction(readonly=True):
            size = db.execute("SELECT octet_length(payload::text) AS bytes FROM {oa} WHERE record_type='WS_REPORT' "
                              "AND workspace_key='large-source'").fetchone()["bytes"]
        artifacts = Path("artifacts"); artifacts.mkdir(exist_ok=True)
        (artifacts / "workspace-100000-unique.json").write_text(json.dumps({
            "status": "PASSED", "records": 100000, "groups": 100000, "chunks": len(done["chunks"]),
            "classification_seconds": round(classification_seconds, 3), "workflow_seconds": round(time.monotonic()-began, 3),
            "source_json_bytes": size, "metadata_serialized_bytes": len(json.dumps(view).encode()),
            "group_page_count": len(groups["items"]), "record_page_count": len(last["items"]),
            "runner_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "reviewed": view["summary"]["reviewed"], "source_and_por_unchanged": True,
            "real_model_called": False}, indent=2)+"\n")

    def test_byte_budget_stops_before_source_fetch_or_partial_clustering(self):
        with patch('oa.sanity_workspace.MAX_REPORT_JSON_BYTES', 1):
            response = self.client.get(BASE+"large-source/workbench")
            self.assertEqual((413, "REPORT_LIMIT"), (response.status_code, response.json()["code"]))
            result = self.post("/clustering/tasks", {"expected_revision": 0, "request_id": str(uuid.uuid4())}, status=413)
            self.assertEqual("REPORT_LIMIT", result["code"])
        self.assertIsNone(self.get("/workbench")["task"])
        self.assertIsNone(self.get("/workbench")["current"])

    def test_failed_chunk_is_persisted_never_becomes_a_partition_and_requires_explicit_resume(self):
        task = self.task()
        with patch('oa.sanity_workspace_pipeline.ConfirmedPartitionAdvisor.propose', side_effect=ReviewError("INVALID_ADVICE", "Synthetic invalid response")):
            failed = self.post("/clustering/tasks/step", {"task_id": task["id"], "expected_chunk": 0})
        self.assertEqual(("FAILED", 0), (failed["status"], failed["processed"]))
        self.assertIsNone(self.get("/workbench")["current"])
        self.post("/clustering/tasks/step", {"task_id": task["id"], "expected_chunk": 0}, status=409)
        result = self.post("/clustering/tasks/step", {"task_id": task["id"], "expected_chunk": 0, "action": "RESUME"})
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(20, self.get("/workbench")["record_count"])

    def test_10000_case_reuse_handles_multiple_large_confirmed_reports(self):
        original = self.get("")
        with Database("oa") as db, db.transaction():
            for name in ("large-second", "large-third"):
                report = copy.deepcopy(original)
                report["id"] = name
                for record in report["records"]: record["id"] = record["id"].replace("large-source", name)
                WorkspaceStore(db).put("WS_REPORT", name, report, project="project-1", block="project-1-cpu")
        self.finish(self.task())
        self.post("/clustering", {"action": "CONFIRM", "expected_revision": 1, "request_id": str(uuid.uuid4()),
            "reason": "전체 분류 기준 확인", "reusable": True, "criteria": "확인한 객체별 구분"})
        self.post("/clustering/proposals", {"expected_revision": 0, "request_id": str(uuid.uuid4())}, report="large-second")
        self.post("/clustering", {"action": "CONFIRM", "expected_revision": 1, "request_id": str(uuid.uuid4()),
            "reason": "두 번째 report의 기준 확인", "reusable": True, "criteria": "확인한 객체별 구분"}, report="large-second")
        third = self.post("/clustering/tasks", {"expected_revision": 0, "request_id": str(uuid.uuid4())}, report="large-third")
        while third["status"] in ("PENDING", "RUNNING"):
            third = self.post("/clustering/tasks/step", {"task_id": third["id"], "expected_chunk": third["next_chunk"]}, report="large-third")
        self.assertEqual("COMPLETED", third["status"])
        view = self.get("/workbench", report="large-third")
        self.assertEqual((2, 100), (view["case_count"], view["current"]["group_count"]))
        self.assertEqual(2, len(view["current"]["references"]))

    def test_changed_case_catalog_marks_task_stale_and_never_applies_its_partial_output(self):
        proposal = self.post("/clustering/proposals", {"expected_revision": 0, "request_id": str(uuid.uuid4())})
        confirmed = self.post("/clustering", {"expected_revision": proposal["revision"], "request_id": str(uuid.uuid4()), "action": "CONFIRM", "reason": "초기 기준 확인", "reusable": True, "criteria": "확인한 객체별 분리"})
        task = self.post("/clustering/tasks", {"expected_revision": 0, "request_id": str(uuid.uuid4())}, report="large-target")
        self.post("/clustering", {"expected_revision": confirmed["revision"], "request_id": str(uuid.uuid4()), "action": "WITHDRAW", "reason": "분류 사례 철회"})
        result = self.post("/clustering/tasks/step", {"task_id": task["id"], "expected_chunk": 0}, report="large-target")
        self.assertEqual(("STALE", 0), (result["status"], result["processed"]))
        self.assertIsNone(self.get("/workbench", report="large-target")["current"])

    def test_task_scope_phase_gate_history_and_corporate_guards(self):
        task = self.task()
        self.post("/clustering", {"expected_revision": 0, "request_id": str(uuid.uuid4()), "action": "CONFIRM", "reason": "진행 중 확정 시도"}, status=409)
        top = self.login("user01")
        self.assertEqual(403, top.post(BASE+"large-source/clustering/tasks/step", json={"task_id": task["id"], "expected_chunk": 0}).status_code)
        self.post("/clustering/tasks", {"expected_revision": 0, "request_id": str(uuid.uuid4()), "mode": "CORPORATE"}, status=503)
        self.assertEqual(403, self.client.get(BASE.replace("project-1-cpu", "project-1-gpu")+"large-source/workbench").status_code)
        self.finish(task)
        gid = self.get("/clustering/groups?expected_revision=1")["items"][0]["id"]
        body = {"expected_revision": 0, "expected_cluster_revision": 1, "request_id": str(uuid.uuid4()), "conclusion": "PASS", "reason": "분류 확정 전 판정 시도"}
        self.post(f"/groups/{gid}/review?compact=true", body, status=409)
        history = self.get("/clustering/history")
        self.assertNotIn("groups", history["items"][0])
        old = self.get(f"/clustering/groups?expected_revision=999&event_id={history['items'][0]['id']}")
        self.assertEqual(2, old["total"])

    def test_changed_source_cannot_start_a_new_task_over_an_existing_partition(self):
        from common.sanity_db import json_value
        self.finish(self.task())
        self.admin_query("UPDATE {oa} SET payload=payload || %s WHERE record_type='WS_REPORT' AND workspace_key='large-source'",
                         (json_value({"raw_report": "Administrator changed the synthetic source"}),))
        result = self.post("/clustering/tasks", {"expected_revision": 1, "request_id": str(uuid.uuid4())}, status=503)
        self.assertEqual("STATE_CORRUPT", result["code"])

    def test_100000_records_keep_group_rationales_paged_and_bulk_adopted(self):
        began=time.monotonic()
        done=self.finish(self.task())
        view=self.get('/workbench')
        self.assertEqual((100000,100),(view['record_count'],view['summary']['total']))
        self.assertEqual({'MISSING':0,'DRAFT':100,'CONFIRMED':0,'STALE':0},view['summary']['rationale_counts'])
        self.assertNotIn('rationales',view['current'])
        self.assertLess(len(json.dumps(view)),40000)
        page=self.get('/clustering/groups?expected_revision=1&offset=75&limit=25')
        self.assertEqual((100,25),(page['total'],len(page['items'])))
        gid=page['items'][-1]['id']
        detail=self.get(f'/groups/{gid}/rationale?expected_revision=1&limit=1')
        self.assertEqual((3,'DRAFT'),(len(detail['samples']),detail['status']))
        self.post('/clustering?compact=true',{'action':'CONFIRM','expected_revision':1,'request_id':str(uuid.uuid4())})
        view=self.get('/workbench')
        self.assertEqual(100,view['summary']['rationale_counts']['CONFIRMED'])
        self.assertTrue(view['summary']['confirmed'])
        last=self.get('/clustering/records?expected_revision=2&offset=99950&limit=50')
        self.assertEqual((100000,50),(last['total'],len(last['items'])))
        Path('artifacts').mkdir(exist_ok=True)
        Path('artifacts/workspace-100000-rationales.json').write_text(json.dumps({
            'status':'PASSED','records':100000,'groups':100,'chunks':len(done['chunks']),
            'workflow_seconds':round(time.monotonic()-began,3),'metadata_bytes':len(json.dumps(view).encode()),
            'runner_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'model_calls':0})+'\n')
