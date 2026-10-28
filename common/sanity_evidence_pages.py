"""Bounded, lossless V2 evidence pages inside the existing private VWP table.

012 is explicit. Logical record IDs, parsed values, exact normalized UTF-8 raw
and derived raw SHA256 are unchanged; physical rows/index entries are per page.
Older evidence rows remain readable and are never automatically rewritten.
"""
from __future__ import annotations

import hashlib
import re
import uuid

from common.sanity_common import ReviewError, canonical_json, pack_json, unpack_json

KIND = 'SOURCE_EVIDENCE_PAGE'
CODEC = 'gzip-source-evidence-v1'
MAX_RECORDS = 256
MAX_BYTES = 4 * 1024 * 1024
STATUSES = ('PASS', 'FAIL', 'UNKNOWN')


def check(ok):
    if not ok:
        raise ReviewError('EVIDENCE_PAGE_CORRUPT', 'Complete source evidence page failed validation')


def _records(records):
    check(isinstance(records, list) and 1 <= len(records) <= MAX_RECORDS)
    counts = dict.fromkeys(STATUSES, 0)
    first = None
    identities = set()
    for offset, item in enumerate(records):
        check(isinstance(item, dict) and set(item) == {'payload', 'raw'}
              and isinstance(item['payload'], dict) and isinstance(item['raw'], str))
        value = item['payload']
        check(type(value.get('record_index')) is int and value['record_index'] > 0
              and isinstance(value.get('record_id'), str)
              and re.fullmatch(r'r-[0-9a-f]{64}', value['record_id']) is not None
              and value.get('check_status') in STATUSES)
        if first is None:
            first = value['record_index']
        check(value['record_index'] == first + offset)
        check(value['record_id'] not in identities)
        identities.add(value['record_id'])
        counts[value['check_status']] += 1
    return first, counts


def _header(records, packed):
    first, counts = _records(records)
    return {'codec': CODEC, 'sha256': packed['payload_sha256'],
            'uncompressed_bytes': packed['uncompressed_bytes'], 'record_index': first,
            'record_end': first + len(records) - 1, 'record_count': len(records),
            'status_counts': counts}


def encode_page(records):
    _records(records)
    packed = pack_json({'records': records}, max_bytes=MAX_BYTES)
    return _header(records, packed), packed['payload_gzip']


def page_key(first):
    return 'sep.' + str(first)


def decode_page(row):
    """Check body/hash/limits, every header field, row identity and raw encoding."""
    header = row.get('payload')
    check(row.get('record_type') == KIND and isinstance(header, dict)
          and header.get('codec') == CODEC and isinstance(row.get('page_bytes'), (bytes, memoryview))
          and row.get('raw_bytes') is None and row.get('raw_sha256') is None
          and row.get('payload_bytes') is None)
    try:
        body = unpack_json({'payload_gzip': bytes(row['page_bytes']),
                           'payload_sha256': header.get('sha256'),
                           'uncompressed_bytes': header.get('uncompressed_bytes')}, max_bytes=MAX_BYTES)
        check(isinstance(body, dict) and set(body) == {'records'})
        records = body['records']
        packed = {'payload_sha256': hashlib.sha256(bytes(row['page_bytes'])).hexdigest(),
                  'uncompressed_bytes': len(canonical_json(body))}
        expected = _header(records, packed)
        check(canonical_json(header) == canonical_json(expected))
        key = page_key(expected['record_index'])
        check(row.get('entity_key') == key and row.get('analysis_id') is not None
              and row.get('record_id') == uuid.uuid5(uuid.UUID(str(row['analysis_id'])), KIND + ':' + key)
              and row.get('review_unit_id') is not None and row.get('parent_id') is not None
              and row['parent_id'] == uuid.uuid5(uuid.UUID(str(row['analysis_id'])), 'UNIT:' + row['review_unit_id'])
              and row.get('status') == 'RECORDED' and row.get('state') == {} and row.get('version') == 0
              and row.get('updated_at') == row.get('created_at'))
        result = []
        for item in records:
            payload = item['payload']
            check(payload.get('review_unit_id') == row['review_unit_id'])
            raw = item['raw'].encode('utf-8')
            result.append({'entity_key': payload['record_id'], 'payload': payload, 'raw_bytes': raw,
                           'raw_sha256': hashlib.sha256(raw).hexdigest()})
        return result
    except (ReviewError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise ReviewError('EVIDENCE_PAGE_CORRUPT', 'Cannot restore complete source evidence page') from exc


class EvidencePageStorage:
    """Detect the complete, explicit 012 schema; partial installs fail closed."""
    def __init__(self, db):
        check(db.side == 'vwp')
        row = db.conn.execute(
            "SELECT a.atttypid='bytea'::regtype AS bytes_type,a.attstorage "
            "FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s "
            "AND t.relname='sanity_vwp_data' AND a.attname='page_bytes' AND a.attnum>0 AND NOT a.attisdropped",
            (db.schema,)).fetchone()
        names = db.conn.execute(
            "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s "
            "AND t.relname='sanity_vwp_data' AND c.contype='c' AND c.convalidated AND NOT c.connoinherit "
            "AND c.conname IN ('sanity_vwp_kind_v5','sanity_vwp_status_v5',"
            "'sanity_vwp_source_page_v1','sanity_vwp_payload_storage_v1')", (db.schema,)).fetchall()
        actual = {r['conname'] for r in names}
        required = {'sanity_vwp_kind_v5', 'sanity_vwp_status_v5',
                    'sanity_vwp_source_page_v1', 'sanity_vwp_payload_storage_v1'}
        self.enabled = row is not None
        if row is None and not (actual - {'sanity_vwp_payload_storage_v1'}):
            return
        index = db.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_class x ON x.oid=i.indexrelid "
            "WHERE n.nspname=%s AND t.relname='sanity_vwp_data' "
            "AND x.relname='sanity_vwp_source_page_start_uq' AND i.indisunique AND i.indisvalid "
            "AND i.indisready AND i.indislive AND i.indimmediate AND i.indnkeyatts=2 AND i.indnatts=2 "
            "AND pg_get_indexdef(i.indexrelid,1,false)='analysis_id' "
            "AND pg_get_indexdef(i.indexrelid,2,false)=%s AND pg_get_expr(i.indpred,i.indrelid)=%s) AS valid",
            (db.schema, "(((payload ->> 'record_index'::text))::bigint)",
             "((record_type)::text = 'SOURCE_EVIDENCE_PAGE'::text)")).fetchone()
        if row is None or not (row['bytes_type'] and row['attstorage'] == 'e'
                               and actual == required and index['valid']):
            raise ReviewError('EVIDENCE_PAGE_SCHEMA_REQUIRED', 'DBA migration 012 must be complete')

    @property
    def selection(self):
        return ',page_bytes' if self.enabled else ''
