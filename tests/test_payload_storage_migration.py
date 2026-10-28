"""Optional 011 portal extension preserves the historical 55/59-step plans."""
from pathlib import Path
import tempfile
import unittest

from db.portal.build_reference import snapshot
from db.portal.runbook import checkpoints, fingerprint, identifier, load_plan, prepare, verify
from tests.support import PgTestCase, ROOT


class PayloadStoragePortalTests(unittest.TestCase):
    def test_011_is_an_explicit_60th_step_with_unchanged_prior_steps(self):
        base, raw, packed = load_plan(), load_plan(raw_sources=True), load_plan(payload_storage=True)
        self.assertEqual(55, len(base['steps']))
        self.assertEqual(59, len(raw['steps']))
        self.assertEqual(60, len(packed['steps']))
        self.assertEqual(raw['steps'], packed['steps'][:59])
        self.assertEqual('011', packed['steps'][59]['phase'])
        states = list(checkpoints(packed))
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/'011'
            manifest = prepare(states[59], bundle, packed)
            self.assertEqual([60], [s['number'] for s in manifest['steps']])
            self.assertFalse(manifest['operational_ready'])
            statement = (bundle/manifest['steps'][0]['file']).read_text()
            self.assertEqual(1, statement.count(';'))
            self.assertIn('SET STORAGE EXTERNAL', statement)
            verify(states[59], bundle, 60, 'before')
            verify(states[60], bundle)


class PayloadStorageMigrationTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql', '009_sso_identity.sql', '010_raw_sources.sql'):
            self.admin.execute((ROOT/'db'/name).read_text().replace('public.', identifier(self.schema)+'.'))

    def test_checkpoint_59_prepares_and_verifies_the_exact_original_011_catalog(self):
        plan = load_plan(payload_storage=True)
        self.assertEqual(fingerprint(list(checkpoints(plan))[59]), fingerprint(snapshot(self.admin, self.schema)))
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/'011'
            manifest = prepare(snapshot(self.admin, self.schema), bundle, plan)
            self.assertEqual([60], [step['number'] for step in manifest['steps']])
            step = manifest['steps'][0]
            self.admin.execute((bundle/step['file']).read_text())
            verify(snapshot(self.admin, self.schema), bundle, 60, 'after')
        with self.admin.cursor() as cursor:
            cursor.execute("SELECT attstorage FROM pg_attribute WHERE attrelid=%s::regclass "
                           "AND attname='payload_bytes'", (identifier(self.schema)+'.sanity_vwp_data',))
            self.assertEqual('e', cursor.fetchone()[0])
