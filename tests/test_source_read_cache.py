"""OA read cache must preserve source bytes, current bindings and privacy checks."""
import copy
from dataclasses import replace
import gzip
import hashlib
import json
import os
import unittest
from unittest.mock import patch

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_privacy import SecretGuard
from common.sanity_shared_sources import MARKER
from common.sanity_submission import message_id, packet_base
from oa.sanity_source_reader import BodyCache, CACHE, SourceReader, VerifiedBody, _body_bytes, _packet_bytes
from tests.support import PgTestCase, ROOT


class SourceReadPureTests(unittest.TestCase):
    def test_fast_secret_scan_matches_existing_redaction_semantics(self):
        guard = SecretGuard({'SYNTHETIC_TOKEN': 'known-synthetic-value'})
        strings = ['', 'ordinary ASCII field', '한국어 정상 로그', '0123456789abcdef'*4,
            'PASSWORD=private', 'api-key: private', 'API_KEY="private"', 'Bearer private', 'Basic private',
            'postgresql://user:private@synthetic/db', 'known-synthetic-value',
            '-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----',
            'hex:ff', 'hex:61', 'baSİc private', 'ſECRET=private', 'TOKEN=[REDACTED]']
        values = strings + [[s, {'raw': s}] for s in strings] + [
            {'token': '[REDACTED]'}, {'token': ''}, {'token': False}, {'token': 1}, {'apiKey': 'private'},
            {'raw': 'hex:61', 'raw_omitted_chars': 1}, {'raw': 'hex:61', 'raw_omitted_chars': 0},
            {'known-synthetic-value': 'changed key'}, {'private_key': {'nested': 'private'}}]
        for value in values:
            before = copy.deepcopy(value); expected = guard.redact(value) == value
            with self.subTest(value=value):
                if expected: guard.ensure_public_json(value)
                else:
                    with self.assertRaises(ReviewError) as caught: guard.ensure_public_json(value)
                    self.assertEqual('SENSITIVE_CONTENT', caught.exception.code)
                self.assertEqual(before, value)
        # A known value can overlap the marker. Existing redaction still accepts
        # an already masked credential field, but scans ordinary string fields.
        guard = SecretGuard({'SYNTHETIC_TOKEN': 'REDACTED'})
        value = {'token': '[REDACTED]'}
        self.assertEqual(value, guard.redact(value)); guard.ensure_public_json(value)
        with self.assertRaises(ReviewError): guard.ensure_public_json({'raw': '[REDACTED]'})

    def test_specialized_encoding_has_the_exact_existing_digest(self):
        for value in ('한국어\n"\\', '<script>synthetic</script>', -1, 0.125, 1e30, None):
            body = {'report_id': 'sr-synthetic', 'part_index': 2, 'records': [{'fields': {'value': value}}]}
            raw, size = _body_bytes(body)
            self.assertEqual(canonical_json(body), raw)
            self.assertEqual(len(canonical_json(body['records'])), size)
            packet = {'schema_version': 2, 'body': {'id': 'sp-'+digest(body), 'body': body}, 'actor': {'x': '한글'}}
            encoded = _packet_bytes(packet, raw)
            self.assertEqual(canonical_json(packet), encoded)
            self.assertEqual(message_id('SOURCE_PART', packet), 's-'+hashlib.sha256(b'["SOURCE_PART",'+encoded+b']').hexdigest())

    def test_cache_bound_expiry_and_independent_json_trees(self):
        value = VerifiedBody(b'{"records":[{"x":1}]}', 'x', 1, 'r', 0, 1, 'p', 'project', 'block')
        cache = BodyCache(maximum_bytes=len(value.body_json)*2, maximum_entries=2, ttl=5)
        with patch('oa.sanity_source_reader.time.monotonic', return_value=0):
            cache.put('a', value); cache.put('b', value); self.assertIs(value, cache.get('a'))
            cache.put('c', value)
            self.assertIsNone(cache.get('b')); self.assertLessEqual(cache.bytes, cache.maximum_bytes)
            first = cache.get('a').decode(); first['records'][0]['x'] = 9
            self.assertEqual(1, cache.get('a').decode()['records'][0]['x'])
            cache.put('large', replace(value, body_json=b'x'*1000)); self.assertIsNone(cache.get('large'))
        with patch('oa.sanity_source_reader.time.monotonic', return_value=6):
            self.assertIsNone(cache.get('a')); self.assertIsNone(cache.get('c')); self.assertEqual(0, cache.bytes)
        cache.clear(); self.assertEqual(0, len(cache.values))


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class SourceReadDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.test_raw_sources import RawSourcesDatabaseTests
        self.case = RawSourcesDatabaseTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        for name in ('011_payload_storage.sql','012_source_evidence_pages.sql','013_shared_source_packets.sql'):
            self.case.admin.execute((ROOT/'db'/name).read_text().replace('public.',self.case.schema+'.'))
        self.case.submit(); self.case.bridge(); self.case.receive(); self.case.bridge()
        from common.sanity_db import Database
        with Database('oa') as db:
            self.report = db.execute("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
                "AND payload->>'source_storage'='PARTS_V2' AND payload->'source'->>'layout'='POST'").fetchone()['payload']
            self.row = db.execute("SELECT * FROM {oa} WHERE workspace_key=%s", (self.report['source_parts'][0]['id'],)).fetchone()
        CACHE.clear(); self.addCleanup(CACHE.clear)

    def read(self):
        from common.sanity_db import Database
        from oa.sanity_workspace_sources import hydrate
        with Database('oa') as db: return hydrate(db, self.report)

    def test_warm_cache_skips_decoding_guard_and_canonicalization_but_not_db_reads(self):
        from common.sanity_db import Database
        from oa.sanity_workspace_sources import hydrate
        first = self.read()
        with (patch('oa.sanity_source_reader.unpack_json', side_effect=AssertionError('warm gzip decode')),
              patch.object(SecretGuard, 'ensure_public_json', side_effect=AssertionError('warm scan'))):
            with Database('oa') as db:
                calls = []
                execute = db.execute
                def counted(query, params=None):
                    calls.append(query); return execute(query, params)
                with patch.object(db, 'execute', side_effect=counted): second = hydrate(db, self.report)
                self.assertEqual(2, len(calls))  # fresh private refs + shared bytes
        self.assertEqual(first, second)
        second['records'][0]['raw'] = 'caller mutation'
        self.assertEqual(first, self.read())

    def test_actual_bytes_and_declared_hash_size_and_header_changes_reject_warm_entries(self):
        from common.sanity_db import Database, Queue, json_value
        self.read(); key = self.row['source_packet_ref']
        with Database('oa') as db: original = Queue(db,'vwp_to_oa').get(key)
        changes = [('payload_gzip', original['payload_gzip'][:-1]), ('payload_sha256', '0'*64),
                   ('uncompressed_bytes', original['uncompressed_bytes']+1), ('project', 'another-project')]
        # SQL already rejects a mismatched compressed digest. Simulate a damaged
        # driver row as well; a warm process cache must still independently reject it.
        with Database('oa') as db:
            reader = SourceReader(db)
            for column, changed in changes:
                with self.subTest(column=column), self.assertRaises(ReviewError):
                    reader._shared_body({**original, column: changed})
        from tests.support import psycopg2
        for column, changed in changes[:2]:
            with self.subTest(sql_constraint=column), self.assertRaises(psycopg2.IntegrityError):
                self.case.admin_query('UPDATE {vwp_to_oa} SET '+column+'=%s WHERE message_id=%s', (changed,key))
        # Consistent compressed bytes/digest still cannot change the immutable packet.
        from common.sanity_common import unpack_json, pack_json
        packet = unpack_json(original); packet['body']['body']['records'][0]['raw'] = 'synthetic changed body'
        packed = pack_json(packet)
        self.case.admin_query('UPDATE {vwp_to_oa} SET payload_gzip=%s,payload_sha256=%s,uncompressed_bytes=%s WHERE message_id=%s',
            (packed['payload_gzip'],packed['payload_sha256'],packed['uncompressed_bytes'],key))
        with self.assertRaises(ReviewError): self.read()
        self.case.admin_query('UPDATE {vwp_to_oa} SET payload_gzip=%s,payload_sha256=%s,uncompressed_bytes=%s WHERE message_id=%s',
            (original['payload_gzip'],original['payload_sha256'],original['uncompressed_bytes'],key))
        # workspace_key is tied to body hash by SQL, so change the binding field instead.
        changed = copy.deepcopy(self.row['payload']); changed['report_id'] = 'sr-wrong'
        self.case.admin_query('UPDATE {oa} SET payload=%s WHERE workspace_key=%s', (json_value(changed),self.row['workspace_key']))
        with self.assertRaises(ReviewError): self.read()

    def test_privacy_policy_rotation_revalidates_and_identical_gzip_reencoding_is_valid(self):
        from common.sanity_db import Database, Queue
        first = self.read(); secret = first['records'][0]['raw'].strip()
        with patch.dict(os.environ, {'SYNTHETIC_CACHE_TOKEN': secret}):
            # load_environment allows only registered config; inject policy explicitly here.
            with patch('oa.sanity_source_reader.SecretGuard', side_effect=lambda: SecretGuard({'TEST_TOKEN': secret})):
                with self.assertRaises(ReviewError): self.read()
        key = self.row['source_packet_ref']
        with Database('oa') as db: original = Queue(db,'vwp_to_oa').get(key)
        packed = gzip.compress(gzip.decompress(original['payload_gzip']), compresslevel=1, mtime=123)
        self.case.admin_query('UPDATE {vwp_to_oa} SET payload_gzip=%s,payload_sha256=%s WHERE message_id=%s',
                             (packed,hashlib.sha256(packed).hexdigest(),key))
        self.assertEqual(first, self.read())

    def test_pagination_and_filters_keep_order_and_complete_counts(self):
        from common.sanity_db import Database
        from oa.sanity_workspace_sources import records_page
        full = self.read()['records']
        with Database('oa') as db:
            page = records_page(db,self.report,offset=1,limit=2)
            self.assertEqual([r['id'] for r in full[1:3]],[r['id'] for r in page['items']])
            self.assertEqual(len(full),page['total'])
            group = {full[0]['id'],full[-1]['id']}
            page = records_page(db,self.report,offset=1,limit=1,member_ids=group)
            self.assertEqual(full[-1]['id'],page['items'][0]['id']); self.assertEqual(2,page['total'])
            page = records_page(db,self.report,search='never present synthetic query')
            self.assertEqual([],page['items']); self.assertEqual(0,page['total'])

    def test_missing_part_or_foreign_report_binding_cannot_use_cached_body(self):
        from common.sanity_db import Database
        from oa.sanity_workspace_sources import hydrate
        self.read()
        for change in ('ref', 'scope', 'report', 'count', 'bytes'):
            report = copy.deepcopy(self.report)
            if change == 'ref': report['source_parts'][0]['id'] = 'sp-'+'0'*64
            if change == 'scope': report['block_id'] = 'foreign-block'
            if change == 'report': report['id'] = 'sr-foreign-report'
            if change == 'count': report['record_count'] += 1
            if change == 'bytes': report['source_bytes'] += 1
            with self.subTest(change=change), Database('oa') as db, self.assertRaises(ReviewError): hydrate(db, report)


