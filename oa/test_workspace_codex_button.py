"""UI/API model requests use a stub transport; never make paid calls in regression."""
import json
import sys
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from devtools.sanity_codex_mock import CodexMockClusteringAdvisor, REPORT_IDS, fixture_report
from common.sanity_common import ReviewError, digest
from common.sanity_db import Database
from oa.sanity_workspace import seed_workspace
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from devtools.sanity_workspace_codex import WorkspaceCodexService
from oa.sanity_workspace_pipeline import WorkspacePipelineStore
from oa.test_workspace import fixture, api, ORIGIN
from oa.test_workspace_codex import answer, events
from tests.support import PgTestCase

BASE = '/workspace/api/blocks/project-1-cpu/versions/project-1-cpu-post-1/reports/'


class WorkspaceCodexButtonTests(PgTestCase):
    def setUp(self):
        super().setUp()
        data = fixture(); data['items'] = [fixture_report(name) for name in REPORT_IDS]
        data['dataset_sha256'] = digest({key: value for key, value in data.items() if key != 'dataset_sha256'})
        with Database('oa') as db, db.transaction(): seed_workspace(db, data)
        self.calls = []
        self.effect = None

    def transport(self, prompt, schema):
        value = json.loads(prompt.split('UNTRUSTED_SYNTHETIC_DATA_JSON:\n', 1)[1])
        self.calls.append(value)
        if self.effect:
            self.effect()
        output = answer({'report': value['report']})
        output['reference_ids'] = [case['id'] for case in value['human_confirmed_examples']]
        output['group_rationales'][0]['reference_groups'] = [{'case_id': case['id'], 'group_id': case['groups'][0]['id'], 'reason': '참고한 합성 확정 그룹'} for case in value['human_confirmed_examples']]
        return events(output)

    def factory(self):
        return CodexMockClusteringAdvisor(enabled=True, executable=sys.executable, transport=self.transport)

    def client(self, enabled=True, user='user04'):
        client = TestClient(api.create_app(origins=(ORIGIN,), codex_mock_enabled=enabled,
            codex_advisor_factory=self.factory), base_url=ORIGIN, headers={'Origin': ORIGIN})
        self.addCleanup(client.close)
        login = client.post('/workspace/api/login', json={'username': user, 'password': 'sanity-workspace'})
        client.headers['X-CSRF-Token'] = login.json()['csrf']
        return client

    def command(self, revision=0):
        return {'request_id': str(uuid.uuid4()), 'expected_revision': revision}

    def post(self, client, command=None, report=REPORT_IDS[0], status=202):
        response = client.post(BASE+report+'/clustering/ai', json=command or self.command())
        self.assertEqual(status, response.status_code, response.text)
        return response.json()

    def change(self, report=REPORT_IDS[0], **command):
        with Database('oa') as db, db.transaction():
            return WorkspaceClusteringStore(db).change('user-04', 'project-1-cpu', 'project-1-cpu-post-1', report,
                                                       request_id=str(uuid.uuid4()), **command)

    def test_disabled_app_never_calls_model_and_enabled_request_uuid_is_exactly_once(self):
        client = self.client(False)
        self.post(client, status=503)
        self.assertEqual([], self.calls)
        client = self.client()
        command = self.command()
        job = self.post(client, command)
        self.assertEqual('COMPLETED', job['status'])
        self.assertNotIn('receipt', job)
        self.assertEqual(job, self.post(client, command))
        self.post(client, {**command, 'expected_revision': 1}, status=409)
        self.post(client, command, report=REPORT_IDS[1], status=409)
        self.assertEqual(1, len(self.calls))
        view = client.get(BASE+REPORT_IDS[0]+'/workbench').json()
        self.assertTrue(view['current']['model_called'])
        self.assertFalse(view['summary']['confirmed'])
        self.assertEqual('gpt-6-luna', view['ai']['model'])

    def test_top_csrf_forged_actor_and_non_fixture_payload_cannot_enable_calls(self):
        self.post(self.client(user='user01'), status=403)
        client = self.client()
        self.post(client, {**self.command(), 'actor': 'user01'}, status=400)
        csrf = client.headers['X-CSRF-Token']; client.headers['X-CSRF-Token'] = 'invalid'
        self.post(client, status=403); client.headers['X-CSRF-Token'] = csrf
        other = fixture_report(REPORT_IDS[0]); other['id'] = 'not-a-codex-fixture'
        with Database('oa') as db, db.transaction():
            WorkspaceClusteringStore(db).ws.put('WS_REPORT', other['id'], other, project='project-1', block='project-1-cpu')
        self.post(client, report=other['id'], status=400)
        self.assertEqual([], self.calls)

    def test_active_reservation_blocks_duplicate_calls_other_jobs_and_manual_partition_writes(self):
        service = WorkspaceCodexService(enabled=True, advisor_factory=self.factory)
        command = dict(user_id='user-04', block_id='project-1-cpu', version_id='project-1-cpu-post-1',
                       report_id=REPORT_IDS[0], **self.command())
        job, created = service.reserve(**command)
        self.assertTrue(created)
        self.assertFalse(service.reserve(**command)[1])
        with self.assertRaises(ReviewError) as raised:
            service.reserve(**{**command, 'report_id': REPORT_IDS[1], 'request_id': str(uuid.uuid4())})
        self.assertEqual('AI_BUSY', raised.exception.code)
        with self.assertRaises(ReviewError) as raised: self.change(action='PROPOSE', expected_revision=0)
        self.assertEqual('CLUSTER_TASK_ACTIVE', raised.exception.code)
        with Database('oa') as db, db.transaction(), self.assertRaises(ReviewError) as raised:
            WorkspacePipelineStore(db).start_task('user-04', 'project-1-cpu', 'project-1-cpu-post-1', REPORT_IDS[0],
                                                  **self.command())
        self.assertEqual('CLUSTER_TASK_ACTIVE', raised.exception.code)
        self.assertEqual('COMPLETED', service.execute(job)['status'])
        self.assertEqual(1, len(self.calls))

    def test_human_edits_and_default_confirmation_are_sent_to_the_next_button_request(self):
        client = self.client()
        self.post(client)
        self.change(action='EDIT', expected_revision=1, reason='두 누락 유형을 함께 검토',
            groups=[{'id': 'human', 'label': '사람이 정한 제약 누락',
                     'record_ids': [row['id'] for row in fixture_report(REPORT_IDS[0])['records']]}])
        self.change(action='EDIT', expected_revision=2, reason='합친 그룹의 근거 작성',
            rationale_operation={'kind':'SAVE', 'group_id':'human', 'reviewed':True, 'content': {
                'reason':'클록과 입력 지연 누락을 제약 설정 누락으로 묶고, 폭 불일치와 구분합니다.',
                'evidence_ids':[fixture_report(REPORT_IDS[0])['records'][0]['id']], 'reference_groups':[]}})
        confirmed = self.change(action='CONFIRM', expected_revision=3, reason='사람이 정한 분류 확인')
        self.assertFalse(confirmed['reusable'])
        self.assertEqual('COMPLETED', self.post(client, report=REPORT_IDS[2])['status'])
        case = self.calls[-1]['human_confirmed_examples'][0]
        self.assertEqual((confirmed['id'], '사람이 정한 제약 누락'), (case['id'], case['groups'][0]['label']))
        self.assertEqual('사람이 정한 분류 확인', case['reason'])
        self.assertIn('폭 불일치와 구분', case['group_rationales'][0]['rationale']['content']['reason'])

    def test_case_changes_during_inference_leave_a_stale_job_without_partial_groups(self):
        client = self.client()
        def add_case():
            self.change(action='PROPOSE', expected_revision=0)
            self.change(action='CONFIRM', expected_revision=1, reason='호출 중 새 확정 사례')
        self.effect = add_case
        job = self.post(client, report=REPORT_IDS[2])
        self.assertEqual('STALE', job['status'])
        view = client.get(BASE+REPORT_IDS[2]+'/workbench').json()
        self.assertEqual((0, None), (view['revision'], view['current']))
        self.assertEqual(1, len(self.calls))

    def test_timeout_and_process_restart_never_repeat_an_uncertain_call(self):
        client = self.client()
        def timeout(): raise ReviewError('AI_OUTCOME_UNKNOWN', 'Synthetic timeout')
        self.effect = timeout
        command = self.command()
        self.assertEqual('UNKNOWN', self.post(client, command)['status'])
        self.assertEqual('UNKNOWN', self.post(client, command)['status'])
        self.assertEqual(1, len(self.calls))
        self.effect = None
        service = WorkspaceCodexService(enabled=True, advisor_factory=self.factory)
        second = dict(user_id='user-04', block_id='project-1-cpu', version_id='project-1-cpu-post-1',
                      report_id=REPORT_IDS[1], **self.command())
        service.reserve(**second)
        replacement = WorkspaceCodexService(enabled=True, advisor_factory=self.factory)
        replacement.recover()
        self.assertEqual(('UNKNOWN', False), (replacement.reserve(**second)[0]['status'], replacement.reserve(**second)[1]))
        self.assertEqual(1, len(self.calls))

    def test_a_received_result_can_be_saved_again_without_another_model_call(self):
        client = self.client(); command = self.command()
        with patch.object(WorkspaceClusteringStore, 'change', side_effect=ReviewError('DATABASE_ERROR', 'Synthetic failure')):
            self.assertEqual('SAVE_FAILED', self.post(client, command)['status'])
        self.assertEqual('COMPLETED', self.post(client, command)['status'])
        self.assertEqual(1, len(self.calls))

    def test_mock_budget_retains_uuid_history_and_model_errors_do_not_leak_sdk_details(self):
        client = self.client(); command = self.command()
        with patch('devtools.sanity_workspace_codex.MAX_JOBS', 1):
            self.post(client, command)
            self.post(client, status=429)
            self.post(client, command)
        self.assertEqual(1, len(self.calls))
        def failure(): raise RuntimeError('private-sdk-diagnostic-must-not-leak')
        self.effect = failure
        job = self.post(client, report=REPORT_IDS[1])
        self.assertEqual(('FAILED', 'INTERNAL_ERROR'), (job['status'], job['error']))
        self.assertNotIn('private-sdk', json.dumps(job))
