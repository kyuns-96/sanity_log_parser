"""Lossless private payloads, mixed deployments, DBA compaction, and 011 ACLs."""
import copy
import hashlib
import os
import unittest
import uuid
from unittest.mock import patch

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import Database, lock_key
from common.sanity_payload_storage import (MARKER, PrivatePayloadStorage,
    decode_payload, encode_evidence, encode_payload, restore_row)
from common.sanity_privacy import SecretGuard
from common.sanity_raw_sources import RawSourceStore
from common.sanity_submission_store import SubmissionStore
from tests.support import PgTestCase, ROOT, psycopg2
from vwp_bridge.payload_compaction import MaintenanceDatabase, compact, fingerprint, logical_row
from vwp_bridge.submission_analysis import SourceEvidence, public_records


def payload():
    return {'record_id': 'r-'+digest('synthetic-record'), 'record_index': 1,
            'check_status': 'UNKNOWN', 'finding_group_id': None,
            'fields': {'detail': 'synthetic evidence detail ' * 300},
            'reasons': ['SYNTHETIC-NO-CRITERION'], 'json_pointer': '/results/0'}


class PayloadCodecTests(unittest.TestCase):
    def test_complete_payload_and_unicode_location_restore_exactly(self):
        value = payload()
        value['fields']['unicode'] = '합성 근거\n\u0000'
        header, packed = encode_payload('EVIDENCE', value)
        self.assertIsNotNone(packed)
        self.assertLess(len(canonical_json(header))+len(packed), len(canonical_json(value)))
        self.assertEqual(value, decode_payload('EVIDENCE', header, packed))
        self.assertEqual(value, decode_payload('EVIDENCE', header, memoryview(packed)))

    def test_small_payload_is_kept_plain(self):
        value = {'synthetic': 'small'}
        self.assertEqual((value, None), encode_payload('EVIDENCE', value))
        self.assertEqual(value, decode_payload('EVIDENCE', value))

    def test_evidence_raw_bytes_are_retained_inside_the_checked_compressed_body(self):
        value = payload()
        for raw in (b'', '합성 원문\r\n\u0000'.encode()):
            header, stored_raw, packed = encode_evidence(value, raw)
            self.assertIsNone(stored_raw)
            row = {'entity_key': value['record_id'], 'payload': header, 'raw_bytes': stored_raw,
                   'payload_bytes': packed, 'raw_sha256': hashlib.sha256(raw).hexdigest()}
            restored = restore_row('EVIDENCE', row)
            self.assertEqual(value, restored['payload'])
            self.assertEqual(raw, restored['raw_bytes'])
            row['raw_sha256'] = '0'*64
            with self.assertRaises(ReviewError): restore_row('EVIDENCE', row)

    def test_incompressible_payload_does_not_grow(self):
        import random
        value = payload()
        value['fields'] = {'random': random.Random(7).randbytes(400).hex()}
        header, packed = encode_payload('EVIDENCE', value)
        self.assertTrue(packed is None or len(canonical_json(header))+len(packed)+64 < len(canonical_json(value)))
        self.assertEqual(value, decode_payload('EVIDENCE', header, packed))

    def test_outbox_packet_and_collection_id_are_preserved(self):
        value = {'submission_id': str(uuid.uuid4()), 'position': 1, 'kind': 'SOURCE_PART',
                 'message_id': 's-'+digest('synthetic-message'),
                 'packet': {'collection_id': 'sc-'+digest('synthetic-collection'), 'body': payload()}}
        header, packed = encode_payload('SOURCE_OUTBOX', value)
        self.assertIsNotNone(packed)
        self.assertEqual(value['packet']['collection_id'], header['packet']['collection_id'])
        self.assertEqual(value, decode_payload('SOURCE_OUTBOX', header, packed))

    def test_corrupt_hash_gzip_and_indexed_header_fail_closed(self):
        header, packed = encode_payload('EVIDENCE', payload())
        variants = []
        wrong_hash = copy.deepcopy(header); wrong_hash[MARKER]['sha256'] = '0'*64
        variants.append((wrong_hash, packed))
        wrong_index = copy.deepcopy(header); wrong_index['record_index'] = 2
        variants.append((wrong_index, packed))
        wrong_type = copy.deepcopy(header); wrong_type['record_index'] = True
        variants.append((wrong_type, packed))
        truncated = packed[:-8]
        wrong_gzip = copy.deepcopy(header); wrong_gzip[MARKER]['sha256'] = hashlib.sha256(truncated).hexdigest()
        variants.append((wrong_gzip, truncated))
        for changed, blob in variants:
            with self.subTest(header=changed), self.assertRaises(ReviewError) as error:
                decode_payload('EVIDENCE', changed, blob)
            self.assertEqual('STORED_PAYLOAD_CORRUPT', error.exception.code)

    def test_invalid_codec_size_and_extra_metadata_are_rejected(self):
        header, packed = encode_payload('EVIDENCE', payload())
        for change in ({'codec': 'unknown'}, {'uncompressed_bytes': True},
                       {'uncompressed_bytes': 64*1024*1024+1}, {'extra': 'unexpected'}):
            modified = copy.deepcopy(header); modified[MARKER].update(change)
            with self.subTest(change=change), self.assertRaises(ReviewError):
                decode_payload('EVIDENCE', modified, packed)

    def test_compressed_expansion_is_bounded(self):
        header, packed = encode_payload('EVIDENCE', payload())
        with patch('common.sanity_payload_storage.MAX_BYTES', 128), self.assertRaises(ReviewError):
            decode_payload('EVIDENCE', header, packed)

    def test_missing_blob_and_disallowed_record_types_are_rejected(self):
        header, packed = encode_payload('EVIDENCE', payload())
        for kind, blob in (('EVIDENCE', None), ('RAW_CHUNK', packed)):
            with self.subTest(kind=kind), self.assertRaises(ReviewError):
                decode_payload(kind, header, blob)


class PayloadStorageDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql', '009_sso_identity.sql', '010_raw_sources.sql'):
            self.admin.execute(self.source(name))

    def source(self, name):
        from db.portal.runbook import identifier
        return (ROOT/'db'/name).read_text().replace('public.', identifier(self.schema)+'.')

    def install(self):
        self.admin.execute(self.source('011_payload_storage.sql'))

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
                              payload()['fields'], 'synthetic raw '+str(i)+'\n',
                              {'line_start': i, 'line_end': i, 'json_pointer': '/results/'+str(i-1)},
                              'UNKNOWN', ['SYNTHETIC-NO-CRITERION'])
                ev.seal_source({'record_count': count, 'status_counts': {'PASS': 0, 'FAIL': 0, 'UNKNOWN': count}})
                return ev.analysis_id
            finally:
                db.conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(ev.analysis_id),))

    def records(self, analysis):
        with Database('vwp') as db:
            return list(public_records(db, analysis, SecretGuard()))

    def snapshot_rows(self):
        with self.owner() as db:
            rows = db.execute('SELECT * FROM {vwp} ORDER BY record_id').fetchall()
            return rows, [logical_row(row) for row in rows]

    def test_old_schema_plain_and_new_schema_compressed_sources_share_the_same_reader(self):
        old = self.seed('old')
        expected = self.records(old)
        self.install()
        self.assertEqual(expected, self.records(old))
        new = self.seed('new')
        records = self.records(new)
        self.assertEqual(8, len(records))
        for i, record in enumerate(records, 1):
            self.assertEqual(payload()['fields'], record['fields'])
            self.assertEqual('synthetic raw '+str(i)+'\n', record['raw'])
            self.assertEqual('/results/'+str(i-1), record['json_pointer'])
            self.assertEqual(hashlib.sha256(record['raw'].encode()).hexdigest(), record['source_raw_sha256'])
        self.assertEqual(8, self.admin_query('SELECT count(*) FROM {vwp} WHERE analysis_id=%s '
                                           'AND payload_bytes IS NOT NULL', (new,)).fetchone()[0])

    def test_sealed_source_replay_preserves_physical_rows_and_logical_records(self):
        self.install(); analysis = self.seed()
        before, _ = self.snapshot_rows()
        expected = self.records(analysis)
        self.assertEqual(analysis, self.seed())
        self.assertEqual(before, self.snapshot_rows()[0])
        self.assertEqual(expected, self.records(analysis))

    def test_dry_run_backfill_restart_and_restore_preserve_all_logical_columns(self):
        analysis = self.seed()
        with self.owner() as db: original_fingerprint = fingerprint(db)
        self.install()
        before, logical = self.snapshot_rows()
        records = self.records(analysis)
        with self.owner() as db:
            preview = compact(db)
            self.assertFalse(preview['applied'])
            self.assertEqual(0, preview['rows_changed'])
            self.assertEqual(8, preview['rows_would_change'])
        self.assertEqual(before, self.snapshot_rows()[0])
        with self.owner() as db:
            first = compact(db, apply=True, max_rows=3, batch_size=2)
            self.assertEqual(3, first['rows_changed'])
            second = compact(db, apply=True)
            self.assertEqual(5, second['rows_changed'])
            self.assertEqual(0, compact(db, apply=True)['rows_changed'])
        self.assertEqual(logical, self.snapshot_rows()[1])
        self.assertEqual(records, self.records(analysis))
        with self.owner() as db: self.assertEqual(original_fingerprint, fingerprint(db))
        with self.owner() as db:
            restored = compact(db, apply=True, restore_plain=True)
            self.assertEqual(8, restored['rows_changed'])
        self.assertEqual(before, self.snapshot_rows()[0])
        with self.owner() as db: self.assertEqual(original_fingerprint, fingerprint(db))

    def test_restore_plain_includes_committed_evidence_from_unfinished_analyses(self):
        self.install()
        manifest = self.manifest('unfinished')
        with Database('vwp') as db:
            ev = SourceEvidence(db, manifest)
            try:
                ev.record(manifest['inventory']['units'][0], manifest['files'][0], 1,
                          payload()['fields'], 'synthetic raw 1\n',
                          {'line_start': 1, 'line_end': 1, 'json_pointer': '/results/0'},
                          'UNKNOWN', ['SYNTHETIC-NO-CRITERION'])
                ev.flush()
                analysis = ev.analysis_id
            finally:
                db.conn.execute('SELECT pg_advisory_unlock(%s)', (lock_key(ev.analysis_id),))
        run = self.admin_query("SELECT status,state FROM {vwp} WHERE record_id=%s", (analysis,)).fetchone()
        self.assertEqual('BUILDING', run[0])
        self.assertNotIn('source_protocol', run[1])
        before, logical = self.snapshot_rows()
        self.assertEqual(1, sum(row['payload_bytes'] is not None for row in before))
        with self.owner() as db:
            original_fingerprint = fingerprint(db)
            self.assertEqual(0, compact(db)['rows_examined'])
            preview = compact(db, restore_plain=True)
            self.assertEqual(1, preview['rows_would_change'])
        self.assertEqual(before, self.snapshot_rows()[0])
        with self.owner() as db:
            self.assertEqual(1, compact(db, apply=True, restore_plain=True)['rows_changed'])
            self.assertEqual(0, compact(db, apply=True, restore_plain=True)['rows_changed'])
            self.assertEqual(original_fingerprint, fingerprint(db))
        restored, restored_logical = self.snapshot_rows()
        self.assertEqual(logical, restored_logical)
        self.assertTrue(all(row['payload_bytes'] is None for row in restored))
        self.assertEqual(analysis, self.seed('unfinished', count=1))

    def test_repack_keeps_every_byte_hash_id_revision_timestamp_and_payload(self):
        analysis = self.seed(); self.install()
        with Database('vwp') as db:
            raw = b'\x00\xffSYNTHETIC\r\n'*50000
            sha = hashlib.sha256(raw).hexdigest()
            RawSourceStore(db, 'synthetic-repack').immutable('RAW_CHUNK', 'rawc.'+sha,
                {'schema_version': 1, 'protocol': 'raw-source-v1', 'sha256': sha, 'size_bytes': len(raw)}, raw)
        with self.owner() as db: compact(db, apply=True)
        before = self.snapshot_rows()
        records = self.records(analysis)
        self.admin.execute(self.source('maintenance/repack_payload_storage.sql'))
        self.assertEqual(before, self.snapshot_rows())
        with Database('vwp') as db:
            self.assertTrue(PrivatePayloadStorage(db).enabled)
            RawSourceStore(db, 'synthetic-repack')
        self.assertEqual(records, self.records(analysis))

    def test_compaction_error_rolls_back_the_current_batch(self):
        self.seed(); self.install()
        before = self.snapshot_rows()
        with self.owner() as db:
            original, writes = db.execute, []
            def fail(query, params=None):
                if query.startswith('UPDATE '):
                    writes.append(1)
                    if len(writes) == 2:
                        raise RuntimeError('synthetic interrupted compaction')
                return original(query, params)
            with patch.object(db, 'execute', side_effect=fail), self.assertRaises(RuntimeError):
                compact(db, apply=True, batch_size=4)
        self.assertEqual(before, self.snapshot_rows())

    def test_blob_and_indexed_header_corruption_are_rejected(self):
        self.install(); analysis = self.seed()
        with self.assertRaises(psycopg2.Error):
            self.admin_query("UPDATE {vwp} SET payload_bytes='bad'::bytea WHERE payload_bytes IS NOT NULL")
        self.admin.execute('ROLLBACK')
        self.admin_query("UPDATE {vwp} SET payload=jsonb_set(payload,'{{record_index}}','999') "
                         "WHERE payload_bytes IS NOT NULL")
        with self.assertRaises(ReviewError) as error: self.records(analysis)
        self.assertEqual('STORED_PAYLOAD_CORRUPT', error.exception.code)

    def test_partial_migration_and_reapplication_fail_closed(self):
        self.install(); self.seed()
        before = self.snapshot_rows()
        with self.assertRaises(psycopg2.Error): self.install()
        self.admin.execute('ROLLBACK')
        self.assertEqual(before, self.snapshot_rows())
        self.admin_query('ALTER TABLE {vwp} DROP CONSTRAINT sanity_vwp_payload_storage_v1')
        with Database('vwp') as db, self.assertRaises(ReviewError) as error:
            PrivatePayloadStorage(db)
        self.assertEqual('PAYLOAD_STORAGE_SCHEMA_REQUIRED', error.exception.code)

    def test_app_roles_cannot_modify_blobs_or_use_owner_maintenance(self):
        self.install(); self.seed()
        with Database('oa') as db, self.assertRaises(psycopg2.errors.InsufficientPrivilege):
            db.execute('SELECT payload_bytes FROM {vwp}')
        with Database('vwp') as db, self.assertRaises(psycopg2.errors.InsufficientPrivilege):
            db.execute('UPDATE {vwp} SET payload_bytes=payload_bytes')
        with MaintenanceDatabase(os.environ['SANITY_VWP_DSN'], self.schema) as db:
            self.assertFalse(compact(db)['applied'])
            with self.assertRaises(ReviewError) as error: compact(db, apply=True)
            self.assertEqual('COMPACTION_OWNER_REQUIRED', error.exception.code)

    def test_outbox_idempotence_and_original_packet_remain_exact(self):
        self.install()
        sid = str(uuid.uuid4())
        packet = {'collection_id': 'sc-'+digest('synthetic-collection'), 'body': payload()}
        with Database('vwp') as db:
            store = SubmissionStore(db)
            message = store.put_outbox(sid, 0, 'SOURCE_PART', packet)
            self.assertEqual(message, store.put_outbox(sid, 0, 'SOURCE_PART', packet))
            self.assertEqual(packet, store.get('SOURCE_OUTBOX', sid+'.0')['payload']['packet'])
            self.assertEqual(packet, store.outbox(sid)[0]['payload']['packet'])
            self.assertEqual({'payload'}, set(store.outbox(sid)[0]))
            changed = copy.deepcopy(packet); changed['body']['fields']['detail'] = 'different'
            with self.assertRaises(ReviewError): store.put_outbox(sid, 0, 'SOURCE_PART', changed)


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class CompactSubmissionFlows(unittest.TestCase):
    def run_existing_scenario(self, case_type, method):
        case = case_type(method)
        try:
            case.setUp()
            from db.portal.runbook import identifier
            case.admin.execute((ROOT/'db/011_payload_storage.sql').read_text()
                               .replace('public.', identifier(case.schema)+'.'))
            getattr(case, method)()
        finally:
            case.doCleanups()

    def test_real_tls_db_originals_and_oa_receipt_with_compressed_storage(self):
        from tests.test_raw_sources import RawSourcesDatabaseTests
        self.run_existing_scenario(RawSourcesDatabaseTests,
            'test_real_tls_db_originals_reach_oa_and_receipts_survive_without_server_files')

    def test_interrupted_parsing_resumes_with_compressed_storage(self):
        from tests.test_raw_sources import RawSourcesDatabaseTests
        self.run_existing_scenario(RawSourcesDatabaseTests,
            'test_analysis_crash_removes_scratch_and_restarts_from_database_originals')

    def test_tokenless_tls_and_sso_owner_confirmation_with_compressed_storage(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests
        self.run_existing_scenario(TokenlessSubmissionTests,
            'test_real_tls_without_token_reaches_pending_then_sso_owner_publishes')
