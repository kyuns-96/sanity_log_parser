"""013 shared packet portal preserves the complete historical 55/59/60/62 plans."""
from pathlib import Path
import tempfile
import uuid

from db.portal.build_reference import capture, create_forms, snapshot
from db.portal.runbook import checkpoints, fingerprint, identifier, load_plan, prepare, verify
from tests.support import PgTestCase, ROOT


class SharedPacketMigrationTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql','009_sso_identity.sql','010_raw_sources.sql',
                     '011_payload_storage.sql','012_source_evidence_pages.sql'):
            self.admin.execute((ROOT/'db'/name).read_text().replace('public.', identifier(self.schema)+'.'))


    def test_all_66_statements_match_original_sql_and_preserve_all_table_oids(self):
        portal, reference = 'shared_portal_'+uuid.uuid4().hex, 'shared_reference_'+uuid.uuid4().hex
        for schema in (portal, reference):
            self.addCleanup(self.admin.execute, 'DROP SCHEMA IF EXISTS '+identifier(schema)+' CASCADE')
        create_forms(self.admin, portal)
        plan = load_plan(shared_packets=True)
        generated = capture(self.admin, reference, shared_packets=True)
        self.assertEqual(plan['steps'], generated['steps'])
        # One complete replay covers the formerly separate 60/62-step installs.
        # Compare each historical plan with original SQL and check its final
        # catalog/OIDs at the matching point, before later extensions run.
        historical = (load_plan(), load_plan(raw_sources=True),
                      load_plan(payload_storage=True), load_plan(evidence_pages=True))
        expected = {}
        for prior in historical:
            count = len(prior['steps'])
            with self.subTest(historical_checkpoint=count):
                self.assertEqual(prior['steps'], plan['steps'][:count])
                self.assertEqual(prior['steps'], generated['steps'][:count])
            expected[count] = list(checkpoints(prior))[-1]
        query = "SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid"
        before = self.admin.execute(query, (portal,)).fetchall()
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)/'all-66'
            manifest = prepare(snapshot(self.admin, portal), bundle, plan)
            self.assertEqual(66, len(manifest['steps']))
            for step in manifest['steps']:
                verify(snapshot(self.admin, portal), bundle, step['number'], 'before')
                self.admin.execute((bundle/step['file']).read_text())
                actual = snapshot(self.admin, portal)
                verify(actual, bundle, step['number'], 'after')
                if step['number'] in expected:
                    with self.subTest(historical_checkpoint=step['number']):
                        self.assertEqual(fingerprint(expected[step['number']]), fingerprint(actual))
                        self.assertEqual(before, self.admin.execute(query, (portal,)).fetchall())
            verify(snapshot(self.admin, portal), bundle)
        self.assertEqual(before, self.admin.execute(query, (portal,)).fetchall())
