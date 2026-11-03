"""Committed evidence pages resume without missing or duplicate records."""
import os

from common.sanity_common import digest
from common.sanity_db import Database, lock_key
from common.sanity_evidence_pages import KIND
from tests import test_payload_storage as fixtures
from tests.support import PgTestCase, ROOT
from common.sanity_privacy import SecretGuard
from vwp_bridge.payload_compaction import MaintenanceDatabase
from vwp_bridge.submission_analysis import public_records
from vwp_bridge.submission_analysis import SourceEvidence


class EvidencePageDatabaseTests(PgTestCase):
    def source(self, name):
        from db.portal.runbook import identifier
        return (ROOT/'db'/name).read_text().replace('public.', identifier(self.schema)+'.')

    def owner(self):
        return MaintenanceDatabase(os.environ['SANITY_TEST_ADMIN_DSN'], self.schema)

    def manifest(self, tag):
        uid = 'u-'+digest(['synthetic-unit', tag])
        return {'run': {'run_id': 'synthetic-'+tag, 'project': 'synthetic', 'design': 'synthetic-block',
                       'stage': 'sanity', 'tool': 'synthetic', 'tool_version': '1'},
                'inventory': {'confirmed': True, 'source': 'synthetic', 'version': '1',
                              'units': [{'review_unit_id': uid, 'item_id': 'synthetic-item',
                                         'rule_version': '1', 'report': 'reports/synthetic.rpt'}]},
                'files': [{'file_id': 'f-'+digest(tag), 'path': 'reports/synthetic.rpt',
                           'sha256': digest('synthetic file'), 'size_bytes': 20}], 'unexpected_files': []}

    def seed(self, tag='source', count=8):
        manifest = self.manifest(tag)
        with Database('vwp') as db:
            ev = SourceEvidence(db, manifest)
            try:
                for i in range(1, count+1):
                    ev.record(manifest['inventory']['units'][0], manifest['files'][0], i,
                              fixtures.payload()['fields'], 'synthetic raw '+str(i)+'\n',
                              {'line_start': i, 'line_end': i, 'json_pointer': '/results/'+str(i-1)},
                              'UNKNOWN', ['SYNTHETIC-NO-CRITERION'])
                ev.seal_source({'record_count': count, 'status_counts': {'PASS': 0, 'FAIL': 0, 'UNKNOWN': count}})
                return ev.analysis_id
            finally:
                db.conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(ev.analysis_id),))

    def records(self, analysis):
        with Database('vwp') as db:
            return list(public_records(db, analysis, SecretGuard()))

    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql', '009_sso_identity.sql', '010_raw_sources.sql', '011_payload_storage.sql'):
            self.admin.execute(self.source(name))

    def install(self):
        self.admin.execute(self.source('012_source_evidence_pages.sql'))

    def physical(self, analysis):
        with self.owner() as db:
            return db.execute('SELECT * FROM {vwp} WHERE analysis_id=%s ORDER BY record_id', (analysis,)).fetchall()


    def test_crash_after_committed_full_page_resumes_without_missing_or_duplicate_logs(self):
        self.install(); manifest = self.manifest('restart')
        with Database('vwp') as db:
            ev = SourceEvidence(db, manifest)
            try:
                for i in range(1, 261):
                    ev.record(manifest['inventory']['units'][0], manifest['files'][0], i,
                        fixtures.payload()['fields'], 'synthetic raw '+str(i)+'\n',
                        {'line_start': i, 'line_end': i, 'json_pointer': '/results/'+str(i-1)},
                        'UNKNOWN', ['SYNTHETIC-NO-CRITERION'])
                ev.flush()  # The unfinished four-record page remains in memory.
                first = [r for r in self.physical(ev.analysis_id) if r['record_type'] == KIND]
                self.assertEqual(1, len(first))
                root = next(r for r in self.physical(ev.analysis_id) if r['record_type'] == 'RUN')
                self.assertEqual(256, root['state']['checkpoint_records_verified'])
            finally:
                db.conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(ev.analysis_id),))
        analysis = self.seed('restart', count=600)
        self.assertEqual(600, len(self.records(analysis)))
        self.assertIn(first[0], self.physical(analysis))
