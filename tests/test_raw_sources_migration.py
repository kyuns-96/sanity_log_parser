"""010 preserves existing contracts and extends the optional portal plan only."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common.sanity_db import Database, json_value
from common.sanity_raw_sources import RawSourceStore
from db.portal.build_reference import capture, create_forms, snapshot
from db.portal.runbook import checkpoints, fingerprint, identifier, load_plan, prepare, verify
from tests.support import PgTestCase, ROOT, psycopg2


class RawSourcesPortalTests(unittest.TestCase):
    def test_base_55_steps_are_unchanged_and_010_is_an_explicit_optional_extension(self):
        base,raw = load_plan(),load_plan(raw_sources=True)
        self.assertEqual(55,len(base['steps']))
        self.assertEqual(base['initial'],raw['initial'])
        self.assertEqual(base['steps'],raw['steps'][:55])
        self.assertEqual(['010']*4,[s['phase'] for s in raw['steps'][55:]])
        self.assertEqual(['alter','index','index','index'],[s['kind'] for s in raw['steps'][55:]])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            states = list(checkpoints(raw))
            manifest = prepare(states[55],directory/'raw',raw)
            self.assertEqual([56,57,58,59],[s['number'] for s in manifest['steps']])
            verify(states[55],directory/'raw',56,'before')
            verify(states[59],directory/'raw')
            legacy = prepare(states[54],directory/'legacy')
            self.assertEqual([55],[s['number'] for s in legacy['steps']])

    def test_all_59_portal_steps_prepare_and_resume_without_erasing_verification(self):
        plan = load_plan(raw_sources=True)
        states = list(checkpoints(plan))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in (0,54,55,56,57,58,59):
                manifest = prepare(states[index],root/str(index),plan)
                self.assertEqual(59-index,len(manifest['steps']))
                self.assertFalse(manifest['operational_ready'])
                verify(states[59],root/str(index))


class RawSourcesMigrationTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql','009_sso_identity.sql'):
            self.admin.execute(self.source(name))

    def source(self, name):
        return (ROOT/'db'/name).read_text().replace('public.',identifier(self.schema)+'.')

    def test_original_migration_preserves_existing_submissions_and_rejects_reapplication(self):
        with Database('vwp') as db:
            key = str(uuid.uuid4())
            db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,status) "
                "VALUES (%s,'SUBMISSION',NULL,%s,%s,'VWP_ACCEPTED')",
                (uuid.uuid4(),key,json_value({'synthetic':'unchanged legacy payload'})))
            before = db.execute("SELECT * FROM {vwp} WHERE entity_key=%s",(key,)).fetchone()
        self.admin.execute(self.source('010_raw_sources.sql'))
        with Database('vwp') as db:
            self.assertEqual(before,db.execute("SELECT * FROM {vwp} WHERE entity_key=%s",(key,)).fetchone())
            RawSourceStore(db,'synthetic-migration')
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):db.execute('SELECT payload FROM {oa}')
        after = snapshot(self.admin,self.schema)
        with self.assertRaises(psycopg2.Error):self.admin.execute(self.source('010_raw_sources.sql'))
        self.admin.execute('ROLLBACK')
        self.assertEqual(after,snapshot(self.admin,self.schema))

    def test_009_checkpoint_executes_four_portal_statements_with_the_original_010_catalog(self):
        plan = load_plan(raw_sources=True)
        before = snapshot(self.admin,self.schema)
        self.assertEqual(fingerprint(list(checkpoints(plan))[55]),fingerprint(before))
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/'010'
            manifest = prepare(before,bundle,plan)
            self.assertEqual([56,57,58,59],[s['number'] for s in manifest['steps']])
            for step in manifest['steps']:
                verify(snapshot(self.admin,self.schema),bundle,step['number'],'before')
                sql = (bundle/step['file']).read_text()
                self.assertEqual(1,sql.count(';'))
                self.admin.execute(sql)
                verify(snapshot(self.admin,self.schema),bundle,step['number'],'after')
            verify(snapshot(self.admin,self.schema),bundle)
        with Database('vwp') as db:RawSourceStore(db,'synthetic-portal')

    def test_prior_constraint_drift_is_rejected_without_partial_replacement(self):
        self.admin_query('ALTER TABLE {vwp} RENAME CONSTRAINT sanity_vwp_status_v3 TO changed_status')
        before = snapshot(self.admin,self.schema)
        with self.assertRaises(psycopg2.Error):self.admin.execute(self.source('010_raw_sources.sql'))
        self.admin.execute('ROLLBACK')
        self.assertEqual(before,snapshot(self.admin,self.schema))

    def test_all_59_autocommit_steps_match_sql_and_keep_gui_names_table_oids_and_acls(self):
        portal = 'raw_portal_'+uuid.uuid4().hex
        reference = 'raw_reference_'+uuid.uuid4().hex
        self.addCleanup(self.admin.execute,'DROP SCHEMA IF EXISTS '+identifier(portal)+' CASCADE')
        self.addCleanup(self.admin.execute,'DROP SCHEMA IF EXISTS '+identifier(reference)+' CASCADE')
        create_forms(self.admin,portal)
        plan = load_plan(raw_sources=True)
        generated = capture(self.admin,reference,raw_sources=True)
        self.assertEqual(plan['steps'],generated['steps'])
        for table in snapshot(self.admin,portal)['tables']:
            for i,constraint in enumerate(table['constraints']):
                if constraint['type'] == 'c':
                    self.admin.execute('ALTER TABLE '+identifier(portal)+'.'+identifier(table['name'])+
                        ' RENAME CONSTRAINT '+identifier(constraint['name'])+' TO '+identifier('gui_'+str(i)))
        before = self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace "
            "AND relkind='r' ORDER BY oid",(portal,)).fetchall()
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/'all-59'
            manifest = prepare(snapshot(self.admin,portal),bundle,plan)
            self.assertEqual(59,len(manifest['steps']))
            for step in manifest['steps']:
                verify(snapshot(self.admin,portal),bundle,step['number'],'before')
                self.admin.execute((bundle/step['file']).read_text())
                verify(snapshot(self.admin,portal),bundle,step['number'],'after')
            verify(snapshot(self.admin,portal),bundle)
        self.assertEqual(before,self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace "
            "AND relkind='r' ORDER BY oid",(portal,)).fetchall())
        self.admin.execute((ROOT/'db/003_grants.sql').read_text().replace('public.',identifier(portal)+'.')
                           .replace('SCHEMA public','SCHEMA '+identifier(portal)))
        with patch.dict('os.environ',{'SANITY_DB_SCHEMA':portal}):
            with Database('vwp') as db:RawSourceStore(db,'synthetic-all-59')
            with Database('oa') as db:
                with self.assertRaises(psycopg2.errors.InsufficientPrivilege):db.execute('SELECT raw_bytes FROM {vwp}')
