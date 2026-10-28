"""Lossless private VWP payload storage; the source/wire JSON contract is unchanged.

Migration 011 opts in. Older schemas and plaintext rows remain readable; a
partially installed or corrupt compressed representation fails closed. Only the
V2 source producer uses this representation, never a different privacy boundary.
"""
from __future__ import annotations

import hashlib
from common.sanity_common import ReviewError, canonical_json, pack_json, unpack_json

MARKER = '__vwp_payload__'
CODEC = 'gzip-json-v1'
EVIDENCE_CODEC = 'gzip-evidence-v1'
MAX_BYTES = 64 * 1024 * 1024
INDEXED = {
    'EVIDENCE': ('record_id', 'record_index', 'check_status', 'finding_group_id'),
    'SOURCE_OUTBOX': ('submission_id', 'position', 'kind', 'message_id'),
}


def _check(ok):
    if not ok:
        raise ReviewError('STORED_PAYLOAD_CORRUPT', 'Private payload representation failed validation')


def _header(kind, value, metadata):
    _check(kind in INDEXED and isinstance(value, dict) and MARKER not in value)
    _check(all(key in value for key in INDEXED[kind]))
    result = {key: value[key] for key in INDEXED[kind]}
    if metadata['codec'] == EVIDENCE_CODEC:
        _check(kind == 'EVIDENCE' and isinstance(value.get('record_id'), str))
        result['local_evidence_ref'] = 'db-gzip-v1:' + value['record_id']
    if kind == 'SOURCE_OUTBOX':
        packet = value.get('packet')
        _check(isinstance(packet, dict) and 'collection_id' in packet)
        result['packet'] = {'collection_id': packet['collection_id']}
    result[MARKER] = metadata
    return result


def encode_payload(kind, value):
    """Keep small/incompressible payloads plain; retain exact canonical JSON otherwise."""
    _check(kind in INDEXED and isinstance(value, dict) and MARKER not in value)
    raw = canonical_json(value)
    if len(raw) < 512:
        return value, None
    packed = pack_json(value, max_bytes=MAX_BYTES)
    metadata = {'codec': CODEC, 'sha256': packed['payload_sha256'],
                'uncompressed_bytes': packed['uncompressed_bytes']}
    header = _header(kind, value, metadata)
    if len(canonical_json(header)) + len(packed['payload_gzip']) + 64 >= len(raw):
        return value, None
    return header, packed['payload_gzip']


def encode_evidence(value, raw):
    """Preserve the exact normalized evidence bytes inside the compressed DB body."""
    if raw is None:
        return value, raw, None
    _check(isinstance(raw, bytes))
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        return value, raw, None
    body = {'payload': value, 'raw': text}
    expanded = canonical_json(body)
    if not 512 <= len(expanded) <= MAX_BYTES:
        return value, raw, None
    packed = pack_json(body, max_bytes=MAX_BYTES)
    metadata = {'codec': EVIDENCE_CODEC, 'sha256': packed['payload_sha256'],
                'uncompressed_bytes': packed['uncompressed_bytes']}
    header = _header('EVIDENCE', value, metadata)
    if len(canonical_json(header)) + len(packed['payload_gzip']) + 64 >= len(canonical_json(value)) + len(raw):
        return value, raw, None
    return header, None, packed['payload_gzip']


def _restore(kind, header, payload_bytes=None):
    """Verify compressed bytes, bounded expansion, and every indexed header field."""
    _check(isinstance(header, dict))
    if payload_bytes is None:
        _check(MARKER not in header)
        return header, None
    _check(kind in INDEXED and isinstance(payload_bytes, (bytes, memoryview)))
    metadata = header.get(MARKER)
    _check(isinstance(metadata, dict) and set(metadata) == {'codec', 'sha256', 'uncompressed_bytes'}
           and metadata['codec'] in (CODEC, EVIDENCE_CODEC))
    try:
        value = unpack_json({'payload_gzip': bytes(payload_bytes),
            'payload_sha256': metadata['sha256'], 'uncompressed_bytes': metadata['uncompressed_bytes']},
            max_bytes=MAX_BYTES)
    except ReviewError as exc:
        raise ReviewError('STORED_PAYLOAD_CORRUPT', 'Cannot restore complete private payload') from exc
    raw = None
    if metadata['codec'] == EVIDENCE_CODEC:
        _check(kind == 'EVIDENCE' and isinstance(value, dict) and set(value) == {'payload', 'raw'}
               and isinstance(value['raw'], str))
        try:
            raw = value['raw'].encode('utf-8')
        except UnicodeError as exc:
            raise ReviewError('STORED_PAYLOAD_CORRUPT', 'Invalid stored evidence encoding') from exc
        value = value['payload']
    _check(canonical_json(header) == canonical_json(_header(kind, value, metadata)))
    return value, raw


def decode_payload(kind, header, payload_bytes=None):
    return _restore(kind, header, payload_bytes)[0]


def restore_row(kind, row):
    payload, raw = _restore(kind, row['payload'], row.get('payload_bytes'))
    result = {**row, 'payload': payload}
    if raw is not None:
        _check(kind == 'EVIDENCE' and row['raw_bytes'] is None
               and row['entity_key'] == payload['record_id']
               and hashlib.sha256(raw).hexdigest() == row['raw_sha256'])
        result['raw_bytes'] = raw
    return result


class PrivatePayloadStorage:
    """Detect the explicit, validated 011 contract once per store/analysis."""
    def __init__(self, db):
        _check(db.side == 'vwp')
        row = db.conn.execute(
            "SELECT a.atttypid='bytea'::regtype AS bytes_type,a.attstorage,"
            "EXISTS(SELECT 1 FROM pg_constraint c WHERE c.conrelid=t.oid "
            "AND c.conname='sanity_vwp_payload_storage_v1' AND c.contype='c' "
            "AND c.convalidated AND NOT c.connoinherit) AS validated "
            "FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND t.relname='sanity_vwp_data' "
            "AND a.attname='payload_bytes' AND a.attnum>0 AND NOT a.attisdropped", (db.schema,)).fetchone()
        self.enabled = row is not None
        if row is not None and not (row['bytes_type'] and row['attstorage'] == 'e' and row['validated']):
            raise ReviewError('PAYLOAD_STORAGE_SCHEMA_REQUIRED', 'DBA migration 011 must be complete')

    @property
    def selection(self):
        return ',payload_bytes' if self.enabled else ''

    def restore(self, kind, row):
        return restore_row(kind, row)
