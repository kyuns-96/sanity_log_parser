"""012 lossless pages: identities, bounded reads, replay, coverage and DBA gates."""
import copy
from datetime import datetime, timezone
import hashlib
import os
import unittest
import uuid
from unittest.mock import patch

from common.sanity_common import ReviewError, digest, pack_json
from common.sanity_db import Database, json_value, lock_key
from common.sanity_evidence_pages import (CODEC, KIND, MAX_RECORDS, EvidencePageStorage,
                                         decode_page, encode_page, page_key)
from common.sanity_privacy import SecretGuard
from common.sanity_raw_sources import RawSourceStore
from common.sanity_submission_store import SubmissionStore
from tests import test_payload_storage as fixtures
from tests.support import PgTestCase, ROOT, psycopg2
from vwp_bridge.payload_compaction import MaintenanceDatabase, compact, fingerprint
from vwp_bridge.submission_analysis import SourceEvidence, public_records


def items(count=3, first=1):
    return [{'payload': {'record_id': 'r-' + digest(['synthetic-page', i]), 'record_index': i,
                         'review_unit_id': 'synthetic-unit', 'check_status': 'UNKNOWN',
                         'fields': {'unicode': '합성\n\u0000'}, 'json_pointer': '/records/' + str(i)},
             'raw': '합성 원문\r\n\u0000' + str(i)} for i in range(first, first + count)]


def page_row(values=None):
    values = items() if values is None else values
    header, blob = encode_page(values)
    analysis = uuid.uuid4()
    key = page_key(header['record_index'])
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {'record_type': KIND, 'record_id': uuid.uuid5(analysis, KIND + ':' + key),
            'analysis_id': analysis, 'entity_key': key, 'review_unit_id': 'synthetic-unit',
            'parent_id': uuid.uuid5(analysis, 'UNIT:synthetic-unit'), 'payload': header,
            'page_bytes': blob, 'payload_bytes': None, 'raw_bytes': None, 'raw_sha256': None,
            'state': {}, 'status': 'RECORDED', 'version': 0, 'created_at': stamp, 'updated_at': stamp}


class EvidencePageCodecTests(unittest.TestCase):
    def test_unicode_empty_raw_and_every_logical_value_restore_exactly(self):
        values = items(MAX_RECORDS)
        values[0]['raw'] = ''
        values[1]['payload']['fields']['nested'] = [None, True, False, 0, '한글']
        row = page_row(values)
        row['page_bytes'] = memoryview(row['page_bytes'])
        restored = decode_page(row)
        self.assertEqual(MAX_RECORDS, len(restored))
        for before, after in zip(values, restored):
            self.assertEqual(before['payload'], after['payload'])
            self.assertEqual(before['raw'].encode(), after['raw_bytes'])
            self.assertEqual(hashlib.sha256(after['raw_bytes']).hexdigest(), after['raw_sha256'])

    def test_empty_oversized_duplicate_and_noncontiguous_pages_are_rejected(self):
        gap, duplicate = items(), items()
        gap[1]['payload']['record_index'] += 1
        duplicate[1]['payload']['record_id'] = duplicate[0]['payload']['record_id']
        for values in ([], items(MAX_RECORDS + 1), gap, duplicate):
            with self.subTest(count=len(values)), self.assertRaises(ReviewError):
                encode_page(values)

    def test_corrupt_blob_and_all_header_fields_fail_closed(self):
        row = page_row()
        variants = []
        for key, value in (('codec', 'other'), ('sha256', '0'*64), ('record_index', True),
                           ('record_end', 99), ('record_count', True), ('uncompressed_bytes', True),
                           ('status_counts', {'PASS': 0, 'FAIL': 0, 'UNKNOWN': True}), ('extra', 'unexpected')):
            changed = copy.deepcopy(row); changed['payload'][key] = value; variants.append(changed)
        for blob in (None, b'bad', row['page_bytes'][:-8]):
            changed = copy.deepcopy(row); changed['page_bytes'] = blob
            if blob: changed['payload']['sha256'] = hashlib.sha256(blob).hexdigest()
            variants.append(changed)
        for changed in variants:
            with self.subTest(header=changed['payload']), self.assertRaises(ReviewError) as error:
                decode_page(changed)
            self.assertEqual('EVIDENCE_PAGE_CORRUPT', error.exception.code)

    def test_page_row_identity_unit_parent_and_immutability_are_bound(self):
        row = page_row()
        for key, value in (('record_id', uuid.uuid4()), ('analysis_id', uuid.uuid4()),
                           ('parent_id', uuid.uuid4()), ('entity_key', 'sep.2'),
                           ('review_unit_id', 'other'), ('state', {'unexpected': 1}),
                           ('version', 1), ('status', 'SEALED'), ('raw_bytes', b'wrong'),
                           ('raw_sha256', '0'*64), ('payload_bytes', b'wrong')):
            changed = copy.deepcopy(row); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ReviewError): decode_page(changed)

    def test_body_shape_and_expansion_limits_are_checked(self):
        row = page_row()
        packed = pack_json({'records': items(), 'unexpected': True})
        row['page_bytes'] = packed['payload_gzip']
        row['payload'].update(sha256=packed['payload_sha256'], uncompressed_bytes=packed['uncompressed_bytes'])
        with self.assertRaises(ReviewError): decode_page(row)
        with patch('common.sanity_evidence_pages.MAX_BYTES', 128), self.assertRaises(ReviewError):
            decode_page(page_row())


