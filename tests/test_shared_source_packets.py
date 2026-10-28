"""013 shared immutable packets: publication barrier, references, integrity and ACLs."""
import copy
import gzip
import hashlib
import os
import unittest
from unittest.mock import patch

from common.sanity_common import ReviewError, canonical_json, digest, pack_json, unpack_json
from common.sanity_db import Database, Queue, json_value
from common.sanity_shared_sources import GATE, MARKER, SharedSourcePackets
from common.sanity_submission import SOURCE_TYPES, message_id
from common.sanity_submission_store import SubmissionStore
from oa.sanity_workspace_sources import hydrate
from tests import test_raw_sources as raw_tests
from tests.support import PgTestCase, ROOT, psycopg2
from vwp_bridge.payload_compaction import MaintenanceDatabase, compact, fingerprint


class SharedSourceDatabaseTests(PgTestCase):
    def setUp(self):
        self.case = raw_tests.RawSourcesDatabaseTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.admin, self.schema = self.case.admin, self.case.schema
        for name in ('011_payload_storage.sql', '012_source_evidence_pages.sql'):
            self.admin.execute(self.source(name))

    def source(self, name):
        from db.portal.runbook import identifier
        return (ROOT/'db'/name).read_text().replace('public.', identifier(self.schema)+'.')

    def install(self):
        self.admin.execute(self.source('013_shared_source_packets.sql'))

    def deliver(self):
        sent = self.case.submit()
        self.assertEqual(1, self.case.bridge()['processed'])
        self.case.receive(); self.case.bridge()
        self.assertEqual('OA_STORED', self.case.client.status(sent['submission_id'])['delivery_state'])
        return sent['submission_id']

    def queue_parts(self):
        return self.admin_query("SELECT message_id FROM {vwp_to_oa} WHERE message_type='SOURCE_PART' ORDER BY message_id").fetchall()

    def reports(self):
        with Database('oa') as db:
            rows = db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
                              "AND payload->>'source_storage'='PARTS_V2' ORDER BY workspace_key").fetchall()
            return [hydrate(db, row['payload']) for row in rows]

    def test_one_retained_queue_body_serves_both_private_stores_after_ack(self):
        self.install(); sid = self.deliver()
        keys = {row[0] for row in self.queue_parts()}
        with Database('vwp') as db:
            shared = SharedSourcePackets(db)
            self.assertTrue(shared.enabled)
            store = SubmissionStore(db)
            values = store.outbox(sid)
            for item in values:
                value = item['payload']
                if value['kind'] == 'SOURCE_PART':
                    self.assertEqual(value['packet'], shared.packet(value['message_id'], gated=True))
                    row = db.execute("SELECT * FROM {vwp} WHERE record_type='SOURCE_OUTBOX' AND entity_key=%s",
                                     (sid+'.'+str(value['position']),)).fetchone()
                    self.assertIn(row['source_packet_ref'], keys)
                    self.assertIsNone(row['payload_bytes'])
                    self.assertNotIn('body', row['payload']['packet'])
                    self.assertEqual({'payload'}, set(item))
        with Database('oa') as db:
            self.assertTrue(SharedSourcePackets(db).enabled)
            rows = db.execute("SELECT * FROM {oa} WHERE record_type='WS_SOURCE_PART'").fetchall()
            self.assertEqual(keys, {row['source_packet_ref'] for row in rows})
            for row in rows:
                self.assertNotIn('records', row['payload'])
                self.assertIsNone(row['payload_bytes'])
                self.assertEqual(len(SharedSourcePackets(db).restore_part(row)['payload']['records']),
                                 row['payload'][MARKER]['record_count'])
        reports = self.reports()
        self.assertEqual(8, sum(len(report['records']) for report in reports))
        self.assertTrue(all(report['source_complete'] for report in reports))

    def test_staged_parts_are_not_claimable_until_commit_even_by_exact_id_and_resume_without_duplicates(self):
        self.install(); sent = self.case.submit()
        original, calls = SubmissionStore.put_outbox, []
        def interrupted(store, *args):
            if calls:
                raise RuntimeError('synthetic stop after one durable source part')
            result = original(store, *args); calls.append(result); return result
        with patch.object(SubmissionStore, 'put_outbox', new=interrupted), self.assertRaises(RuntimeError):
            self.case.bridge()
        self.assertEqual(1, len(calls))
        with Database('oa') as db:
            queue = Queue(db, 'vwp_to_oa')
            self.assertIsNone(queue.claim(SOURCE_TYPES))
            self.assertIsNone(queue.claim(('SOURCE_PART',), calls[0]))
        self.case.receive()
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_SUBMISSION'").fetchone()[0])
        self.assertEqual(1, self.case.bridge()['processed'])
        self.case.receive(); self.case.bridge()
        self.assertEqual('OA_STORED', self.case.client.status(sent['submission_id'])['delivery_state'])
        self.assertEqual(2, len(self.queue_parts()))
        self.assertEqual(8, sum(len(report['records']) for report in self.reports()))

    def test_a_mismatched_commit_cannot_release_an_unfinished_source(self):
        self.install(); self.case.submit()
        original = SubmissionStore.put_outbox
        def interrupted(store, sid, position, kind, packet):
            if kind != 'SOURCE_PART':
                raise RuntimeError('synthetic stop before manifest/commit')
            return original(store, sid, position, kind, packet)
        with patch.object(SubmissionStore, 'put_outbox', new=interrupted), self.assertRaises(RuntimeError):
            self.case.bridge()
        with Database('vwp') as db:
            queue = Queue(db, 'vwp_to_oa')
            row = queue.get(self.queue_parts()[0][0])
            packet = unpack_json(row)
            forged = {**packet, 'collection_id': 'sc-'+digest('synthetic-wrong-collection'),
                      'body': {'manifest_sha256': digest('synthetic'), 'parts': 2}}
            header = {k: forged[k] for k in ('schema_version','protocol','submission_id','content_sha256','collection_id')}
            queue.enqueue({'message_id': message_id('SOURCE_COMMIT', forged), 'message_type': 'SOURCE_COMMIT',
                'project': row['project'], 'run_id': None, 'bundle_id': None, 'correlation_id': row['correlation_id'],
                'payload': header, **pack_json(forged)})
        with Database('oa') as db:
            self.assertIsNone(Queue(db, 'vwp_to_oa').claim(('SOURCE_PART',)))

    def test_foreign_keys_prevent_purging_acknowledged_bodies_and_apps_cannot_mutate_them(self):
        self.install(); self.deliver(); key = self.queue_parts()[0][0]
        with self.assertRaises(psycopg2.IntegrityError):
            self.admin_query('DELETE FROM {vwp_to_oa} WHERE message_id=%s', (key,))
        for side in ('vwp', 'oa'):
            with Database(side) as db:
                with self.assertRaises(psycopg2.Error):
                    db.execute('UPDATE {vwp_to_oa} SET payload_gzip=NULL WHERE message_id=%s', (key,))
                own = 'vwp' if side == 'vwp' else 'oa'
                with self.assertRaises(psycopg2.Error):
                    db.execute('UPDATE {'+own+'} SET source_packet_ref=NULL WHERE source_packet_ref=%s', (key,))
        self.assertEqual(8, sum(len(report['records']) for report in self.reports()))

    def test_changed_or_truncated_queue_body_is_not_replaced_by_partial_logs(self):
        self.install(); self.deliver(); key = self.queue_parts()[0][0]
        with Database('vwp') as db:
            row = Queue(db, 'vwp_to_oa').get(key)
        packet = unpack_json(row)
        packet['body']['body']['records'][0]['raw'] = 'synthetic changed raw\n'
        packed = pack_json(packet)
        self.admin_query('UPDATE {vwp_to_oa} SET payload_gzip=%s,payload_sha256=%s,uncompressed_bytes=%s WHERE message_id=%s',
            (packed['payload_gzip'], packed['payload_sha256'], packed['uncompressed_bytes'], key))
        with self.assertRaises(ReviewError): self.reports()
        with Database('vwp') as db, self.assertRaises(ReviewError):
            SubmissionStore(db).outbox(row['correlation_id'])

    def test_a_valid_packet_from_another_block_cannot_supply_a_private_part(self):
        self.install(); self.deliver()
        with Database('oa') as db:
            part = db.execute("SELECT * FROM {oa} WHERE record_type='WS_SOURCE_PART' LIMIT 1").fetchone()
            packet = SharedSourcePackets(db).packet(part['source_packet_ref'])
        packet['scope']['block_key'] = 'synthetic-other-block'
        key = message_id('SOURCE_PART', packet)
        with Database('vwp') as db:
            Queue(db, 'vwp_to_oa').enqueue({'message_id': key, 'message_type': 'SOURCE_PART',
                'project': packet['scope']['project_key'], 'run_id': None, 'bundle_id': None,
                'correlation_id': packet['submission_id'],
                'payload': {k: packet[k] for k in ('schema_version','protocol','submission_id','content_sha256','collection_id')},
                **pack_json(packet)})
        header = copy.deepcopy(part['payload'])
        header[MARKER].update(message_id=key, packet_sha256=digest(packet))
        self.admin_query('UPDATE {oa} SET payload=%s,source_packet_ref=%s WHERE record_id=%s',
                         (json_value(header), key, part['record_id']))
        with self.assertRaises(ReviewError): self.reports()

    def test_equivalent_gzip_idempotence_fingerprint_and_repack_keep_every_record(self):
        self.install(); sid = self.deliver()
        before = self.reports()
        with MaintenanceDatabase(os.environ['SANITY_TEST_ADMIN_DSN'], self.schema) as db:
            expected = fingerprint(db)
            self.assertEqual(0, compact(db, apply=True)['rows_changed'])
        with Database('vwp') as db:
            values = SubmissionStore(db).outbox(sid)
        for value in (item['payload'] for item in values if item['payload']['kind'] == 'SOURCE_PART'):
            packed = gzip.compress(canonical_json(value['packet']), compresslevel=1, mtime=123)
            self.admin_query('UPDATE {vwp_to_oa} SET payload_gzip=%s,payload_sha256=%s WHERE message_id=%s',
                             (packed, hashlib.sha256(packed).hexdigest(), value['message_id']))
            with Database('vwp') as db:
                self.assertEqual(value['message_id'], SubmissionStore(db).put_outbox(
                    sid, value['position'], value['kind'], value['packet']))
        self.admin.execute(self.source('maintenance/repack_payload_storage.sql'))
        with MaintenanceDatabase(os.environ['SANITY_TEST_ADMIN_DSN'], self.schema) as db:
            self.assertEqual(expected, fingerprint(db))
        self.assertEqual(before, self.reports())

    def test_legacy_inline_parts_remain_readable_and_immutable_after_013(self):
        sid = self.deliver(); before = self.reports()
        old = self.admin_query("SELECT record_id,payload FROM {oa} WHERE record_type='WS_SOURCE_PART' ORDER BY record_id").fetchall()
        self.install()
        self.assertEqual(before, self.reports())
        self.assertEqual(old, self.admin_query("SELECT record_id,payload FROM {oa} WHERE record_type='WS_SOURCE_PART' ORDER BY record_id").fetchall())
        with Database('vwp') as db:
            self.assertTrue(SubmissionStore(db).outbox(sid))

    def test_partial_or_cascading_reference_contract_fails_closed(self):
        self.install()
        self.admin.execute('ALTER TABLE "'+self.schema+'".sanity_oa_data DROP CONSTRAINT sanity_oa_source_packet_fk')
        self.admin.execute('ALTER TABLE "'+self.schema+'".sanity_oa_data ADD CONSTRAINT sanity_oa_source_packet_fk '
                           'FOREIGN KEY(source_packet_ref) REFERENCES "'+self.schema+'".sanity_vwp_to_oa(message_id) ON DELETE CASCADE')
        for side in ('vwp','oa'):
            with Database(side) as db, self.assertRaises(ReviewError) as error:
                SharedSourcePackets(db)
            self.assertEqual('SHARED_SOURCE_SCHEMA_REQUIRED', error.exception.code)


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class SharedSourceSubmissionFlows(unittest.TestCase):
    def run_scenario(self, case_type, method, *, before=()):
        case = case_type(method)
        try:
            case.setUp()
            from db.portal.runbook import identifier
            for name in before + ('011_payload_storage.sql','012_source_evidence_pages.sql','013_shared_source_packets.sql'):
                case.admin.execute((ROOT/'db'/name).read_text().replace('public.', identifier(case.schema)+'.'))
            getattr(case, method)()
        finally:
            case.doCleanups()

    def test_real_tls_db_originals_oa_receipt_and_recovery_with_shared_bodies(self):
        self.run_scenario(raw_tests.RawSourcesDatabaseTests,
            'test_real_tls_db_originals_reach_oa_and_receipts_survive_without_server_files')

    def test_tokenless_sso_owner_confirmation_keeps_full_shared_evidence(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests
        self.run_scenario(TokenlessSubmissionTests,
            'test_real_tls_without_token_reaches_pending_then_sso_owner_publishes')

    def test_group_confirmation_human_judgment_and_next_run_references_with_shared_bodies(self):
        from tests.test_submission_database import SubmissionDatabaseTests
        self.run_scenario(SubmissionDatabaseTests,
            'test_received_pages_support_group_confirmation_judgment_and_next_run_cases',
            before=('009_sso_identity.sql','010_raw_sources.sql'))
