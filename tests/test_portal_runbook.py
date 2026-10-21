"""Portal DDL is equivalent to the canonical migrations, including interrupted runs."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from db.portal.runbook import (PortalError, checkpoints, constraint_sql, fingerprint,
                               identifier, load_plan, prepare, statement, verify)
from db.portal.build_reference import capture, create_forms, snapshot
from tests.support import PgTestCase, ROOT, psycopg2


class PortalRunbookTests(unittest.TestCase):
    def setUp(self):
        self.plan = load_plan()
        self.temp = tempfile.TemporaryDirectory(prefix='sanity-portal-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_offline_cli_needs_only_standard_library_and_never_loads_a_db_driver(self):
        script = ROOT / 'db/portal/runbook.py'
        result = subprocess.run([sys.executable, '-S', str(script), 'inspect', '--schema', 'synthetic_private',
                                 '--out', str(self.root / 'inspect.sql')], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        sql = (self.root / 'inspect.sql').read_text()
        self.assertTrue(sql.startswith('SELECT '))
        self.assertNotIn('CREATE ', sql)
        self.assertNotIn('synthetic_private', result.stdout)

    def test_all_six_ddl_phases_include_workspace_and_exclude_grants_and_sso_jit(self):
        self.assertEqual({'002', '004', '005', '006', '007', '008'}, {s['phase'] for s in self.plan['steps']})
        manifest = prepare(self.plan['initial'], self.root / 'bundle')
        self.assertFalse(manifest['operational_ready'])
        for step in manifest['steps']:
            sql = (self.root / 'bundle' / step['file']).read_text()
            self.assertRegex(sql, r'^(ALTER TABLE|CREATE (UNIQUE )?INDEX) ')
            self.assertEqual(1, sql.count(';'))
            for forbidden in ('DO $', 'BEGIN;', 'COMMIT;', 'LOCK TABLE', 'GRANT ', 'CREATE ROLE', 'SSO_IDENTITY'):
                self.assertNotIn(forbidden, sql)

    def test_prepare_resumes_at_verified_checkpoints_and_complete_state_is_not_operational_ready(self):
        states = list(checkpoints(self.plan))
        for i in (0, 3, len(states) // 2, len(states) - 1):
            manifest = prepare(states[i], self.root / str(i))
            self.assertEqual(i, manifest['start_checkpoint'])
            self.assertEqual(len(self.plan['steps']) - i, len(manifest['steps']))
            verify(states[-1], self.root / str(i))
        self.assertFalse(manifest['operational_ready'])

    def test_drift_nonempty_invalid_or_unvalidated_schema_is_rejected(self):
        for mutation in ('rows', 'missing_unique', 'unvalidated', 'extra_column', 'duplicate_check'):
            state = deepcopy(self.plan['initial'])
            table = state['tables'][0]
            if mutation == 'rows': table['rows'] = 1
            if mutation == 'missing_unique': table['constraints'].pop()
            if mutation == 'unvalidated': table['constraints'][0]['validated'] = False
            if mutation == 'extra_column': table['columns'].append(deepcopy(table['columns'][0]))
            if mutation == 'duplicate_check': table['constraints'].append(deepcopy(table['constraints'][0]))
            with self.subTest(mutation=mutation), self.assertRaises(PortalError):
                prepare(state, self.root / mutation)

    def test_bundle_sql_tampering_wrong_schema_and_wrong_checkpoint_fail_verification(self):
        bundle = self.root / 'bundle'
        manifest = prepare(self.plan['initial'], bundle)
        first = manifest['steps'][0]
        verify(self.plan['initial'], bundle, first['number'], 'before')
        with self.assertRaises(PortalError): verify(self.plan['initial'], bundle, first['number'], 'after')
        wrong_schema = deepcopy(self.plan['initial'])
        wrong_schema['schema'] = 'other_schema'
        with self.assertRaises(PortalError): verify(wrong_schema, bundle, first['number'], 'before')
        (bundle / first['file']).write_text('ALTER TABLE unexpected;\n')
        with self.assertRaises(PortalError): verify(self.plan['initial'], bundle, first['number'], 'before')

    def test_output_is_not_overwritten_and_source_drift_invalidates_reference(self):
        bundle = self.root / 'bundle'
        prepare(self.plan['initial'], bundle)
        with self.assertRaises(PortalError): prepare(self.plan['initial'], bundle)
        altered = deepcopy(self.plan)
        altered['sources']['db/005_workspace.sql'] = '0' * 64
        with patch('db.portal.runbook.read_json', return_value=altered), self.assertRaises(PortalError):
            load_plan()

    def test_editing_manifest_hashes_cannot_authorize_modified_sql(self):
        bundle = self.root / 'bundle'
        manifest = prepare(self.plan['initial'], bundle)
        manifest['steps'][0]['sha256'] = '0' * 64
        (bundle / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(PortalError): verify(self.plan['initial'], bundle, 1, 'before')

    def test_index_order_is_part_of_the_expected_catalog(self):
        final = list(checkpoints(self.plan))[-1]
        changed = deepcopy(final)
        table = next(t for t in changed['tables'] if t['name'] == 'sanity_oa_data')
        index = next(i for i in table['indexes'] if i['name'] == 'sanity_workspace_audit_idx')
        self.assertEqual(3, index['options'][1])  # DESC / NULLS FIRST, as in 005.
        index['options'][1] = 0
        with self.assertRaises(PortalError): prepare(changed, self.root / 'bad-order')


class PortalDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.portal_schema = 'sanity_portal_' + uuid.uuid4().hex
        create_forms(self.admin, self.portal_schema)
        self.addCleanup(self.admin.execute, 'DROP SCHEMA ' + identifier(self.portal_schema) + ' CASCADE')
        self.plan = load_plan()
        self.temp = tempfile.TemporaryDirectory(prefix='sanity-portal-db-')
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name) / 'bundle'

    def inspect(self):
        return snapshot(self.admin, self.portal_schema)

    def test_portal_autocommit_steps_match_original_sql_preserve_gui_names_fk_options_and_acls(self):
        reference_schema = 'sanity_reference_' + uuid.uuid4().hex
        reference = capture(self.admin, reference_schema)
        self.addCleanup(self.admin.execute, 'DROP SCHEMA ' + identifier(reference_schema) + ' CASCADE')
        self.assertEqual([fingerprint(s) for s in checkpoints(reference)],
                         [fingerprint(s) for s in checkpoints(self.plan)])
        self.assertEqual(reference['steps'], self.plan['steps'])
        before_oids = self.admin.execute('SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind=\'r\' ORDER BY oid',
                                        (self.portal_schema,)).fetchall()
        for table in self.inspect()['tables']:
            for i, c in enumerate(table['constraints']):
                if c['type'] == 'c':
                    self.admin.execute('ALTER TABLE ' + identifier(self.portal_schema) + '.' + identifier(table['name'])
                                       + ' RENAME CONSTRAINT ' + identifier(c['name']) + ' TO ' + identifier('gui_check_' + str(i)))
        manifest = prepare(self.inspect(), self.bundle)
        for step in manifest['steps']:
            verify(self.inspect(), self.bundle, step['number'], 'before')
            self.admin.execute((self.bundle / step['file']).read_text())
            verify(self.inspect(), self.bundle, step['number'], 'after')
            if step['number'] == len(manifest['steps']) // 2:
                resumed = prepare(self.inspect(), Path(self.temp.name) / 'resumed')
                self.assertEqual(step['number'], resumed['start_checkpoint'])
        verify(self.inspect(), self.bundle)
        after_oids = self.admin.execute('SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind=\'r\' ORDER BY oid',
                                       (self.portal_schema,)).fetchall()
        self.assertEqual(before_oids, after_oids)
        parent = next(c for t in self.inspect()['tables'] for c in t['constraints'] if c['name'] == 'sanity_vwp_parent_fk')
        self.assertTrue(parent['deferrable'])
        self.assertTrue(parent['deferred'])
        # DBA applies the unchanged grant contract separately, after portal DDL.
        grants = (ROOT / 'db/003_grants.sql').read_text().replace('public.', identifier(self.portal_schema) + '.')
        self.admin.execute(grants.replace('SCHEMA public', 'SCHEMA ' + identifier(self.portal_schema)))
        from common.sanity_db import Database
        with patch.dict('os.environ', {'SANITY_DB_SCHEMA': self.portal_schema}):
            for side in ('vwp', 'oa'):
                with Database(side): pass
        self.admin.execute((ROOT / 'db/tests/smoke.sql').read_text().replace('public.', identifier(self.portal_schema) + '.'))

    def test_missing_form_unique_cannot_be_silently_repaired(self):
        state = self.inspect()
        table = next(t for t in state['tables'] if t['name'] == 'sanity_vwp_data')
        unique = next(c for c in table['constraints'] if c['type'] == 'u' and c['columns'] == ['analysis_id', 'record_id'])
        self.admin.execute('ALTER TABLE ' + identifier(self.portal_schema) + '.sanity_vwp_data DROP CONSTRAINT ' + identifier(unique['name']))
        with self.assertRaises(PortalError): prepare(self.inspect(), self.bundle)

    def test_fk_to_a_same_named_table_in_another_schema_is_rejected(self):
        other = 'sanity_other_' + uuid.uuid4().hex
        create_forms(self.admin, other)
        self.addCleanup(self.admin.execute, 'DROP SCHEMA ' + identifier(other) + ' CASCADE')
        step = self.plan['steps'][0]
        self.admin.execute(statement(step, self.portal_schema, self.inspect()))
        fk = next(c for t in self.inspect()['tables'] for c in t['constraints'] if c['name'] == 'sanity_vwp_parent_fk')
        self.admin.execute('ALTER TABLE ' + identifier(self.portal_schema) + '.sanity_vwp_data DROP CONSTRAINT '
                           + identifier(fk['name']) + ', ' + constraint_sql(fk, other))
        wrong = next(c for t in self.inspect()['tables'] for c in t['constraints'] if c['name'] == fk['name'])
        self.assertEqual('OTHER', wrong['ref_schema'])
        with self.assertRaises(PortalError): prepare(self.inspect(), self.bundle)

    def test_failed_single_alter_rolls_back_check_replacement_and_added_columns(self):
        for step in self.plan['steps']:
            if step['phase'] == '004' and step['table'] == 'sanity_oa_data': break
            self.admin.execute(statement(step, self.portal_schema, self.inspect()))
        self.admin.execute('INSERT INTO ' + identifier(self.portal_schema) + ".sanity_oa_data(record_id,record_type,project,run_id,bundle_id,payload) "
                           "VALUES (%s,'RECEIVED_BUNDLE','synthetic','run-1',%s,'{}')", (uuid.uuid4(), 'b-' + 'a' * 64))
        before = self.inspect()
        with self.assertRaises(PortalError): prepare(before, self.bundle)
        sql = statement(step, self.portal_schema, before).rstrip().removesuffix(';')
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.admin.execute(sql + ', ADD CONSTRAINT portal_deliberate_failure CHECK (false)')
        self.assertEqual(before, self.inspect())