class EvidencePageDatabaseTests(PgTestCase):
    manifest = fixtures.PayloadStorageDatabaseTests.manifest
    seed = fixtures.PayloadStorageDatabaseTests.seed
    records = fixtures.PayloadStorageDatabaseTests.records
    source = fixtures.PayloadStorageDatabaseTests.source
    owner = fixtures.PayloadStorageDatabaseTests.owner

    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql', '009_sso_identity.sql', '010_raw_sources.sql', '011_payload_storage.sql'):
            self.admin.execute(self.source(name))

    def install(self):
        self.admin.execute(self.source('012_source_evidence_pages.sql'))

    def physical(self, analysis):
        with self.owner() as db:
            return db.execute('SELECT * FROM {vwp} WHERE analysis_id=%s ORDER BY record_id', (analysis,)).fetchall()

    def test_new_pages_reduce_rows_and_preserve_every_record_and_outbox_packet(self):
        self.install()
        analysis = self.seed(count=600)
        rows = self.physical(analysis)
        self.assertEqual(3, sum(row['record_type'] == KIND for row in rows))
        self.assertFalse(any(row['record_type'] == 'EVIDENCE' for row in rows))
        records = self.records(analysis)
        self.assertEqual(600, len(records))
        for i, record in enumerate(records, 1):
            self.assertEqual('synthetic raw '+str(i)+'\n', record['raw'])
            self.assertEqual(hashlib.sha256(record['raw'].encode()).hexdigest(), record['source_raw_sha256'])
            self.assertEqual('/results/'+str(i-1), record['json_pointer'])
        packet = {'collection_id': 'sc-'+digest('synthetic'), 'body': {'records': records}}
        sid = '00000000-0000-4000-8000-000000000012'
        with Database('vwp') as db:
            self.assertTrue(EvidencePageStorage(db).enabled)
            RawSourceStore(db, 'synthetic-page-node')
            store = SubmissionStore(db)
            store.put_outbox(sid, 0, 'SOURCE_PART', packet)
            value = store.outbox(sid)[0]
            self.assertEqual({'payload'}, set(value))
            self.assertEqual(packet, value['payload']['packet'])
            store.put_outbox(sid, 0, 'SOURCE_PART', packet)

    def test_preexisting_row_analyses_keep_their_layout_after_012(self):
        analysis = self.seed('existing', count=8)
        before, wire = self.physical(analysis), self.records(analysis)
        self.install()
        self.assertEqual(analysis, self.seed('existing', count=8))
        after = [{k: v for k, v in row.items() if k != 'page_bytes'} for row in self.physical(analysis)]
        self.assertEqual(before, after)
        self.assertEqual(wire, self.records(analysis))
        new = self.seed('pages', count=8)
        self.assertEqual(8, len(self.records(new)))

    def test_sealed_page_replay_preserves_all_physical_columns_and_wire(self):
        self.install(); analysis = self.seed(count=600)
        before, wire = self.physical(analysis), self.records(analysis)
        self.assertEqual(analysis, self.seed(count=600))
        self.assertEqual(before, self.physical(analysis))
        self.assertEqual(wire, self.records(analysis))

    def test_replay_compares_logical_records_across_equivalent_gzip_encodings(self):
        import gzip
        from common.sanity_common import canonical_json, unpack_json
        self.install(); analysis = self.seed(count=300)
        wire = self.records(analysis)
        for row in self.physical(analysis):
            if row['record_type'] != KIND:
                continue
            body = unpack_json({'payload_gzip': bytes(row['page_bytes']),
                                'payload_sha256': row['payload']['sha256'],
                                'uncompressed_bytes': row['payload']['uncompressed_bytes']})
            blob = gzip.compress(canonical_json(body), compresslevel=1, mtime=123)
            header = dict(row['payload'], sha256=hashlib.sha256(blob).hexdigest())
            self.admin_query('UPDATE {vwp} SET payload=%s,page_bytes=%s WHERE record_id=%s',
                             (json_value(header), blob, row['record_id']))
        before = self.physical(analysis)
        self.assertEqual(analysis, self.seed(count=300))
        self.assertEqual(before, self.physical(analysis))
        self.assertEqual(wire, self.records(analysis))

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

    def test_missing_first_middle_and_final_pages_never_return_a_complete_report(self):
        self.install()
        for tag, first in (('first', 1), ('middle', 257), ('final', 513)):
            analysis = self.seed(tag, count=600)
            self.admin_query("DELETE FROM {vwp} WHERE analysis_id=%s AND record_type=%s AND entity_key=%s",
                             (analysis, KIND, page_key(first)))
            with self.subTest(page=tag), self.assertRaises(ReviewError): self.records(analysis)

    def test_corrupt_compressed_body_is_rejected_even_with_a_matching_storage_hash(self):
        self.install(); analysis = self.seed()
        row = next(r for r in self.physical(analysis) if r['record_type'] == KIND)
        truncated = bytes(row['page_bytes'])[:-8]
        header = dict(row['payload'], sha256=hashlib.sha256(truncated).hexdigest())
        self.admin_query('UPDATE {vwp} SET payload=%s,page_bytes=%s WHERE record_id=%s',
                         (json_value(header), truncated, row['record_id']))
        with self.assertRaises(ReviewError) as error: self.records(analysis)
        self.assertEqual('EVIDENCE_PAGE_CORRUPT', error.exception.code)

    def test_zero_records_and_byte_bounded_pages_preserve_coverage(self):
        self.install(); empty = self.seed('empty', count=0)
        self.assertEqual([], self.records(empty))
        with patch('vwp_bridge.submission_analysis.PAGE_BYTES', 20000):
            analysis = self.seed('byte-bounded', count=20)
        self.assertEqual(20, len(self.records(analysis)))
        self.assertGreater(sum(r['record_type'] == KIND for r in self.physical(analysis)), 1)

    def test_fingerprint_repack_and_restore_guard_preserve_pages_and_raw_sources(self):
        self.install(); analysis = self.seed(count=300)
        with self.owner() as db: before = fingerprint(db)
        wire = self.records(analysis)
        self.admin.execute(self.source('maintenance/repack_payload_storage.sql'))
        with self.owner() as db:
            self.assertEqual(before, fingerprint(db))
            for apply in (False, True):
                with self.assertRaises(ReviewError) as error: compact(db, restore_plain=True, apply=apply)
                self.assertEqual('PAGE_STORAGE_RESTORE_REQUIRED', error.exception.code)
            self.assertEqual(before, fingerprint(db))
        self.assertEqual(wire, self.records(analysis))
        with Database('vwp') as db:
            self.assertTrue(EvidencePageStorage(db).enabled)
            RawSourceStore(db, 'synthetic-page-node')

    def test_011_restore_cannot_claim_old_reader_compatibility_after_012_even_before_new_pages(self):
        self.seed('existing-rows'); self.install()
        with self.owner() as db:
            before = fingerprint(db)
            with self.assertRaises(ReviewError) as error: compact(db, restore_plain=True, apply=True)
            self.assertEqual('PAGE_STORAGE_RESTORE_REQUIRED', error.exception.code)
            self.assertEqual(before, fingerprint(db))

    def test_partial_schema_and_missing_index_fail_closed(self):
        self.install()
        self.admin.execute('DROP INDEX "'+self.schema+'".sanity_vwp_source_page_start_uq')
        with Database('vwp') as db:
            with self.assertRaises(ReviewError) as error: EvidencePageStorage(db)
            self.assertEqual('EVIDENCE_PAGE_SCHEMA_REQUIRED', error.exception.code)
            with self.assertRaises(ReviewError): RawSourceStore(db, 'synthetic')

    def test_a_wrong_index_with_the_expected_name_does_not_enable_pages(self):
        self.install()
        self.admin.execute('DROP INDEX "'+self.schema+'".sanity_vwp_source_page_start_uq')
        self.admin.execute('CREATE UNIQUE INDEX sanity_vwp_source_page_start_uq ON "'+self.schema+
                           '".sanity_vwp_data(record_id)')
        with Database('vwp') as db:
            with self.assertRaises(ReviewError) as error: EvidencePageStorage(db)
            self.assertEqual('EVIDENCE_PAGE_SCHEMA_REQUIRED', error.exception.code)

    def test_100000_records_preserve_all_ids_and_coverage_with_391_pages(self):
        self.install(); manifest = self.manifest('synthetic-100000')
        with Database('vwp') as db:
            ev = SourceEvidence(db, manifest)
            try:
                for i in range(1, 100001):
                    ev.record(manifest['inventory']['units'][0], manifest['files'][0], i,
                        {'object': 'synthetic/cell_'+str(i), 'code': 'SYNTHETIC-LARGE', 'value': i},
                        'synthetic raw '+str(i)+'\n', {'line_start': i, 'line_end': i},
                        'UNKNOWN', ['SYNTHETIC-NO-CRITERION'])
                ev.seal_source({'record_count': 100000, 'status_counts': {'PASS': 0, 'FAIL': 0, 'UNKNOWN': 100000}})
            finally:
                db.conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(ev.analysis_id),))
            count = 0
            for count, record in enumerate(public_records(db, ev.analysis_id, SecretGuard()), 1):
                expected_id = 'r-'+digest([manifest['run']['run_id'], manifest['inventory']['units'][0]['review_unit_id'],
                                           manifest['files'][0]['file_id'], count])
                self.assertEqual(expected_id, record['id'])
                self.assertEqual('synthetic raw '+str(count)+'\n', record['raw'])
                self.assertEqual(count, record['fields']['value'])
            self.assertEqual(100000, count)
        rows = self.physical(ev.analysis_id)
        self.assertEqual(391, sum(r['record_type'] == KIND for r in rows))
        self.assertFalse(any(r['record_type'] == 'EVIDENCE' for r in rows))

    def test_app_cannot_update_page_bytes_or_apply_012_and_reapplication_is_atomic(self):
        self.install(); analysis = self.seed()
        with Database('vwp') as db:
            with self.assertRaises(psycopg2.Error): db.execute('UPDATE {vwp} SET page_bytes=NULL WHERE analysis_id=%s', (analysis,))
            with self.assertRaises(psycopg2.Error): db.conn.execute(self.source('012_source_evidence_pages.sql'))
        before = self.physical(analysis)
        with self.assertRaises(psycopg2.Error): self.admin.execute(self.source('012_source_evidence_pages.sql'))
        self.admin.execute('ROLLBACK')
        self.assertEqual(before, self.physical(analysis))


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class PagedSubmissionFlows(unittest.TestCase):
    def run_scenario(self, case_type, method):
        case = case_type(method)
        try:
            case.setUp()
            from db.portal.runbook import identifier
            for name in ('011_payload_storage.sql', '012_source_evidence_pages.sql'):
                case.admin.execute((ROOT/'db'/name).read_text().replace('public.', identifier(case.schema)+'.'))
            getattr(case, method)()
        finally:
            case.doCleanups()

    def test_real_tls_database_originals_and_receipt_with_pages(self):
        from tests.test_raw_sources import RawSourcesDatabaseTests
        self.run_scenario(RawSourcesDatabaseTests,
            'test_real_tls_db_originals_reach_oa_and_receipts_survive_without_server_files')

    def test_interrupted_analysis_resumes_with_pages(self):
        from tests.test_raw_sources import RawSourcesDatabaseTests
        self.run_scenario(RawSourcesDatabaseTests,
            'test_analysis_crash_removes_scratch_and_restarts_from_database_originals')

    def test_tokenless_tls_and_sso_owner_confirmation_with_pages(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests
        self.run_scenario(TokenlessSubmissionTests,
            'test_real_tls_without_token_reaches_pending_then_sso_owner_publishes')

    def test_collection_only_owner_intake_and_human_review_with_pages(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests
        self.run_scenario(TokenlessSubmissionTests,
            'test_collection_only_runs_owner_intake_ai_and_human_review_without_changing_source')
