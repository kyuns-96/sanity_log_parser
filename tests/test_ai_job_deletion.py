"""Real, isolated PostgreSQL deletion with synthetic reports and model replies."""
import os
import secrets
import unittest
from unittest.mock import patch
from uuid import uuid4

from common.sanity_db import Database, json_value
from common.sanity_common import ReviewError
from oa.sanity_ai_deletion import PREFIX, NAMESPACE, marker
from oa.sanity_ai_monitor import Monitor
from tests.support import PgTestCase, psycopg2


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class AIJobDeletionDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w = VwpWorkerTests()
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.c = self.w.c
        from psycopg2 import sql
        from psycopg2.extensions import make_dsn
        self.restricted_oa_dsn = os.environ['SANITY_OA_DSN']
        self.login = 'sanity_delete_' + uuid4().hex
        password = secrets.token_urlsafe(32)
        self.c.admin.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB '
            'NOCREATEROLE NOBYPASSRLS PASSWORD %s').format(sql.Identifier(self.login)), (password,))
        self.addCleanup(self._drop_login)
        self.c.admin.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(
            sql.Identifier(self.c.schema), sql.Identifier(self.login)))
        self.c.admin.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {} TO {}').format(
            sql.Identifier(self.c.schema), sql.Identifier(self.login)))
        self.table_delete_right('GRANT')
        dsn = make_dsn(os.environ['SANITY_TEST_ADMIN_DSN'], user=self.login, password=password)
        environment = patch.dict(os.environ, {'SANITY_VWP_DSN': dsn, 'SANITY_OA_DSN': dsn,
                                              'SANITY_DB_ALLOW_SINGLE_ACCOUNT': 'true'})
        environment.start()
        self.addCleanup(environment.stop)

    def _drop_login(self):
        from psycopg2 import sql
        self.c.admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(self.login)))
        self.c.admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(self.login)))

    def table_delete_right(self, action):
        from psycopg2 import sql
        self.c.admin.execute(sql.SQL(action + ' DELETE ON {} ' + ('TO' if action == 'GRANT' else 'FROM')
            + ' {}').format(sql.Identifier(self.c.schema, 'sanity_oa_data'), sql.Identifier(self.login)))

    def delete(self, identity, actor='owner'):
        with self.c.service() as service:
            return service.delete(actor, identity)

    def count(self, pattern):
        return self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                                  (pattern,)).fetchone()[0]

    def assert_removed(self, identity):
        for prefix in ('ai1.job.', 'ai2.', 'ai.intent.', 'ai.live.'):
            self.assertEqual(0, self.count(prefix + identity + '.%'))
        for prefix in ('ai.resume.%', 'ai.unknown.%'):
            self.assertEqual(0, self.c.admin_query("SELECT count(*) FROM {oa} WHERE workspace_key LIKE %s "
                "AND payload->>'job_id'=%s", (prefix, identity)).fetchone()[0])
        with self.c.service() as service:
            value = marker(service, identity)
            self.assertEqual({'namespace', 'id', 'actor_id', 'sha256'}, set(value))
            self.assertEqual((NAMESPACE, identity, 'owner'), (value['namespace'], value['id'], value['actor_id']))

    def test_completed_job_removes_execution_rows_and_preserves_report_and_proposal(self):
        job = self.w.request()
        self.w.prepare()
        self.assertEqual('COMPLETED', self.w.v.finish()['status'])
        sibling = self.w.request(expected_revision=1)
        before = self.c.admin_query("SELECT record_id,payload,state FROM {oa} WHERE record_type<>'WS_CONFIG' "
                                    "ORDER BY record_id").fetchall()
        self.assertGreater(self.count('ai1.job.' + job['id'] + '.%'), 0)
        with self.c.service() as service:
            cards = {card['id']: card for card in Monitor(service).items('owner')['items']}
            self.assertTrue(cards[job['id']]['can_delete'])
            self.assertFalse(cards[sibling['id']]['can_delete'])
        self.assertEqual({'id': job['id'], 'deleted': True}, self.delete(job['id']))
        self.assert_removed(job['id'])
        self.assertEqual(before, self.c.admin_query("SELECT record_id,payload,state FROM {oa} "
            "WHERE record_type<>'WS_CONFIG' ORDER BY record_id").fetchall())
        with self.c.service() as service:
            self.assertEqual([sibling['id']], [card['id'] for card in Monitor(service).items('owner')['items']])
        self.assertEqual({'id': job['id'], 'deleted': True}, self.delete(job['id']))
        self.c.assert_code('AI_JOB_DELETED', lambda: self.w.request(job['id']))
        self.c.assert_code('AI_JOB_DELETED', lambda: self.c.enqueue(job['id']))
        self.assertEqual(1, len(self.w.v.wire))

    def test_cancelled_preparation_and_resume_markers_are_actually_removed(self):
        job = self.w.request()
        self.w.prepare()
        with self.c.service() as service:
            service.cancel('owner', job['id'])
            service.resume('owner', job['id'], uuid4())
            service.cancel('owner', job['id'])
        self.assertGreater(self.count('ai.resume.%'), 0)
        self.delete(job['id'])
        self.assert_removed(job['id'])
        self.assertIsNone(self.w.prepare())
        self.assertEqual([], self.w.v.wire)

    def test_only_original_requester_with_current_write_access_can_delete(self):
        job = self.w.request()
        with self.c.service() as service:
            service.cancel('owner', job['id'])
            service.ws.put('WS_USER', 'other-owner', {'id': 'other-owner', 'name': '합성 담당자 2',
                'username': 'other-owner', 'role': 'OWNER'}, state={'active': True})
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{owners}}',%s) WHERE record_type='WS_BLOCK'",
                           (json_value(['owner', 'other-owner']),))
        self.c.assert_code('FORBIDDEN', lambda: self.delete(job['id'], 'other-owner'))
        self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{owners}}','[]'::jsonb) WHERE record_type='WS_BLOCK'")
        self.c.assert_code('FORBIDDEN', lambda: self.delete(job['id']))
        self.assertEqual(0, self.count(PREFIX + '%'))
        self.assertGreater(self.count('ai.intent.' + job['id'] + '.%'), 0)

    def test_active_jobs_and_missing_delete_permission_never_hide_or_remove_a_job(self):
        job = self.w.request()
        self.c.assert_code('AI_JOB_ACTIVE', lambda: self.delete(job['id']))
        with self.c.service() as service:
            service.cancel('owner', job['id'])
        self.table_delete_right('REVOKE')
        self.c.assert_code('AI_JOB_DELETE_NOT_CONFIGURED', lambda: self.delete(job['id']))
        with self.c.service() as service:
            self.assertFalse(Monitor(service).items('owner')['items'][0]['can_delete'])
            self.assertIsNone(marker(service, job['id']))
        self.assertGreater(self.count('ai.intent.' + job['id'] + '.%'), 0)

    def test_restricted_mode_still_forbids_direct_delete_and_disables_cleanup(self):
        job = self.w.request()
        with self.c.service() as service:
            service.cancel('owner', job['id'])
        with patch.dict(os.environ, {'SANITY_OA_DSN': self.restricted_oa_dsn,
                                     'SANITY_DB_ALLOW_SINGLE_ACCOUNT': 'false'}):
            with self.c.service() as service:
                self.assertFalse(Monitor(service).items('owner')['items'][0]['can_delete'])
            self.c.assert_code('AI_JOB_DELETE_NOT_CONFIGURED', lambda: self.delete(job['id']))
            with self.assertRaises(ReviewError) as caught:
                with Database('oa') as db, db.transaction():
                    db.execute("DELETE FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s",
                               ('ai.intent.' + job['id'] + '.%',))
            self.assertEqual('DATABASE_ERROR', caught.exception.code)
            self.assertIsInstance(caught.exception.__cause__, psycopg2.errors.InsufficientPrivilege)
        self.assertGreater(self.count('ai.intent.' + job['id'] + '.%'), 0)

    def test_delete_works_with_existing_table_permission_and_no_installed_function(self):
        with Database('oa') as db, db.transaction():
            self.assertEqual('single_account', db.privilege_mode)
            self.assertTrue(db.execute("SELECT has_table_privilege(current_user, "
                "quote_ident(%s) || '.sanity_oa_data', 'DELETE') AS allowed", (db.schema,)).fetchone()['allowed'])
            self.assertIsNone(db.execute("SELECT to_regprocedure(quote_ident(%s) "
                "|| '.sanity_delete_ai_job(uuid,text)') AS oid", (db.schema,)).fetchone()['oid'])
        job = self.w.request()
        with self.c.service() as service:
            service.cancel('owner', job['id'])
        self.assertEqual({'id': job['id'], 'deleted': True}, self.delete(job['id']))
        self.assert_removed(job['id'])

    def test_permission_loss_after_preflight_rolls_back_the_deletion_marker(self):
        job = self.w.request()
        with self.c.service() as service:
            service.cancel('owner', job['id'])
        self.table_delete_right('REVOKE')
        with self.assertRaises(ReviewError) as caught:
            with patch('oa.sanity_ai_deletion.available', return_value=True):
                self.delete(job['id'])
        self.assertEqual('DATABASE_ERROR', caught.exception.code)
        self.assertIsInstance(caught.exception.__cause__, psycopg2.errors.InsufficientPrivilege)
        with self.c.service() as service:
            self.assertIsNone(marker(service, job['id']))
        self.assertGreater(self.count('ai.intent.' + job['id'] + '.%'), 0)

    def test_transaction_failure_rolls_back_both_deletion_and_marker(self):
        job = self.w.request()
        with self.c.service() as service:
            service.cancel('owner', job['id'])
            before = service.db.execute("SELECT workspace_key FROM {oa} WHERE record_type='WS_CONFIG' "
                "AND workspace_key LIKE %s ORDER BY workspace_key", ('ai.intent.' + job['id'] + '.%',)).fetchall()
            with self.assertRaises(ReviewError):
                with service.db.transaction():
                    service.delete('owner', job['id'])
                    self.assertIsNotNone(marker(service, job['id']))
                    raise ReviewError('SYNTHETIC_ROLLBACK', 'Synthetic transaction failure')
            self.assertIsNone(marker(service, job['id']))
            self.assertEqual(before, service.db.execute("SELECT workspace_key FROM {oa} WHERE record_type='WS_CONFIG' "
                "AND workspace_key LIKE %s ORDER BY workspace_key", ('ai.intent.' + job['id'] + '.%',)).fetchall())

    def test_cancel_and_delete_during_single_or_batch_call_discards_late_reply(self):
        from tests.test_ai_batches import batch_settings
        from tests.test_vwp_inference import configuration
        for batch in (False, True):
            with self.subTest(batch=batch):
                if batch:
                    self.w.connection = self.w.v.create(configuration(batch=batch_settings(max_records=1).public()))
                job = self.w.request()
                self.w.prepare()

                def cancel_and_delete(request):
                    self.c.assert_code('AI_JOB_ACTIVE', lambda: self.delete(job['id']))
                    with self.c.service() as service:
                        service.cancel('owner', job['id'])
                    self.delete(job['id'])

                self.c.callback = cancel_and_delete
                self.assertIsNone(self.w.prepare())
                self.assert_removed(job['id'])
                self.assertIsNone(self.w.prepare())
        self.assertEqual(2, len(self.w.v.wire))
        self.assertEqual(0, self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_completed_batch_removes_child_plans_results_and_keeps_confirmed_history(self):
        from tests.test_ai_batches import batch_settings
        from tests.test_vwp_inference import configuration
        from oa.sanity_workspace_clustering import WorkspaceClusteringStore
        self.w.connection = self.w.v.create(configuration(batch=batch_settings(max_records=1).public()))
        job = self.w.request()
        self.w.prepare()
        self.assertEqual('COMPLETED', self.w.v.finish()['status'])
        self.assertGreater(self.count('ai2.' + job['id'] + '.%'), 0)
        with self.c.service() as service:
            store = WorkspaceClusteringStore(service.db, ws=service.ws)
            store.change(*self.c.path, action='CONFIRM', expected_revision=1, request_id=uuid4(),
                         reason='합성 분류 확인', reusable=False, compatible_versions=False, criteria='')
            before = store.current('report')
        self.delete(job['id'])
        self.assert_removed(job['id'])
        with self.c.service() as service:
            self.assertEqual(before, WorkspaceClusteringStore(service.db, ws=service.ws).current('report'))

    def test_serial_and_parallel_unknown_recovery_markers_are_removed(self):
        from dataclasses import replace
        import httpx
        from tests.test_ai_batches import batch_settings
        from tests.test_vwp_inference import configuration
        for width in (1, 2):
            with self.subTest(concurrency=width):
                settings = replace(batch_settings(max_records=1), max_concurrency=width)
                self.w.connection = self.w.v.create(configuration(batch=settings.public()))
                job = self.w.request()
                self.w.prepare()
                def timeout(request):
                    raise httpx.ReadTimeout('Synthetic unknown outcome', request=request)
                self.c.callback = timeout
                unknown = self.w.prepare()
                self.assertEqual('UNKNOWN', unknown['status'])
                info = unknown['unknown_recovery']
                with self.c.service() as service:
                    service.retry_unknown('owner', job['id'], uuid4(),
                        info['event_sha256'], info['step_sha256'], True)
                    self.assertEqual('CANCELLED', service.cancel('owner', job['id'])['status'])
                self.assertGreater(self.count('ai.unknown.%'), 0)
                self.delete(job['id'])
                self.assert_removed(job['id'])
                self.assertIsNone(self.w.prepare())

    def test_expired_parallel_wave_can_be_deleted_before_late_responses_arrive(self):
        from concurrent.futures import ThreadPoolExecutor
        from dataclasses import replace
        from datetime import timedelta
        import json
        from threading import Event
        from common.sanity_ai_execution import executing_on
        from oa.sanity_ai_jobs import _now
        from tests.test_ai_batches import batch_settings
        from tests.test_vwp_inference import configuration
        settings = replace(batch_settings(max_records=1), max_concurrency=2)
        self.w.connection = self.w.v.create(configuration(batch=settings.public()))
        job = self.w.request()
        self.w.prepare()
        entered, release = [Event(), Event()], Event()
        def blocked(request):
            packet = json.loads(json.loads(request.content)['messages'][1]['content'])
            entered[packet['batch']['start']].set()
            if not release.wait(20):
                raise AssertionError('Synthetic parallel call was not released')
        self.c.callback = blocked
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.w.prepare)
            try:
                self.assertTrue(entered[0].wait(10))
                self.assertTrue(entered[1].wait(10))
                with self.c.service() as service:
                    self.assertEqual('RUNNING', service.cancel('owner', job['id'])['status'])
                self.c.assert_code('AI_JOB_ACTIVE', lambda: self.delete(job['id']))
                at = _now() + timedelta(seconds=400)
                with executing_on('vwp'), patch('oa.sanity_ai_jobs._db_now', return_value=at):
                    self.assertEqual('UNKNOWN', self.c.run_job(now=at)['status'])
                self.delete(job['id'])
            finally:
                release.set()
            self.assertIsNone(pending.result(timeout=10))
        self.assert_removed(job['id'])
        self.assertIsNone(self.w.prepare())
        self.assertEqual(0, self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])
