"""Human review CLI boundaries, and a real queue round trip into production stores."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from common.sanity_cli_review import PROTOCOL, signature, signing_key, validate_envelope
from common.sanity_common import ReviewError, canonical_json
from vwp.review_cli import LocalSession, ReviewClient, command, display, parser
from tests.support import PgTestCase, ROOT


SECRET = 's'*43
CONFIG = {'schema_version': 3, 'transport': 'https', 'endpoint': 'https://bridge.example.test',
          'ca_file': None, 'timeout_seconds': 5, 'default_profile': 'synthetic', 'submission_auth': 'internal_network'}


def envelope(session, action, args=None, rid=None):
    value = {'schema_version': 1, 'protocol': PROTOCOL, 'session_id': session['id'],
        'request_id': rid or str(uuid4()), 'expires_at': session['expires_at'],
        'command': {'action': action, 'args': args or {}}}
    value['signature'] = signature(signing_key(session['secret']), value)
    return value


class CliReviewTests(unittest.TestCase):
    def assert_code(self, code, callback):
        with self.assertRaises(ReviewError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def test_vwp_entrypoint_and_transport_need_only_standard_library(self):
        script = ('import sys,json; import vwp.review_cli,vwp.review_https; '
                  'print(json.dumps(sorted(sys.modules)))')
        result = subprocess.run([sys.executable, '-S', '-c', script], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        loaded = json.loads(result.stdout)
        self.assertFalse(any(name.split('.')[0] in {'psycopg2', 'oa', 'vwp_bridge', 'pydantic', 'httpx', 'fastapi'} for name in loaded))

    def test_envelope_binds_every_command_field_and_has_a_closed_contract(self):
        value = envelope({'id': str(uuid4()), 'secret': SECRET, 'expires_at': int(time.time())+3600}, 'STATUS')
        self.assertIs(validate_envelope(value), value)
        for field, replacement in (('request_id', str(uuid4())), ('session_id', str(uuid4())),
                ('expires_at', value['expires_at']+1), ('command', {'action': 'JUDGE', 'args': {'conclusion': 'PASS'}})):
            changed = {**value, field: replacement}
            self.assertNotEqual(value['signature'], signature(signing_key(SECRET), changed))
        self.assert_code('INVALID_REQUEST', lambda: validate_envelope({**value, 'user_id': 'forged'}))
        self.assert_code('INVALID_REQUEST', lambda: validate_envelope({**value, 'expires_at': True}))
        self.assert_code('CLI_REVIEW_AUTH_REQUIRED', lambda: signing_key('bad key'))

    def test_timeout_keeps_exact_uuid_and_restart_resumes_without_a_second_edit(self):
        with tempfile.TemporaryDirectory() as root:
            with LocalSession(root) as local:
                transport = Mock()
                transport.request.return_value = {'state': 'DONE', 'result': {'revision': 1}}
                client = ReviewClient(local, CONFIG, transport)
                client.connect(str(uuid4()), int(time.time())+3600, SECRET)
                transport.request.return_value = {'state': 'PENDING'}
                self.assert_code('CLI_REVIEW_PENDING', lambda: client.send('CONFIRM', {'expected_revision': 1, 'reason': '확인'}, wait_seconds=0))
                pending = deepcopy(local.value['pending'])
                self.assert_code('CLI_REVIEW_PENDING', lambda: client.send('CONFIRM', {'expected_revision': 1, 'reason': '다른 요청'}))
            self.assertEqual(0o600, (Path(root)/'session.json').stat().st_mode & 0o777)
            self.assertNotIn(SECRET, (Path(root)/'session.json').read_text())
            with LocalSession(root) as local:
                transport.request.return_value = {'state': 'DONE', 'result': {'revision': 2}}
                client = ReviewClient(local, CONFIG, transport)
                result = client.resume(wait_seconds=0)
                self.assertEqual(2, result['revision'])
                self.assertEqual(pending, transport.request.call_args.args[0])
                self.assertIsNone(local.value['pending'])
                for gid in ('first', 'last'):
                    transport.request.return_value = {'state': 'DONE', 'result': {'group_id': gid,
                        'cluster_revision': 2, 'review': {'revision': 0}, 'evidence': {}}}
                    client.send('GROUP', {'expected_revision': 2, 'group_id': gid})
                self.assertEqual({'last'}, set(local.value['groups']))
                self.assert_code('CLI_REVIEW_GROUP_REQUIRED', lambda: command(client,
                    parser().parse_args(['judge', 'first', 'PASS', '--reason', '다시 조회 필요'])))

    def test_group_revision_is_required_and_explicit_id_files_are_never_search_selections(self):
        client = Mock()
        client.pinned.return_value = 7
        client.state = {'groups': {}}
        self.assert_code('CLI_REVIEW_GROUP_REQUIRED', lambda: command(client, parser().parse_args(['judge', 'g', 'PASS', '--reason', '확인'])))
        with tempfile.TemporaryDirectory() as root:
            ids = Path(root)/'ids.txt'
            ids.write_text('a\nb\n')
            args = parser().parse_args(['split', '--records-file', str(ids), '--label', '원인 A', '--reason', '분리 확인'])
            command(client, args)
            action, payload = client.send.call_args.args
            self.assertEqual('EDIT', action)
            self.assertEqual(7, payload['expected_revision'])
            self.assertEqual(['a', 'b'], payload['operation']['record_ids'])
            self.assertTrue(payload['operation']['new_group_id'].startswith('cli-'))
            ids.write_text('a\na\n')
            self.assert_code('INVALID_PARTITION', lambda: command(client, args))

    def test_private_state_lock_permissions_symlinks_and_terminal_control_escaping(self):
        with tempfile.TemporaryDirectory() as root:
            with LocalSession(root):
                self.assert_code('CLI_REVIEW_BUSY', lambda: LocalSession(root).__enter__())
            path = Path(root)/'session.json'
            path.write_text('{}')
            path.chmod(0o644)
            self.assert_code('CLI_REVIEW_UNSAFE_STATE', lambda: LocalSession(root).__enter__())
            path.unlink()
            path.symlink_to(Path(root)/'lock')
            self.assert_code('UNSAFE_SUBMISSION_FILE', lambda: LocalSession(root).__enter__())
        output = io.StringIO()
        with redirect_stdout(output):
            display({'record': {'id': 'r1'}, 'raw': '한글\n\x1b[2J\u202e', 'offset': 0, 'limit': 10, 'total': 99})
        self.assertNotIn('\x1b', output.getvalue())
        self.assertNotIn('\u202e', output.getvalue())
        self.assertIn('계속 읽기: record r1 --offset 10', output.getvalue())


class CliReviewApiTests(unittest.TestCase):
    def test_session_creation_and_revocation_use_the_sso_actor_closed_body_and_csrf(self):
        from tests.test_ai_workbench_api import ProductionWorkbenchApiTests, BASE, HEADERS, TARGET
        fixture = ProductionWorkbenchApiTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        store = Mock()
        store.create.return_value = {'id': str(uuid4()), 'secret': SECRET}
        store.sessions.return_value = {'items': []}
        store.revoke.return_value = {'revoked': True}
        path = BASE+'/cli-sessions'
        with patch('oa.sanity_cli_review.CliReviews', return_value=store):
            self.assertEqual(403, fixture.client.post(path, json={}).status_code)
            self.assertEqual(400, fixture.client.post(path, headers=HEADERS, json={'user_id': 'forged'}).status_code)
            self.assertEqual(201, fixture.client.post(path, headers=HEADERS, json={}).status_code)
            store.create.assert_called_once_with(*TARGET)
            self.assertEqual(200, fixture.client.get(path).status_code)
            store.sessions.assert_called_once_with(*TARGET)
            sid = str(uuid4())
            self.assertEqual(200, fixture.client.post(path+'/'+sid+'/revoke', headers=HEADERS, json={}).status_code)
            store.revoke.assert_called_once_with(*TARGET, sid)
            fixture.actor = type('LocalActor', (), {'auth_method': 'local_token', 'user_id': TARGET[0], 'access': fixture.access})()
            self.assertEqual(403, fixture.client.post(path, headers=HEADERS, json={}).status_code)


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class CliReviewDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_production_workspace import ProductionWorkspaceTests, OWNER
        from common.sanity_db import Database
        from oa.sanity_workspace import record_id
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        from oa.sanity_cli_review import CliReviews
        self.c = ProductionWorkspaceTests()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.c.admin.execute((ROOT/'db/014_cli_reviews.sql').read_text().replace('public.', self.c.schema+'.'))
        self.target = (OWNER.user_id, 'block.A1', 'block.A1.v1', 'cli-report')
        self.raw = '합성 전체 원문\n'+('긴 로그 한글\n'*1200)
        with self.c.store() as ws:
            report = {'id': 'cli-report', 'project_id': 'project.A', 'block_id': 'block.A1', 'version_id': 'block.A1.v1',
                'version_record_id': str(record_id('WS_VERSION', 'block.A1.v1')), 'eco': None, 'item_id': 'clock_check',
                'family': 'GCA', 'parser': 'synthetic-v1', 'rule_version': '1', 'rule': 'C1', 'parse_status': 'COMPLETE',
                'check_status': 'FAIL', 'analysis_ready': True, 'raw_report': '합성 report', 'record_count': 4,
                'records': [{'id': key, 'raw': self.raw if key == 'a' else '다른 합성 원문 '+key,
                    'line_start': index+1, 'line_end': index+1, 'check_status': 'FAIL',
                    'fields': {'object': 'top/'+key, 'check_point': 'clock', 'mode': 'M1', 'code': 'C1'}}
                    for index, key in enumerate(('a', 'b', 'c', 'd'))]}
            ws.put('WS_REPORT', 'cli-report', report, project='project.A', block='block.A1')
            self.initial = WorkspaceClusteringStore(ws.db, ws=ws).change(*self.target, action='PROPOSE',
                expected_revision=0, request_id=uuid4(), prepared={'reason': '합성 분류 초안', 'reference_ids': [],
                'groups': [{'id': 'g1', 'label': '동일 원인 A', 'record_ids': ['a', 'b']},
                           {'id': 'g2', 'label': '다른 원인 B', 'record_ids': ['c', 'd']}],
                'group_rationales': [{'group_id': group, 'reason': '동일 원인과 다른 예외를 확인합니다.',
                    'evidence_ids': [record], 'reference_groups': []} for group, record in (('g1', 'a'), ('g2', 'c'))]})
            self.session = CliReviews(ws).create(*self.target)

    def relay(self, method, *args):
        from common.sanity_db import Database
        from vwp_bridge.review_relay import ReviewRelay
        with Database('vwp') as db, db.transaction():
            return getattr(ReviewRelay(db), method)(*args)

    def work(self):
        from common.sanity_db import Database
        from oa.sanity_access import AccessStore
        from oa.sanity_cli_review import receive_one
        from oa.sanity_production_workspace import ProductionWorkspaceStore
        from tests.test_production_workspace import ADMIN
        with Database('oa') as db:
            return receive_one(db, lambda connection: ProductionWorkspaceStore(connection, AccessStore(connection, ADMIN.user_id)))

    def call(self, action, args=None):
        value = envelope(self.session, action, args)
        self.assertEqual('PENDING', self.relay('enqueue', value)['state'])
        self.assertTrue(self.work())
        response = self.relay('poll', value['request_id'], value['signature'])
        self.assertEqual('DONE', response['state'], response)
        self.last = value
        return response['result']

    def rejected(self, value, code):
        self.relay('enqueue', value)
        self.work()
        result = self.relay('poll', value['request_id'], value['signature'])
        self.assertEqual(('ERROR', code), (result['state'], result.get('code')), result)
        self.assertNotIn('result', result)

    def test_paged_full_text_confirmation_judgment_and_opt_in_share_the_web_history(self):
        from oa.sanity_cli_review import CliReviews
        from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
        from oa.sanity_ai_evidence_choices import EvidenceChoices
        page = self.call('RECORDS', {'expected_revision': 1, 'offset': 0, 'limit': 2, 'search': '합성'})
        self.assertEqual((4, 2), (page['total'], len(page['items'])))
        self.assertTrue(page['items'][0]['raw_display_truncated'])
        recovered = ''
        while len(recovered) < len(self.raw):
            part = self.call('RECORD', {'expected_revision': 1, 'record_id': 'a', 'offset': len(recovered), 'limit': 4096})
            recovered += part['raw']
        self.assertEqual(self.raw, recovered)
        self.call('CONFIRM', {'expected_revision': 1, 'reason': '전체 구성과 분류 근거 확인'})
        self.call('GROUP', {'expected_revision': 2, 'group_id': 'g1'})
        result = self.call('JUDGE', {'group_id': 'g1', 'expected_cluster_revision': 2, 'expected_revision': 0,
            'conclusion': 'FAIL', 'reason': '전체 클록 근거 확인 후 실패로 확정'})
        self.assertEqual(self.target[0], result['event']['actor']['id'])
        self.assertEqual('FAIL', result['event']['conclusion'])
        repeated = self.relay('enqueue', self.last)
        self.assertEqual(result, repeated['result'])
        self.assertFalse(self.work())
        with self.c.store() as ws:
            event, revision = WorkspaceGroupReviewStore(ws.db, 'g1', ws=ws).current('cli-report')
            self.assertEqual((1, 'FAIL'), (revision, event['conclusion']))
            self.assertFalse(EvidenceChoices(ws.db, ws).view(*self.target, 'g1', 'REVIEW')['enabled'])
            sessions = CliReviews(ws).sessions(*self.target)
            self.assertNotIn('secret', sessions['items'][0])
            self.assertNotIn('signing_key', sessions['items'][0])
        group = self.call('GROUP', {'expected_revision': 2, 'group_id': 'g1'})
        choice = group['evidence']['REVIEW']
        self.call('EVIDENCE', {'group_id': 'g1', 'stage': 'REVIEW', 'enabled': True,
            'expected_revision': choice['revision'], 'binding_sha256': choice['binding_sha256']})
        with self.c.store() as ws:
            self.assertTrue(EvidenceChoices(ws.db, ws).view(*self.target, 'g1', 'REVIEW')['enabled'])
        text = canonical_json(self.c.admin_query('SELECT payload FROM {vwp_to_oa}').fetchall()).decode()
        self.assertNotIn(self.session['secret'], text)
        self.assertNotIn(signing_key(self.session['secret']), text)

    def test_split_merge_move_and_rename_keep_all_records_and_require_updated_rationales(self):
        split = self.call('EDIT', {'expected_revision': 1, 'reason': '명시한 로그 분리', 'operation': {
            'kind': 'MOVE', 'record_ids': ['a'], 'new_group_id': 'separated', 'label': '별도 원인'}})
        self.assertEqual(2, split['revision'])
        self.rejected(envelope(self.session, 'CONFIRM', {'expected_revision': 2, 'reason': '근거 없는 확정'}), 'RATIONALE_STALE')
        self.call('RATIONALE', {'expected_revision': 2, 'group_id': 'separated', 'action': 'SAVE', 'reason': '분리 근거 확인',
            'content': {'reason': 'a는 별도의 실행 조건이므로 분리합니다.', 'evidence_ids': ['a'], 'reference_groups': []}})
        self.call('EDIT', {'expected_revision': 3, 'reason': '다시 함께 검토', 'operation': {
            'kind': 'MERGE', 'group_ids': ['separated', 'g1'], 'destination_id': 'g1'}})
        self.call('EDIT', {'expected_revision': 4, 'reason': '기존 그룹으로 이동', 'operation': {
            'kind': 'MOVE', 'record_ids': ['b'], 'destination_id': 'g2'}})
        self.call('EDIT', {'expected_revision': 5, 'reason': '구체적인 이름', 'operation': {
            'kind': 'RENAME', 'destination_id': 'g2', 'label': '검토 대상 B'}})
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        with self.c.store() as ws:
            event, revision = WorkspaceClusteringStore(ws.db, ws).current('cli-report')
            self.assertEqual(6, revision)
            self.assertEqual(['a', 'b', 'c', 'd'], sorted(key for group in event['groups'] for key in group['record_ids']))
            self.assertFalse(event['confirmed'])
            self.assertEqual(6, ws.db.execute("SELECT count(*) AS n FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()['n'])
            self.assertEqual(self.target[0], event['actor']['id'])

    def test_changed_web_judgment_and_cluster_revision_do_not_get_overwritten(self):
        from oa.sanity_workspace_group_review import WorkspaceGroupReviewStore
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        self.call('CONFIRM', {'expected_revision': 1, 'reason': '확정'})
        with self.c.store() as ws:
            WorkspaceGroupReviewStore(ws.db, 'g1', 2, ws=ws).save(*self.target, conclusion='FAIL', reason='웹의 새 판단',
                criteria='', conditions={}, reusable=False, compatible_versions=False, expected_revision=0, request_id=uuid4())
        self.rejected(envelope(self.session, 'JUDGE', {'group_id': 'g1', 'expected_cluster_revision': 2,
            'expected_revision': 0, 'conclusion': 'PASS', 'reason': '오래된 CLI 판단'}), 'REVISION_CONFLICT')
        with self.c.store() as ws:
            WorkspaceClusteringStore(ws.db, ws).change(*self.target, action='EDIT', expected_revision=2,
                request_id=uuid4(), reason='웹에서 그룹명 수정', operation={'kind': 'RENAME', 'destination_id': 'g1', 'label': '수정'})
        self.rejected(envelope(self.session, 'GROUPS', {'expected_revision': 2}), 'REVISION_CONFLICT')
        with self.c.store() as ws:
            current, revision = WorkspaceGroupReviewStore(ws.db, 'g1', ws=ws).current('cli-report')
            self.assertEqual((1, 'FAIL'), (revision, current['conclusion']))

    def test_signature_actor_replay_revocation_and_expiry_cannot_authorize_a_review(self):
        from oa.sanity_cli_review import CliReviews
        value = envelope(self.session, 'STATUS')
        value['command'] = {'action': 'CONFIRM', 'args': {'expected_revision': 1, 'reason': 'forged'}}
        self.rejected(value, 'CLI_REVIEW_AUTH_REQUIRED')
        value = envelope(self.session, 'STATUS', {'user_id': 'forged'})
        self.rejected(value, 'INVALID_REQUEST')
        value = envelope(self.session, 'STATUS')
        self.relay('enqueue', value)
        with self.assertRaises(ReviewError) as caught:
            self.relay('poll', value['request_id'], '0'*64)
        self.assertEqual('NOT_FOUND', caught.exception.code)
        with self.c.store() as ws:
            CliReviews(ws).revoke(*self.target, self.session['id'])
        self.work()
        self.assertEqual('CLI_REVIEW_REVOKED', self.relay('poll', value['request_id'], value['signature'])['code'])
        with self.c.store() as ws:
            second = CliReviews(ws).create(*self.target)
            with patch('common.sanity_cli_review.time.time', return_value=second['expires_at']+1):
                with self.assertRaises(ReviewError) as caught:
                    CliReviews(ws).execute(envelope(second, 'STATUS'))
                self.assertEqual('CLI_REVIEW_EXPIRED', caught.exception.code)

    def test_queued_read_rechecks_revoked_owner_grant_and_never_returns_raw(self):
        from tests.test_production_workspace import ADMIN
        value = envelope(self.session, 'RECORDS', {'expected_revision': 1})
        self.relay('enqueue', value)
        with self.c.store(management=True) as ws:
            access = ws.authorization
            access.revoke(ADMIN, self.c.owner_grant, uuid4(), 'CLI 도중 담당 회수', access.snapshot(ADMIN.user_id)['revision'])
        self.work()
        response = self.relay('poll', value['request_id'], value['signature'])
        self.assertEqual('RESOURCE_NOT_FOUND', response['code'])
        self.assertNotIn('result', response)
        self.assertNotIn('합성', canonical_json(response).decode())

    def test_top_and_unverified_source_identity_cannot_create_a_cli_session(self):
        from oa.sanity_cli_review import CliReviews
        from tests.test_production_workspace import TOP
        with self.c.store() as ws:
            for user in (TOP.user_id, 'unverified-os-user'):
                with self.assertRaises(ReviewError):
                    CliReviews(ws).create(user, *self.target[1:])

    def test_ten_thousand_logs_are_paged_and_an_explicit_last_record_move_preserves_the_partition(self):
        from oa.sanity_cli_review import CliReviews
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        ids = ['record-'+str(index).zfill(5) for index in range(10001)]
        self.target = (*self.target[:-1], 'large-cli-report')
        with self.c.store() as ws:
            report = deepcopy(ws.get('WS_REPORT', 'cli-report')['payload'])
            report.update(id='large-cli-report', raw_report='독립 합성 10001건', record_count=len(ids),
                records=[{'id': key, 'raw': '독립 합성 로그 '+key, 'check_status': 'FAIL',
                    'fields': {'object': 'top/'+key, 'code': 'C1', 'mode': 'M1'}} for key in ids])
            ws.put('WS_REPORT', report['id'], report, project='project.A', block='block.A1')
            WorkspaceClusteringStore(ws.db, ws).change(*self.target, action='PROPOSE', expected_revision=0,
                request_id=uuid4(), prepared={'reason': '전체 입력을 두 원인으로 분류', 'reference_ids': [],
                'groups': [{'id': 'large-a', 'label': '원인 A', 'record_ids': ids[:5000]},
                           {'id': 'large-b', 'label': '원인 B', 'record_ids': ids[5000:]}],
                'group_rationales': [{'group_id': group, 'reason': '합성 실행 조건별 공통 원인과 차이를 확인합니다.',
                    'evidence_ids': [record], 'reference_groups': []} for group, record in (('large-a', ids[0]), ('large-b', ids[-1]))]})
            self.session = CliReviews(ws).create(*self.target)
        for offset, length in ((0, 100), (4900, 100), (10000, 1)):
            page = self.call('RECORDS', {'expected_revision': 1, 'offset': offset, 'limit': 100})
            self.assertEqual(10001, page['total'])
            self.assertEqual(ids[offset:offset+length], [r['id'] for r in page['items']])
        found = self.call('RECORDS', {'expected_revision': 1, 'search': ids[-1]})
        self.assertEqual([ids[-1]], [r['id'] for r in found['items']])
        self.call('EDIT', {'expected_revision': 1, 'reason': '마지막 로그의 원인 재분류',
            'operation': {'kind': 'MOVE', 'record_ids': [ids[-1]], 'destination_id': 'large-a'}})
        with self.c.store() as ws:
            current, revision = WorkspaceClusteringStore(ws.db, ws).current(self.target[-1])
            members = [key for group in current['groups'] for key in group['record_ids']]
            self.assertEqual((2, 10001, 10001), (revision, len(members), len(set(members))))
            self.assertEqual(set(ids), set(members))
            self.assertEqual(5001, len(current['groups'][0]['record_ids']))

    def test_source_change_rejects_the_existing_session_instead_of_rebinding_it(self):
        from common.sanity_db import json_value
        report = self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' AND workspace_key='cli-report'").fetchone()[0]
        report['raw_report'] = '다른 합성 원본'
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT' AND workspace_key='cli-report'", (json_value(report),))
        self.rejected(envelope(self.session, 'STATUS'), 'CLI_REVIEW_SOURCE_CHANGED')

    def test_response_limit_rolls_back_the_mutation_and_keeps_a_bounded_error_receipt(self):
        from oa.sanity_cli_review import CliReviews
        from common.sanity_cli_review import MAX_RESPONSE
        def oversized(service, *args):
            service.ws.put('WS_CONFIG', 'cli-test-response-limit', {'synthetic': True})
            return {'text': 'x'*(MAX_RESPONSE+1)}
        with patch.object(CliReviews, 'dispatch', oversized):
            self.rejected(envelope(self.session, 'STATUS'), 'CLI_REVIEW_LIMIT')
        self.assertEqual(0, self.c.admin_query("SELECT count(*) FROM {oa} WHERE workspace_key='cli-test-response-limit'").fetchone()[0])

    def test_real_tls_cli_connect_query_confirm_and_close_never_send_a_submission_token(self):
        from tests.submission_https_support import tls_server
        from vwp_bridge.submission_api import create_app
        from vwp.review_https import ReviewTransport
        from common.sanity_db import Database
        self.c.admin.execute((ROOT/'db/010_raw_sources.sql').read_text().replace('public.', self.c.schema+'.'))
        with tempfile.TemporaryDirectory() as root:
            config = {'schema_version': 4, 'transport': 'https', 'storage': 'postgresql', 'service_uid': os.geteuid(),
                'node_id': 'synthetic-cli-bridge', 'allowed_hosts': ['127.0.0.1'], 'temp_root': root,
                'submission_auth': 'internal_network', 'allowed_client_networks': ['127.0.0.1/32']}
            authority = Mock()
            app = create_app(config, authority)
            captured = []

            @app.middleware('http')
            async def observe(request, call_next):
                captured.append(dict(request.headers))
                return await call_next(request)

            fixture = self

            class PumpedTransport(ReviewTransport):
                def request(self, value, *, poll=False):
                    response = super().request(value, poll=poll)
                    if response['state'] == 'PENDING':
                        fixture.work()
                        return super().request(value, poll=True)
                    return response

            with tls_server(app, Path(root)) as (endpoint, ca):
                config = {**CONFIG, 'endpoint': endpoint, 'ca_file': ca}
                with LocalSession(Path(root)/'state') as local, patch.dict(os.environ, {'SANITY_VWP_SUBMIT_TOKEN': 'must-not-be-used'}):
                    client = ReviewClient(local, config, PumpedTransport(config))
                    self.assertEqual(1, client.connect(self.session['id'], self.session['expires_at'], self.session['secret'])['revision'])
                    self.assertEqual(2, command(client, parser().parse_args(['groups']))['total'])
                    command(client, parser().parse_args(['confirm', '--reason', 'CLI 전체 확인']))
                    command(client, parser().parse_args(['group', 'g1']))
                    result = command(client, parser().parse_args(['judge', 'g1', 'DEFER', '--reason', '추가 근거 요청']))
                    self.assertEqual('DEFER', result['event']['conclusion'])
                    command(client, parser().parse_args(['close']))
                    self.assertFalse(local.path.exists())
            self.assertGreater(len(captured), 5)
            self.assertTrue(all('authorization' not in headers for headers in captured))
            self.assertNotIn(self.session['secret'], str(captured))
            self.assertEqual([], authority.mock_calls)
