"""Real PostgreSQL and authenticated API checks for both feedback loops."""
from concurrent.futures import ThreadPoolExecutor
import copy
import uuid

from fastapi.testclient import TestClient

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database
from oa.sanity_workspace import SYNTHETIC_PASSWORD, seed_workspace
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from oa.test_workspace import api, fixture, ORIGIN
from tests.support import PgTestCase

BASE = "/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/"


class WorkspacePipelineTests(PgTestCase):
    def setUp(self):
        super().setUp()
        data = fixture()
        source = data["items"][0]
        source.update(rule_version="pipeline-v1", record_count=4)
        source["records"] = [{"id": f"e-{index}", "check_status": "PASS" if index == 4 else "FAIL",
            "fields": {"object": obj, "mode": "M1", "check_point": "P1", "code": "CLK_0003", "severity": "ERROR"},
            "raw": f"{obj} mode=M1 check=P1", "line_start": index, "line_end": index, "reasons": []}
            for index, obj in enumerate(("top/a", "top/a", "top/b", "top/c"), 1)]
        source["raw_report"] = "\n".join(record["raw"] for record in source["records"])
        for rid, mode in (("target", "M1"), ("other-mode", "M2")):
            target = copy.deepcopy(source); target["id"] = rid
            for record in target["records"]:
                record["id"] = rid + "-" + record["id"]; record["fields"]["mode"] = mode
            data["items"].append(target)
        missing = copy.deepcopy(source)
        missing.update(id="unreceived", records=[], record_count=0, raw_report=None, parse_status="MISSING", analysis_ready=False)
        data["items"].append(missing)
        data["dataset_sha256"] = digest({k: v for k, v in data.items() if k != "dataset_sha256"})
        with Database("oa") as db, db.transaction(): seed_workspace(db, data)
        self.client = self.client_for("user04")

    def client_for(self, username):
        client = TestClient(api.create_app(origins=(ORIGIN,)), base_url=ORIGIN, headers={"Origin": ORIGIN}, raise_server_exceptions=False)
        self.addCleanup(client.close)
        response = client.post("/workspace/api/login", json={"username": username, "password": SYNTHETIC_PASSWORD})
        self.assertEqual(200, response.status_code, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        return client

    def command(self, **changes):
        return {"request_id": str(uuid.uuid4()), "expected_revision": 0, **changes}

    def post(self, path, body, status=200):
        response = self.client.post(BASE + path, json=body)
        self.assertEqual(status, response.status_code, response.text)
        return response.json()

    def propose(self, report="sample-0001", **changes):
        return self.post(report + "/clustering/proposals", self.command(**changes))

    def confirm(self, report="sample-0001", **changes):
        draft = self.client.get(BASE + report + "/clustering").json()
        # This helper simulates a person's explicit supplementation after edits.
        # Dedicated tests below exercise the unmodified blocked HTTP requests.
        for group in draft["current"]["groups"]:
            row = draft["current"].get("rationales", {}).get(group["id"])
            if not row or row["status"] == "STALE":
                self.post(report + "/groups/" + group["id"] + "/rationale", self.command(
                    action="SAVE", expected_revision=draft["revision"], reason="사람이 변경된 분류 기준을 보완",
                    reviewed=True, content={"reason": group["label"] + ": 원문을 검토하여 함께 다룰 항목으로 분류했습니다. 다른 그룹과 별도 검토합니다.",
                        "evidence_ids": group["record_ids"][:2], "reference_groups": []}))
                draft = self.client.get(BASE + report + "/clustering").json()
        return self.post(report + "/clustering", self.command(action="CONFIRM", reason="원본 로그와 그룹 구성 확인",
            expected_revision=draft["revision"], **changes))

    def group_save(self, cluster, group=None, report="sample-0001", **changes):
        return self.post(report + "/groups/" + (group or cluster["groups"][0]["id"]) + "/review",
            self.command(expected_cluster_revision=cluster["revision"], conclusion="PASS", reason="확인된 허용 조건입니다.", **changes))

    def group_propose(self, cluster, report="target", group=None):
        return self.post(report + "/groups/" + (group or cluster["groups"][0]["id"]) + "/review-proposals",
                         self.command(expected_cluster_revision=cluster["revision"]))

    def test_partition_keeps_every_original_record_including_pass_and_top_reads_the_same_confirmation(self):
        source = self.client.get(BASE + "sample-0001").json()
        por = self.client.get("/workspace/api/blocks/project-1-cpu/versions").json()["por"]
        draft = self.propose()
        self.assertFalse(draft["confirmed"])
        self.assertFalse(draft["model_called"])
        self.assertEqual(3, len(draft["groups"]))
        self.assertCountEqual(["e-1", "e-2", "e-3", "e-4"], [key for group in draft["groups"] for key in group["record_ids"]])
        confirmed = self.confirm()
        top = self.client_for("user01")
        self.assertEqual(confirmed, top.get(BASE + "sample-0001/clustering").json()["current"])
        self.assertEqual(source, self.client.get(BASE + "sample-0001").json())
        self.assertEqual(por, self.client.get("/workspace/api/blocks/project-1-cpu/versions").json()["por"])

    def test_manual_merge_is_a_confirmed_clustering_case_for_the_next_report(self):
        draft = self.propose()
        merged = [{"id": "manual-merge", "label": "확인한 같은 문제", "record_ids": ["e-1", "e-2", "e-3"]},
                  {"id": "remaining", "label": "다른 문제", "record_ids": ["e-4"]}]
        self.post("sample-0001/clustering", self.command(action="EDIT", reason="관련 객체를 동일 문제로 확인", groups=merged, expected_revision=draft["revision"]))
        confirmed = self.confirm(reusable=True, criteria="이 객체 집합은 같은 원인의 문제입니다.")
        target = self.propose("target")
        self.assertEqual(2, len(target["groups"]))
        self.assertEqual([confirmed["id"]], [reference["id"] for reference in target["references"]])
        self.assertTrue(any(set(group["record_ids"]) == {"target-e-1", "target-e-2", "target-e-3"} for group in target["groups"]))
        self.assertEqual([], self.propose("other-mode")["references"])

    def test_missing_duplicate_or_foreign_evidence_cannot_be_confirmed_by_editing(self):
        draft = self.propose()
        for ids in (["e-1"], ["e-1", "e-2", "e-3", "e-4", "e-4"], ["e-1", "e-2", "e-3", "foreign-record"]):
            result = self.post("sample-0001/clustering", self.command(action="EDIT", reason="잘못된 구성 검증",
                expected_revision=draft["revision"], groups=[{"id": "invalid", "label": "잘못된 그룹", "record_ids": ids}]), 400)
            self.assertEqual("INVALID_PARTITION", result["code"])
        self.assertEqual(1, self.client.get(BASE + "sample-0001/clustering").json()["revision"])

    def test_known_modes_cannot_be_dropped_from_future_clustering_identity(self):
        self.post("sample-0001/clustering/proposals", self.command(identity_fields=["object"]), 400)
        self.assertEqual(0, self.client.get(BASE + "sample-0001/clustering").json()["revision"])

    def test_only_a_confirmed_group_accepts_a_judgment_and_it_does_not_become_a_report_judgment(self):
        draft = self.propose(); gid = draft["groups"][0]["id"]
        body = self.command(expected_cluster_revision=draft["revision"], conclusion="PASS", reason="확정 전 차단")
        self.post("sample-0001/groups/" + gid + "/review", body, 409)
        cluster = self.confirm()
        saved = self.group_save(cluster, gid)["event"]
        self.assertEqual((gid, cluster["id"], cluster["revision"]), (saved["group_id"], saved["cluster_event_id"], saved["cluster_revision"]))
        self.assertIsNone(self.client.get(BASE + "sample-0001/review").json()["current"])
        summary = self.client.get(BASE + "sample-0001/clustering").json()["summary"]
        self.assertEqual((1, 3), (summary["reviewed"], summary["total"]))

    def test_group_criteria_are_reused_with_their_exact_confirmed_partition_provenance(self):
        self.propose("target"); target = self.confirm("target")
        self.propose(); source = self.confirm()
        saved = self.group_save(source, reusable=True, criteria="同じ規則とモードで許容した例",
                                conditions={"rule": "CLK_0003", "mode": "M1"})["event"]
        proposal = self.group_propose(target)
        self.assertEqual("PASS", proposal["conclusion"])
        self.assertEqual([saved["id"]], proposal["reference_ids"])
        self.assertEqual(source["id"], proposal["references"][0]["cluster_event_id"])

    def test_reclustering_makes_previous_judgments_stale_and_preserves_removed_group_history(self):
        self.propose(); cluster = self.confirm(); old_group = cluster["groups"][0]["id"]
        original = self.group_save(cluster, old_group)["event"]
        self.post("sample-0001/clustering", self.command(action="EDIT", reason="다시 묶음", expected_revision=cluster["revision"],
            groups=[{"id": "replaced", "label": "새 그룹", "record_ids": ["e-1", "e-2", "e-3", "e-4"]}]))
        latest = self.confirm()
        old = self.client.get(BASE + "sample-0001/groups/" + old_group + "/review").json()
        self.assertTrue(old["current_stale"])
        self.assertFalse(old["can_review"])
        self.assertEqual(original, old["history"][0])
        self.assertEqual("GROUP_REMOVED", old["blocked_reason"])
        self.assertEqual(0, self.client.get(BASE + "sample-0001/clustering").json()["summary"]["reviewed"])
        self.assertEqual("PASS", self.group_save(latest, "replaced")["event"]["conclusion"])

    def test_a_group_that_keeps_its_id_still_requires_review_after_a_partition_revision(self):
        self.propose(); cluster = self.confirm(); group = cluster["groups"][0]["id"]
        self.group_save(cluster, group)
        self.post("sample-0001/clustering", self.command(action="EDIT", reason="이름과 구성 재확인", groups=cluster["groups"], expected_revision=cluster["revision"]))
        latest = self.confirm()
        old = self.client.get(BASE + "sample-0001/groups/" + group + "/review").json()
        self.assertTrue(old["current_stale"])
        body = self.command(expected_revision=1, expected_cluster_revision=cluster["revision"], conclusion="FAIL", reason="오래된 그룹 개정")
        self.post("sample-0001/groups/" + group + "/review", body, 409)
        saved = self.group_save(latest, group, expected_revision=1)["event"]
        self.assertEqual(latest["id"], saved["cluster_event_id"])

    def test_changing_a_source_partition_withdraws_its_group_criteria_from_future_proposals(self):
        self.propose("target"); target = self.confirm("target")
        self.propose(); source = self.confirm()
        self.group_save(source, reusable=True, criteria="허용 조건", conditions={"rule": "CLK_0003", "mode": "M1"})
        proposal = self.group_propose(target)
        self.post("sample-0001/clustering", self.command(action="WITHDRAW", reason="분류 재검토", expected_revision=source["revision"]))
        view = self.client.get(BASE + "target/groups/" + target["groups"][0]["id"] + "/review").json()
        self.assertTrue(view["proposal"]["stale"])
        self.assertEqual(0, view["case_count"])
        body = self.command(conclusion="PASS", reason="오래된 근거", proposal_id=proposal["id"], expected_cluster_revision=target["revision"])
        self.post("target/groups/" + target["groups"][0]["id"] + "/review", body, 409)

    def test_confirmed_examples_cannot_form_a_self_dependent_clustering_cycle(self):
        self.propose(); source = self.confirm(reusable=True, criteria="초기 분류 기준")
        target_draft = self.propose("target")
        self.assertEqual(1, len(target_draft["references"]))
        self.confirm("target", reusable=True, criteria="후속 분류 기준")
        new_source = self.propose(expected_revision=source["revision"])
        self.assertEqual([], new_source["references"])

    def test_request_replay_and_concurrent_classification_do_not_overwrite_a_revision(self):
        body = self.command()
        first = self.post("sample-0001/clustering/proposals", body)
        self.assertEqual(first, self.post("sample-0001/clustering/proposals", body))
        self.post("sample-0001/clustering/proposals", {**body, "identity_fields": ["object"]}, 409)
        def propose():
            try:
                with Database("oa") as db, db.transaction():
                    WorkspaceClusteringStore(db).change("user-04", "project-1-cpu", "project-1-cpu-post-1", "sample-0001",
                        action="PROPOSE", expected_revision=1, request_id=uuid.uuid4())
                return "SAVED"
            except ReviewError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: values = list(pool.map(lambda _: propose(), range(2)))
        self.assertCountEqual(["SAVED", "REVISION_CONFLICT"], values)

    def test_top_scope_csrf_forged_actor_and_unconfigured_models_remain_guarded(self):
        top = self.client_for("user01")
        self.assertEqual(403, top.post(BASE + "sample-0001/clustering/proposals", json=self.command()).status_code)
        self.assertEqual(403, self.client.get(BASE.replace("project-1-cpu", "project-1-gpu") + "sample-0001/clustering").status_code)
        self.post("sample-0001/clustering/proposals", self.command(actor="user01"), 400)
        response = self.client.post(BASE + "sample-0001/clustering/proposals", json=self.command(), headers={"X-CSRF-Token": "invalid"})
        self.assertEqual(403, response.status_code)
        self.post("sample-0001/clustering/proposals", self.command(mode="CORPORATE"), 503)
        self.assertEqual(0, self.client.get(BASE + "sample-0001/clustering").json()["revision"])
        self.post("unreceived/clustering/proposals", self.command(), 409)

    def test_rationale_confirmation_is_explicit_bulk_scoped_and_memo_is_optional(self):
        draft = self.propose()
        self.assertTrue(all(row['status'] == 'DRAFT' for row in draft['rationales'].values()))
        body = self.command(action='CONFIRM', expected_revision=1)
        event = self.post('sample-0001/clustering', body)
        self.assertEqual('', event['reason'])
        self.assertTrue(all(row['status'] == 'CONFIRMED' and row['reviewed_by']['id'] == 'user-04'
                            and row['binding']['cluster_event_id'] == event['id'] for row in event['rationales'].values()))
        self.assertEqual(event, self.post('sample-0001/clustering', body))
        view = self.client.get(BASE+'sample-0001/workbench').json()
        self.assertEqual({'MISSING':0, 'DRAFT':0, 'CONFIRMED':3, 'STALE':0}, view['summary']['rationale_counts'])
        self.assertNotIn('rationales', view['current'])

    def test_move_invalidates_only_affected_group_reasons_and_blocks_confirmation(self):
        self.propose(); before = self.confirm()
        source = next(g for g in before['groups'] if len(g['record_ids']) == 2)
        targets = [g for g in before['groups'] if g['id'] != source['id']]
        moved = self.post('sample-0001/clustering/operations', self.command(expected_revision=before['revision'],
            reason='원문을 확인한 로그 이동', operation={'kind':'MOVE','record_ids':[source['record_ids'][0]], 'destination_id':targets[0]['id']}))
        self.assertEqual({'MISSING':0, 'DRAFT':0, 'CONFIRMED':1, 'STALE':2}, moved['rationale_counts'])
        blocked = self.post('sample-0001/clustering', self.command(action='CONFIRM', expected_revision=moved['revision']), 409)
        self.assertEqual('RATIONALE_STALE', blocked['code'])
        unchanged = self.client.get(BASE+'sample-0001/groups/'+targets[1]['id']+'/rationale?expected_revision=3').json()
        self.assertEqual('CONFIRMED', unchanged['status'])
        self.assertEqual(before['id'], unchanged['rationale']['previous_event_id'])
        self.assertEqual(3, self.client.get(BASE+'sample-0001/clustering').json()['revision'])

    def test_human_rationale_edit_preserves_ai_or_engine_original_and_requires_reconfirmation(self):
        draft = self.propose(); before = self.confirm(); group = before['groups'][0]
        self.group_save(before, group['id'])
        body = self.command(action='SAVE', expected_revision=before['revision'], reason='원안의 구분 조건을 보완', reviewed=True,
            content={'reason':'같은 클록 설정의 누락을 묶되 다른 경로는 분리합니다. 동일 원인인지는 아직 확인되지 않았습니다.',
                     'evidence_ids':[group['record_ids'][0]], 'reference_groups':[]})
        saved = self.post('sample-0001/groups/'+group['id']+'/rationale', body)
        self.assertEqual(3, saved['revision'])
        self.assertFalse(saved['confirmed'])
        detail = self.client.get(BASE+'sample-0001/groups/'+group['id']+'/rationale?expected_revision=3&limit=1').json()
        self.assertEqual('CONFIRMED', detail['status'])
        self.assertEqual(draft['rationales'][group['id']]['content'], detail['rationale']['proposal']['content'])
        self.assertEqual('HUMAN', detail['rationale']['source'])
        self.assertEqual(1, len(detail['history']['items']))
        self.assertEqual(saved, self.post('sample-0001/groups/'+group['id']+'/rationale', body))
        self.assertFalse(self.client.get(BASE+'sample-0001/workbench').json()['summary']['confirmed'])
        self.post('sample-0001/groups/'+group['id']+'/review', self.command(expected_revision=1,
            expected_cluster_revision=3, conclusion='PASS', reason='분류 재확정 전 차단'), 409)
        final = self.confirm()
        self.assertEqual(detail['rationale']['content'], final['rationales'][group['id']]['content'])
        review = self.group_save(final, group['id'], expected_revision=1)['event']
        self.assertEqual(final['rationales'][group['id']], review['clustering_rationale'])

    def test_missing_or_foreign_rationale_is_atomic_and_cannot_be_used_by_top(self):
        event = self.propose(); group = event['groups'][0]
        self.post('sample-0001/clustering', self.command(action='EDIT', expected_revision=1, reason='명시적 새 그룹',
            groups=[{'id':'new','label':'새 분류','record_ids':['e-1','e-2','e-3','e-4']}]))
        error = self.post('sample-0001/clustering', self.command(action='CONFIRM', expected_revision=2), 409)
        self.assertEqual('RATIONALE_REQUIRED', error['code'])
        body = self.command(action='SAVE', expected_revision=2, reason='검증 대상', reviewed=True,
            content={'reason':'분류 근거', 'evidence_ids':['foreign'], 'reference_groups':[]})
        path='sample-0001/groups/new/rationale'
        self.assertEqual('INVALID_RATIONALE', self.post(path, body, 400)['code'])
        self.assertEqual(2, self.client.get(BASE+'sample-0001/clustering').json()['revision'])
        top = self.client_for('user01')
        self.assertEqual(403, top.post(BASE+path, json=body).status_code)
        body['content']['evidence_ids']=['e-1']
        body['content']['reference_groups']=[{'case_id':str(uuid.uuid4()),'group_id':group['id'],'reason':'존재하지 않는 사례'}]
        self.assertEqual('INVALID_RATIONALE', self.post(path, body, 400)['code'])

    def test_same_group_move_is_a_noop_and_does_not_invalidate_reviewed_reasons(self):
        self.propose(); before = self.confirm(); group=before['groups'][0]
        body=self.command(expected_revision=2, reason='같은 그룹으로 이동', operation={
            'kind':'MOVE','record_ids':[group['record_ids'][0]],'destination_id':group['id']})
        first=self.post('sample-0001/clustering/operations', body)
        self.assertEqual(first, self.post('sample-0001/clustering/operations', body))
        self.assertEqual((2,True),(first['revision'],first['confirmed']))

    def test_legacy_confirmed_groups_stay_in_history_but_require_new_rationale_for_writes(self):
        from common.sanity_db import json_value
        from oa.sanity_workspace import WorkspaceStore, record_id
        legacy=self.propose(); legacy=copy.deepcopy(legacy)
        legacy.update(id=str(uuid.uuid4()), revision=2, confirmed=True, previous_event_id=legacy['id'])
        legacy.pop('rationales'); legacy.pop('rationale_schema')
        with Database('oa') as db,db.transaction():
            WorkspaceStore(db).put('WS_CLUSTER_EVENT',legacy['id'],legacy,project=legacy['project_id'],block=legacy['block_id'])
            db.execute("UPDATE {oa} SET state=%s,version=2 WHERE record_type='WS_CLUSTER' AND workspace_key='sample-0001'",
                (json_value({'event_id':legacy['id'],'event_record_id':str(record_id('WS_CLUSTER_EVENT',legacy['id']))}),))
        view=self.client.get(BASE+'sample-0001/workbench').json()
        self.assertTrue(view['current']['confirmed'])
        self.assertFalse(view['summary']['confirmed'])
        self.assertEqual(3,view['summary']['rationale_counts']['MISSING'])
        self.post('sample-0001/groups/'+legacy['groups'][0]['id']+'/review', self.command(
            expected_cluster_revision=2, conclusion='PASS', reason='구버전 무근거 판정 차단'), 409)
        confirmed=self.confirm()
        self.assertTrue(confirmed['confirmed'])
        history=self.client.get(BASE+'sample-0001/clustering').json()['history']
        self.assertEqual(legacy,next(row for row in history if row['id']==legacy['id']))

    def test_packed_rationales_keep_logical_api_history_retry_and_case_contracts(self):
        from unittest.mock import patch
        with patch('common.sanity_workspace_rationale.PACK_THRESHOLD',1):
            self.propose(); confirmed=self.confirm(); group=confirmed['groups'][0]
            with Database('oa') as db,db.transaction(readonly=True):
                stored=db.execute("SELECT payload FROM {oa} WHERE record_type='WS_CLUSTER_EVENT' AND workspace_key=%s",(confirmed['id'],)).fetchone()['payload']
                self.assertEqual('bucket-zlib-v1',stored['rationale_encoding'])
                self.assertIsInstance(stored['rationales'][group['id']],str)
            body=self.command(action='SAVE',expected_revision=2,reviewed=True,reason='압축 저장 상태에서도 원안을 보존하는 합성 정정',
                content={'reason':'현재 그룹의 원문을 묶어 별도로 조사하며 동일 원인은 미확인입니다.',
                         'evidence_ids':[group['record_ids'][0]],'reference_groups':[]})
            first=self.post('sample-0001/groups/'+group['id']+'/rationale',body)
            self.assertEqual(first,self.post('sample-0001/groups/'+group['id']+'/rationale',body))
            detail=self.client.get(BASE+'sample-0001/groups/'+group['id']+'/rationale?expected_revision=3&limit=2').json()
            self.assertEqual(body['content'],detail['rationale']['content'])
            self.assertEqual(body['content'],detail['history']['items'][0]['rationale']['content'])
            self.assertEqual(confirmed['rationales'][group['id']]['content'],detail['rationale']['proposal']['content'])
            history=self.client.get(BASE+'sample-0001/clustering/history?limit=1').json()
            self.assertNotIn('rationale_chunks',history['items'][0])
            final=self.confirm()
            with Database('oa') as db,db.transaction(readonly=True):
                store=WorkspaceClusteringStore(db)
                context=store.context('user-04','project-1-cpu','project-1-cpu-post-1','target')
                cases=store.catalog(context)
                self.assertEqual([final['id']],[case['id'] for case in cases])
                self.assertEqual(body['content'],cases[0]['rationales'][group['id']]['content'])
