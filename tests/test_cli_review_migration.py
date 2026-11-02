"""014 queue contracts and every portal DDL step on an independent test DB."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from common.sanity_cli_review import PROJECT
from common.sanity_common import ReviewError
from common.sanity_db import Database, Queue
from db.portal.build_reference import capture, create_forms, snapshot
from db.portal.runbook import checkpoints, fingerprint, identifier, load_plan, prepare, verify
from tests.support import PgTestCase, ROOT, psycopg2


class CliReviewPortalTests(unittest.TestCase):
    def test_extension_keeps_each_optional_installation_prefix_and_adds_only_two_alters(self):
        for options in ({}, {'raw_sources': True}, {'payload_storage': True},
                        {'evidence_pages': True}, {'shared_packets': True}):
            prior, extended = load_plan(**options), load_plan(cli_reviews=True, **options)
            with self.subTest(options=options):
                self.assertEqual(prior['steps'], extended['steps'][:-2])
                self.assertEqual(['014', '014'], [s['phase'] for s in extended['steps'][-2:]])
                states = list(checkpoints(extended))
                with tempfile.TemporaryDirectory() as root:
                    bundle = Path(root)/'014'
                    manifest = prepare(states[-3], bundle, extended)
                    self.assertEqual(2, len(manifest['steps']))
                    self.assertFalse(manifest['operational_ready'])
                    verify(states[-3], bundle, len(extended['steps'])-1, 'before')
                    verify(states[-1], bundle)
                    for step in manifest['steps']:
                        sql = (bundle/step['file']).read_text()
                        self.assertEqual(1, sql.count(';'))
                        self.assertTrue(sql.startswith('ALTER TABLE '))
                        self.assertNotIn('GRANT', sql)


class CliReviewMigrationTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql', '009_sso_identity.sql'):
            self.admin.execute((ROOT/'db'/name).read_text().replace('public.', self.schema+'.'))

    def test_raw_migration_preserves_legacy_queue_validation_and_role_permissions(self):
        script = (ROOT/'db/014_cli_reviews.sql').read_text().replace('public.', self.schema+'.')
        self.admin.execute(script)
        for side, direction in (('vwp', 'vwp_to_oa'), ('oa', 'oa_to_vwp')):
            with Database(side) as db, db.transaction():
                queue = Queue(db, direction)
                message = {'message_id': 'legacy-'+side, 'message_type': 'PING', 'project': 'legacy', 'run_id': 'legacy'}
                self.assertEqual('PING', queue.enqueue(message)['message_type'])
            with self.assertRaises(ReviewError) as caught:
                with Database(side) as db, db.transaction():
                    Queue(db, direction).enqueue({**message, 'message_id': 'invalid-'+side, 'run_id': None})
            self.assertEqual('DATABASE_ERROR', caught.exception.code)
        with Database('vwp') as db:
            from common.sanity_cli_review_db import require_schema
            require_schema(db)
            self.assertFalse(db.execute("SELECT has_table_privilege(current_user,%s,'SELECT') AS allowed",
                (self.schema+'.sanity_oa_data',)).fetchone()['allowed'])

    def test_migration_failure_rolls_back_all_queue_changes(self):
        self.admin.execute('ALTER TABLE '+identifier(self.schema)+'.sanity_oa_to_vwp DROP CONSTRAINT sanity_oa_message_kind_v3')
        before = snapshot(self.admin, self.schema)
        with self.assertRaises(psycopg2.Error):
            self.admin.execute((ROOT/'db/014_cli_reviews.sql').read_text().replace('public.', self.schema+'.'))
        self.admin.execute('ROLLBACK')
        self.assertEqual(fingerprint(before), fingerprint(snapshot(self.admin, self.schema)))

    def _replay(self, shared):
        reference, portal = 'cli_ref_'+uuid4().hex, 'cli_portal_'+uuid4().hex
        for schema in (reference, portal):
            self.addCleanup(self.admin.execute, 'DROP SCHEMA IF EXISTS '+identifier(schema)+' CASCADE')
        # Independently execute original SQL on this server. Compare the entire
        # catalog after every phase with a replay of all frozen portal SQL.
        # This does not approve bypassing deployment snapshot verification:
        # historical PG18 fingerprint failures remain in the existing tests.
        original = capture(self.admin, reference, shared_packets=shared, cli_reviews=True)
        original_states = list(checkpoints(original))
        phase_states = {step['phase']: original_states[index+1] for index, step in enumerate(original['steps'])}
        plan = load_plan(shared_packets=shared, cli_reviews=True)
        create_forms(self.admin, portal)
        query = "SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid"
        before = self.admin.execute(query, (portal,)).fetchall()
        with tempfile.TemporaryDirectory() as root:
            bundle = Path(root)/'all'
            manifest = prepare(plan['initial'] | {'schema': portal}, bundle, plan)
            self.assertEqual(68 if shared else 57, len(manifest['steps']))
            for index, step in enumerate(manifest['steps']):
                self.admin.execute((bundle/step['file']).read_text())
                if index+1 == len(manifest['steps']) or manifest['steps'][index+1]['phase'] != step['phase']:
                    self.assertEqual(fingerprint(phase_states[step['phase']]), fingerprint(snapshot(self.admin, portal)))
            self.assertEqual(fingerprint(snapshot(self.admin, reference)), fingerprint(snapshot(self.admin, portal)))
        self.assertEqual(before, self.admin.execute(query, (portal,)).fetchall())

    def test_all_57_portal_statements_match_original_sql_on_the_running_server(self):
        self._replay(False)

    def test_all_68_portal_statements_preserve_optional_storage_and_match_original_sql(self):
        self._replay(True)
