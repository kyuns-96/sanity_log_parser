"""012 portal checkpoints preserve the complete historical 55/59/60-step plans."""
from pathlib import Path
import tempfile
import unittest

from db.portal.build_reference import snapshot
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
