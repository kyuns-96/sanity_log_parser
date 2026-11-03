"""Real, isolated PostgreSQL deletion with synthetic reports and model replies."""
import os
import secrets
import unittest
from unittest.mock import patch
from uuid import uuid4

from oa.sanity_ai_deletion import NAMESPACE, marker
from tests.support import PgTestCase


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class AIJobDeletionDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w = VwpWorkerTests()
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.c = self.w.c
        self.v = self.w.v
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
            self.assertEqual({'namespace', 'id', 'actor_id'}, set(value))
            self.assertEqual((NAMESPACE, identity, 'owner'), (value['namespace'], value['id'], value['actor_id']))


    def test_rolling_delete_after_refill_stops_more_work_and_late_observations(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from tests.test_ai_parallel_batches import ParallelVwpJobsTests
        from oa.sanity_ai_monitor import record
        job=ParallelVwpJobsTests.create_job(self,records=5,execution='parallel-v2')
        entered=[Event() for _ in range(5)];release=Event()
        def block(request):
            packet=ParallelVwpJobsTests.packet(request)
            index=packet['batch']['start'];entered[index].set()
            if index!=1 and not release.wait(20):raise AssertionError('Synthetic response was not released')
        self.c.callback=block
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.w.prepare)
            try:
                self.assertTrue(entered[0].wait(10));self.assertTrue(entered[2].wait(10))
                with self.c.service() as service:
                    saved=service._load(job['id']);status=service.public(saved)
                    self.assertEqual(1,status['current_group_count'])
                    self.assertEqual(0,status['merge_iteration'])
                self.delete(job['id']);self.assert_removed(job['id'])
                with self.c.service() as service:
                    record(service,job['id'],saved['data']['command'],saved['data']['target'],status,phase='SENDING')
                self.assert_removed(job['id'])
            finally:release.set()
            self.assertIsNone(future.result(timeout=10))
        self.assertFalse(entered[3].is_set());self.assertFalse(entered[4].is_set())
        self.assertEqual(3,len(self.w.v.wire));self.assert_removed(job['id'])