@unittest.skipIf(getattr(PgTestCase, '__unittest_skip__', False), 'Disposable PostgreSQL fixture required')
class SourceReadAuthorizationTests(unittest.TestCase):
    def test_revoked_owner_cannot_read_cached_source_or_reuse_an_old_response(self):
        from tests.test_submission_tokenless import TokenlessSubmissionTests, OWNER, ADMIN
        c = TokenlessSubmissionTests()
        CACHE.clear()
        try:
            c.setUp()
            for name in ('011_payload_storage.sql','012_source_evidence_pages.sql','013_shared_source_packets.sql'):
                c.admin.execute((ROOT/'db'/name).read_text().replace('public.', c.schema+'.'))
            sid = c.submit(); c.success(c.decide(sid)); c.bridge()
            report = c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
                "AND payload->'source'->>'layout'='POST'").fetchone()[0]
            path = '/api/workbench/blocks/block-a1/versions/'+report['version_id']+'/reports/'+report['id']+'/workbench'
            self.assertEqual(200,c.clients[OWNER].get(path).status_code)
            # Capability/header polling no longer hydrates entire reports.
            # Warm the cache through an authorized evidence-page read instead.
            records=path.removesuffix('/workbench')+'/clustering/records?expected_revision=0&limit=25'
            self.assertEqual(200,c.clients[OWNER].get(records).status_code)
            self.assertGreater(len(CACHE.values),0)
            c.success(c.post(ADMIN,'/api/admin/grants/'+c.owner_grant+'/revoke',c.revoke_body()))
            with patch.object(SourceReader,'parts',side_effect=AssertionError('Revoked caller reached cached evidence')):
                self.assertIn(c.clients[OWNER].get(path).status_code,(403,404))
        finally:
            c.doCleanups(); CACHE.clear()
