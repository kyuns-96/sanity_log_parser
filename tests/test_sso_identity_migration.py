"""009 preserves legacy data and isolates durable, strictly typed SSO identities."""
from copy import deepcopy
from pathlib import Path
import tempfile
import uuid

from common.sanity_db import Database, json_value, stable_uuid
from db.portal.build_reference import snapshot
from db.portal.runbook import checkpoints, fingerprint, identifier, load_plan, prepare, verify
from tests.support import PgTestCase, ROOT, psycopg2


class SSOIdentityMigrationTests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.admin.execute(self.source('008_submissions.sql'))
        self.table = identifier(self.schema) + '.sanity_oa_data'

    def source(self, filename):
        return (ROOT / 'db' / filename).read_text().replace('public.', identifier(self.schema) + '.')

    def apply_009(self):
        self.admin.execute(self.source('009_sso_identity.sql'))

    def identity(self, subject='synthetic.user01', enabled=True):
        return {'issuer': 'https://synthetic-idp.example.test/entity', 'subject': subject,
                'display_name': '합성 사용자', 'first_seen_at': '2026-10-22T00:00:00.000000Z'}, {
                    'enabled': enabled, 'last_seen_at': '2026-10-22T00:00:00.000000Z'}

    def insert(self, payload, state, key=None, connection=None, conflict=''):
        subject = payload.get('subject') if key is None else key
        record_id = stable_uuid('sso-identity-v1', 'SSO_IDENTITY', subject)
        return (connection or self.admin).execute(
            'INSERT INTO ' + self.table + '(record_id,record_type,workspace_key,payload,state) '
            "VALUES (%s,'SSO_IDENTITY',%s,%s,%s) " + conflict,
            (record_id, subject, json_value(payload), json_value(state)))

    def test_008_checkpoint_runs_one_portal_statement_with_the_exact_original_009_catalog(self):
        plan = load_plan()
        before = snapshot(self.admin, self.schema)
        self.assertEqual(fingerprint(list(checkpoints(plan))[54]), fingerprint(before))
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / '009'
            manifest = prepare(before, bundle)
            self.assertEqual(54, manifest['start_checkpoint'])
            self.assertEqual([55], [s['number'] for s in manifest['steps']])
            verify(before, bundle, 55, 'before')
            self.admin.execute((bundle / manifest['steps'][0]['file']).read_text())
            verify(snapshot(self.admin, self.schema), bundle, 55, 'after')
            verify(snapshot(self.admin, self.schema), bundle)

    def test_original_009_preserves_legacy_payloads_and_keeps_existing_scope_guards(self):
        record_id = uuid.uuid4()
        # Identity-looking keys in an old payload must not activate SSO casts.
        legacy = {'first_seen_at': 'not-a-date', 'subject': None, 'issuer': 10}
        self.admin.execute('INSERT INTO ' + self.table
                           + "(record_id,record_type,project,run_id,bundle_id,payload) VALUES "
                           "(%s,'RECEIVED_BUNDLE','synthetic','run-1',%s,%s)",
                           (record_id, 'b-' + 'a' * 64, json_value(legacy)))
        self.apply_009()
        self.assertEqual(legacy, self.admin.execute('SELECT payload FROM ' + self.table
                                                   + ' WHERE record_id=%s', (record_id,)).fetchone()[0])
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.admin.execute('INSERT INTO ' + self.table
                               + "(record_id,record_type,payload) VALUES (%s,'RECEIVED_BUNDLE','{}')", (uuid.uuid4(),))
        payload, state = self.identity()
        self.insert(payload, state)
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.admin.execute('UPDATE ' + self.table + " SET project='foreign-scope' WHERE record_type='SSO_IDENTITY'")
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.admin.execute('UPDATE ' + self.table + " SET workspace_project_ref=record_id WHERE record_type='SSO_IDENTITY'")

    def test_json_null_wrong_types_unknown_fields_and_invalid_timestamps_are_rejected(self):
        self.apply_009()
        payload, state = self.identity()
        mutations = [('payload', 'issuer', None), ('payload', 'issuer', 7), ('payload', 'issuer', ''),
                     ('payload', 'issuer', ' issuer'), ('payload', 'issuer', 'bad\nissuer'),
                     ('payload', 'subject', None), ('payload', 'subject', 7),
                     ('payload', 'display_name', None), ('payload', 'display_name', ''),
                     ('payload', 'display_name', 'x' * 101), ('payload', 'roles', ['TOP']),
                     ('payload', 'first_seen_at', None), ('payload', 'first_seen_at', '2026-02-31T00:00:00.000000Z'),
                     ('state', 'enabled', None), ('state', 'enabled', 'true'), ('state', 'enabled', 1),
                     ('state', 'roles', ['TOP']), ('state', 'last_seen_at', None),
                     ('state', 'last_seen_at', '2026-10-21T00:00:00.000000Z'),
                     ('state', 'last_seen_at', '2026-10-22T24:00:00.000000Z'),
                     ('state', 'last_seen_at', '2026-10-22T00:00:60.000000Z'),
                     ('state', 'last_seen_at', '2026-10-22T00:00:00Z')]
        for section, field, value in mutations:
            for remove in ((False, True) if field in ('issuer', 'subject', 'display_name', 'first_seen_at', 'enabled', 'last_seen_at') else (False,)):
                changed = {'payload': deepcopy(payload), 'state': deepcopy(state)}
                if remove:
                    changed[section].pop(field)
                else:
                    changed[section][field] = value
                with self.subTest(section=section, field=field, value=value, remove=remove), self.assertRaises(psycopg2.Error):
                    self.insert(changed['payload'], changed['state'], key='synthetic.user01')
        for key in (None, 'different.user', ' bad', 'x' * 101):
            with self.subTest(key=key), self.assertRaises(psycopg2.Error):
                # An explicit NULL requires a direct insert instead of the helper default.
                self.admin.execute('INSERT INTO ' + self.table
                                   + "(record_id,record_type,workspace_key,payload,state) VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",
                                   (uuid.uuid4(), key, json_value(payload), json_value(state)))
        self.assertEqual(0, self.admin.execute('SELECT count(*) FROM ' + self.table).fetchone()[0])
        self.insert(payload, state)

    def test_conflicting_insert_preserves_original_issuer_disabled_state_and_role_permissions(self):
        self.apply_009()
        payload, state = self.identity(enabled=False)
        with Database('oa') as oa:
            self.insert(payload, state, connection=oa.conn)
            conflicting = {**payload, 'issuer': 'https://other-synthetic.example.test/entity'}
            self.insert(conflicting, {**state, 'enabled': True}, connection=oa.conn,
                        conflict='ON CONFLICT (record_type,workspace_key) WHERE workspace_key IS NOT NULL DO NOTHING')
            row = oa.conn.execute('SELECT payload,state FROM ' + self.table).fetchone()
            self.assertEqual(payload, row['payload'])
            self.assertIs(row['state']['enabled'], False)
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                oa.conn.execute('UPDATE ' + self.table + ' SET payload=%s', (json_value(conflicting),))
            oa.conn.execute('UPDATE ' + self.table + " SET state=jsonb_set(state,'{last_seen_at}',%s), "
                            "version=version+1,updated_at=CURRENT_TIMESTAMP",
                            (json_value('2026-10-22T01:00:00.000000Z'),))
            self.assertIs(oa.conn.execute('SELECT state FROM ' + self.table).fetchone()['state']['enabled'], False)
        with Database('vwp') as vwp:
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                vwp.conn.execute('SELECT payload FROM ' + self.table)

    def test_prior_contract_drift_and_reapplication_fail_without_leaving_partial_constraints(self):
        self.admin.execute('ALTER TABLE ' + self.table + ' RENAME CONSTRAINT sanity_oa_kind_v6 TO changed_kind')
        before = snapshot(self.admin, self.schema)
        with self.assertRaises(psycopg2.Error):
            self.apply_009()
        self.admin.execute('ROLLBACK')
        self.assertEqual(before, snapshot(self.admin, self.schema))
        self.admin.execute('ALTER TABLE ' + self.table + ' RENAME CONSTRAINT changed_kind TO sanity_oa_kind_v6')
        self.apply_009()
        after = snapshot(self.admin, self.schema)
        self.assertEqual(fingerprint(list(checkpoints(load_plan()))[-1]), fingerprint(after))
        with self.assertRaises(psycopg2.Error):
            self.apply_009()
        self.admin.execute('ROLLBACK')
        self.assertEqual(after, snapshot(self.admin, self.schema))
