"""Bounded OA source restoration cache. Never caches authorization or DB rows.

Every read fetches the current private reference and shared bytes. Only a fully
validated content/policy match can reuse canonical body bytes; callers receive
new JSON trees, so editing a review cannot poison later reads.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import threading
import time

from common.sanity_common import ReviewError, canonical_json, digest, unpack_json
from common.sanity_db import stable_uuid
from common.sanity_privacy import SecretGuard
from common import sanity_privacy as privacy
from common.sanity_shared_sources import GATE, HEADER, MARKER, SharedSourcePackets, part_identity
from common.sanity_submission import PART_BYTES, check, validate_packet

BATCH_SIZE = 64
CACHE_BYTES = 128 * 1024 * 1024
CACHE_ENTRIES = 512
CACHE_SECONDS = 300
CACHE_VERSION = 'oa-source-read-v1'


@dataclass(frozen=True)
class VerifiedBody:
    body_json: bytes
    sha256: str
    records_bytes: int | None
    owner: str
    index: int
    count: int
    packet_sha256: str
    project: str
    block: str

    def decode(self):
        # These immutable bytes were serialized after strict decoding/validation.
        # Return an independent tree, never the cache's mutable object graph.
        return json.loads(self.body_json)


class BodyCache:
    def __init__(self, maximum_bytes=CACHE_BYTES, maximum_entries=CACHE_ENTRIES, ttl=CACHE_SECONDS):
        self.maximum_bytes, self.maximum_entries, self.ttl = maximum_bytes, maximum_entries, ttl
        self.lock = threading.RLock()
        self.values = OrderedDict()
        self.bytes = 0

    def clear(self):
        with self.lock:
            self.values.clear(); self.bytes = 0

    def get(self, key):
        with self.lock:
            found = self.values.get(key)
            if found is None: return None
            at, value = found
            if time.monotonic() - at >= self.ttl:
                self.values.pop(key); self.bytes -= len(value.body_json)
                return None
            self.values.move_to_end(key)
            return value

    def put(self, key, value):
        size = len(value.body_json)
        if size > self.maximum_bytes or self.maximum_entries < 1: return
        with self.lock:
            old = self.values.pop(key, None)
            if old is not None: self.bytes -= len(old[1].body_json)
            self.values[key] = (time.monotonic(), value); self.bytes += size
            while self.bytes > self.maximum_bytes or len(self.values) > self.maximum_entries:
                _, (_, removed) = self.values.popitem(last=False)
                self.bytes -= len(removed.body_json)


CACHE = BodyCache()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _body_bytes(body):
    if set(body) == {'report_id', 'part_index', 'records'}:
        check(isinstance(body['records'], list), 'SHARED_SOURCE_CORRUPT')
        records = canonical_json(body['records'])
        # Exact canonical key order; serialize the large records array only once.
        raw = (b'{"part_index":' + canonical_json(body['part_index']) + b',"records":' + records
               + b',"report_id":' + canonical_json(body['report_id']) + b'}')
        return raw, len(records)
    return canonical_json(body), None


def _packet_bytes(packet, body_json):
    wrapper = b'{"body":' + body_json + b',"id":' + canonical_json(packet['body']['id']) + b'}'
    return b'{' + b','.join(canonical_json(k) + b':' + (wrapper if k == 'body' else canonical_json(packet[k]))
                            for k in sorted(packet)) + b'}'


class SourceReader:
    def __init__(self, db):
        check(db.side == 'oa', 'FORBIDDEN')
        self.db = db
        self.shared = SharedSourcePackets(db)  # Validate current schema once per read, not once per part.
        self.guard = SecretGuard()
        self.policy = digest([CACHE_VERSION, self.guard.known, privacy.MASK,
            [(p.pattern, p.flags) for p in (privacy.SECRET_KEY, privacy.PRIVATE_KEY, *privacy.PATTERNS)]])
        parameters = getattr(db.conn, 'get_dsn_parameters', None)
        # Unknown adapters retain validation but do not share a process cache.
        values = parameters() if parameters is not None else None
        self.namespace = digest([db.side, db.schema, {k: values.get(k) for k in
            ('host', 'hostaddr', 'port', 'dbname', 'user')}]) if values is not None else None

    def _shared_body(self, row):
        try:
            check(row is not None and row['message_type'] == 'SOURCE_PART', 'SHARED_SOURCE_CORRUPT')
            compressed, size = row['payload_gzip'], row['uncompressed_bytes']
            check(isinstance(compressed, bytes) and 0 < len(compressed) <= PART_BYTES
                  and type(size) is int and 0 < size <= PART_BYTES, 'SHARED_SOURCE_CORRUPT')
            actual = _sha(compressed)  # Check actual bytes even if the stored digest was not updated.
            check(actual == row['payload_sha256'], 'SHARED_SOURCE_CORRUPT')
            binding = {k: row[k] for k in ('message_id', 'message_type', 'payload', 'correlation_id',
                                           'project', 'run_id', 'bundle_id', 'uncompressed_bytes')}
            key = (self.namespace, self.policy, actual, digest(binding))
            cached = CACHE.get(key) if self.namespace is not None else None
            if cached is not None:
                return cached
            packet = unpack_json(row, max_bytes=PART_BYTES)
            check(isinstance(packet.get('body'), dict) and set(packet['body']) == {'id', 'body'}
                  and isinstance(packet['body']['body'], dict), 'SHARED_SOURCE_CORRUPT')
            # Same pure envelope checks; complete JSON type/size checks use the
            # canonical bytes below instead of serializing the full packet again.
            validate_packet({**packet, 'body': {}})
            body = packet['body']['body']
            raw, record_bytes = _body_bytes(body)
            encoded = _packet_bytes(packet, raw)
            check(len(encoded) <= PART_BYTES, 'SOURCE_MESSAGE_LIMIT')
            expected = {name: packet[name] for name in HEADER}
            if row['payload'].get(GATE) is True: expected[GATE] = True
            check(row['payload'] == expected and row['message_id'] == 's-'+_sha(b'["SOURCE_PART",'+encoded+b']')
                  and row['correlation_id'] == packet['submission_id']
                  and row['project'] == packet['scope']['project_key']
                  and row['run_id'] is None and row['bundle_id'] is None, 'SHARED_SOURCE_CORRUPT')
            self.guard.ensure_public_json(packet)
            owner, count = part_identity(body)
            check(packet['body']['id'] == 'sp-'+_sha(raw), 'SHARED_SOURCE_CORRUPT')
            result = VerifiedBody(raw, _sha(raw), record_bytes, owner, body['part_index'], count,
                                  _sha(encoded), packet['scope']['project_key'], packet['scope']['block_key'])
            if self.namespace is not None: CACHE.put(key, result)
            return result
        except (ReviewError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ReviewError('SHARED_SOURCE_CORRUPT', 'Cannot restore complete source packet') from exc

    def _restore(self, row, messages, report):
        check(row is not None and row['record_type'] == 'WS_SOURCE_PART'
              and row['workspace_project_ref'] == stable_uuid('workspace-v1', 'WS_PROJECT', report['project_id'])
              and row['workspace_block_ref'] == stable_uuid('workspace-v1', 'WS_BLOCK', report['block_id']), 'STATE_CORRUPT')
        header, key = row['payload'], row.get('source_packet_ref')
        check(isinstance(header, dict), 'STATE_CORRUPT')
        if MARKER not in header:
            check(key is None, 'SHARED_SOURCE_CORRUPT')
            raw, record_bytes = _body_bytes(header)
            owner, count = part_identity(header)
            return VerifiedBody(raw, _sha(raw), record_bytes, owner, header['part_index'], count,
                                '', report['project_id'], report['block_id'])
        check(self.shared.enabled and isinstance(header.get(MARKER), dict)
              and key == header[MARKER].get('message_id'), 'SHARED_SOURCE_CORRUPT')
        value = self._shared_body(messages.get(key))
        expected = {'report_id': value.owner, 'part_index': value.index, MARKER: {
            'message_id': key, 'packet_sha256': value.packet_sha256, 'body_sha256': value.sha256,
            'record_count': value.count}}
        check(canonical_json(header) == canonical_json(expected) and row['workspace_key'] == 'sp-'+value.sha256
              and row['project'] == value.project == report['project_id'] and value.block == report['block_id'],
              'SHARED_SOURCE_CORRUPT')
        return value

    def parts(self, report, refs):
        """Yield validated bodies in manifest order, with bounded batched queries."""
        for start in range(0, len(refs), BATCH_SIZE):
            batch = refs[start:start+BATCH_SIZE]
            keys = [r['id'] for r in batch]
            check(len(keys) == len(set(keys)), 'STATE_CORRUPT')
            rows = self.db.execute("SELECT * FROM {oa} WHERE record_type='WS_SOURCE_PART' AND workspace_key=ANY(%s)",
                                   (keys,)).fetchall()
            by_key = {r['workspace_key']: r for r in rows}
            check(len(by_key) == len(keys), 'STATE_CORRUPT')
            project = stable_uuid('workspace-v1', 'WS_PROJECT', report['project_id'])
            block = stable_uuid('workspace-v1', 'WS_BLOCK', report['block_id'])
            check(all(r['workspace_project_ref'] == project and r['workspace_block_ref'] == block for r in rows),
                  'STATE_CORRUPT')
            shared_keys = list({r['source_packet_ref'] for r in rows if r.get('source_packet_ref') is not None})
            messages = self.db.execute('SELECT * FROM {vwp_to_oa} WHERE message_id=ANY(%s)',
                                       (shared_keys,)).fetchall() if shared_keys else []
            by_message = {r['message_id']: r for r in messages}
            for ref in batch:
                value = self._restore(by_key.get(ref['id']), by_message, report)
                check(value.sha256 == ref['sha256'] and value.count == ref['count'], 'STATE_CORRUPT')
                yield value
