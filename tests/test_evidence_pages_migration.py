"""012 portal checkpoints preserve the complete historical 55/59/60-step plans."""
from pathlib import Path
import tempfile
import unittest
import uuid

from db.portal.build_reference import capture, create_forms, snapshot
from db.portal.runbook import checkpoints, fingerprint, identifier, load_plan, prepare, verify
from tests.support import PgTestCase, ROOT


class EvidencePagesPortalTests(unittest.TestCase):
    def test_012_is_explicit_and_keeps_every_earlier_step(self):
        prior, pages = load_plan(payload_storage=True), load_plan(evidence_pages=True)
        self.assertEqual(62, len(pages['steps']))
        self.assertEqual(prior['steps'], pages['steps'][:60])
        self.assertEqual(['012', '012'], [step['phase'] for step in pages['steps'][60:]])
        states = list(checkpoints(pages))
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/'012'
            manifest = prepare(states[60], bundle, pages)
            self.assertEqual([61, 62], [s['number'] for s in manifest['steps']])
            self.assertFalse(manifest['operational_ready'])
            for step in manifest['steps']:
                self.assertEqual(1, (bundle/step['file']).read_text().count(';'))
            verify(states[60], bundle, 61, 'before')
            verify(states[62], bundle)


class EvidencePagesMigrationTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql', '009_sso_identity.sql', '010_raw_sources.sql', '011_payload_storage.sql'):
            self.admin.execute((ROOT/'db'/name).read_text().replace('public.', identifier(self.schema)+'.'))

    def test_checkpoint_60_applies_only_012_with_exact_original_catalog(self):
        plan = load_plan(evidence_pages=True)
        self.assertEqual(fingerprint(list(checkpoints(plan))[60]), fingerprint(snapshot(self.admin, self.schema)))
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/'012'
            manifest = prepare(snapshot(self.admin, self.schema), bundle, plan)
            self.assertEqual([61, 62], [s['number'] for s in manifest['steps']])
            for step in manifest['steps']:
                self.admin.execute((bundle/step['file']).read_text())
                verify(snapshot(self.admin, self.schema), bundle, step['number'], 'after')
            verify(snapshot(self.admin, self.schema), bundle)

    def test_all_62_steps_match_original_sql_and_preserve_every_table_oid(self):
        portal, reference = 'pages_portal_'+uuid.uuid4().hex, 'pages_reference_'+uuid.uuid4().hex
        for schema in (portal, reference):
            self.addCleanup(self.admin.execute, 'DROP SCHEMA IF EXISTS '+identifier(schema)+' CASCADE')
        create_forms(self.admin, portal)
        generated = capture(self.admin, reference, evidence_pages=True)
        plan = load_plan(evidence_pages=True)
        self.assertEqual(plan['steps'], generated['steps'])
        query = "SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid"
        before = self.admin.execute(query, (portal,)).fetchall()
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/'all-62'
            manifest = prepare(snapshot(self.admin, portal), bundle, plan)
            self.assertEqual(62, len(manifest['steps']))
            for step in manifest['steps']:
                verify(snapshot(self.admin, portal), bundle, step['number'], 'before')
                self.admin.execute((bundle/step['file']).read_text())
                verify(snapshot(self.admin, portal), bundle, step['number'], 'after')
            verify(snapshot(self.admin, portal), bundle)
        self.assertEqual(before, self.admin.execute(query, (portal,)).fetchall())
