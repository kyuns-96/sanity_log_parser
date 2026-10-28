"""013: one immutable masked source packet, private references and commit gating.

All reads use the already shared VWP-to-OA queue, never the other private table.
The queue body is retained after acknowledgement; private FKs prevent its purge.
"""
from common.sanity_common import ReviewError, canonical_json, digest, pack_json, unpack_json
from common.sanity_db import Queue, stable_uuid
from common.sanity_privacy import SecretGuard
from common.sanity_submission import PART_BYTES, message_id, validate_packet

MARKER = '__source_packet__'
GATE = '__source_commit_gate__'
HEADER = ('schema_version', 'protocol', 'submission_id', 'content_sha256', 'collection_id')


def check(ok):
    if not ok:
        raise ReviewError('SHARED_SOURCE_CORRUPT', 'Immutable source packet reference failed validation')


def part_identity(body):
    if body.get('kind') == 'preserved-text-v1':
        from common.sanity_preserved_text import validate_part
        validate_part(body)
        return body['source_id'], len(body['segments'])
    return body['report_id'], len(body['records'])


class SharedSourcePackets:
    def __init__(self, db):
        self.db = db
        columns = db.conn.execute(
            "SELECT t.relname,a.atttypid='varchar'::regtype AND a.atttypmod=132 AND a.attgenerated='' AS valid "
            "FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND t.relname IN ('sanity_vwp_data','sanity_oa_data') "
            "AND a.attname='source_packet_ref' AND a.attnum>0 AND NOT a.attisdropped", (db.schema,)).fetchall()
        constraints = db.conn.execute(
            "SELECT t.relname,c.conname,c.contype,c.confdeltype,c.confupdtype,c.condeferrable,"
            "cardinality(c.conkey) AS key_count,"
            "(SELECT a.attname FROM pg_attribute a WHERE a.attrelid=c.conrelid AND a.attnum=c.conkey[1]) AS key_column,"
            "(SELECT a.attname FROM pg_attribute a WHERE a.attrelid=c.confrelid AND a.attnum=c.confkey[1]) AS ref_column,"
            "c.confrelid="
            "(SELECT q.oid FROM pg_class q JOIN pg_namespace s ON s.oid=q.relnamespace "
            "WHERE s.nspname=%s AND q.relname='sanity_vwp_to_oa') AS shared_fk "
            "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND c.convalidated AND (c.contype='f' OR NOT c.connoinherit) AND c.conname IN "
            "('sanity_vwp_shared_source_v1','sanity_oa_shared_source_v1','sanity_vwp_source_gate_v1',"
            "'sanity_vwp_source_packet_fk','sanity_oa_source_packet_fk')", (db.schema, db.schema)).fetchall()
        self.enabled = bool(columns or constraints)
        if not self.enabled:
            return
        expected = {('sanity_vwp_data', 'sanity_vwp_shared_source_v1', 'c'),
                    ('sanity_oa_data', 'sanity_oa_shared_source_v1', 'c'),
                    ('sanity_vwp_to_oa', 'sanity_vwp_source_gate_v1', 'c'),
                    ('sanity_vwp_data', 'sanity_vwp_source_packet_fk', 'f'),
                    ('sanity_oa_data', 'sanity_oa_source_packet_fk', 'f')}
        index = db.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_class x ON x.oid=i.indexrelid "
            "WHERE n.nspname=%s AND t.relname='sanity_vwp_to_oa' "
            "AND x.relname='sanity_vwp_source_commit_gate_idx' AND i.indisvalid AND i.indisready AND i.indislive "
            "AND i.indnkeyatts=2 AND i.indnatts=2 AND pg_get_indexdef(i.indexrelid,1,false)='correlation_id' "
            "AND pg_get_indexdef(i.indexrelid,2,false)='project' AND pg_get_expr(i.indpred,i.indrelid)=%s) AS valid",
            (db.schema, "((message_type)::text = 'SOURCE_COMMIT'::text)")).fetchone()
        if not ({row['relname'] for row in columns} == {'sanity_vwp_data', 'sanity_oa_data'}
                and all(row['valid'] for row in columns)
                and {(r['relname'], r['conname'], r['contype']) for r in constraints} == expected
                and all(r['shared_fk'] and r['confdeltype'] == 'a' and r['confupdtype'] == 'a'
                        and not r['condeferrable'] and r['key_count'] == 1
                        and r['key_column'] == 'source_packet_ref' and r['ref_column'] == 'message_id'
                        for r in constraints if r['contype'] == 'f') and index['valid']):
            raise ReviewError('SHARED_SOURCE_SCHEMA_REQUIRED', 'DBA migration 013 must be complete')

    def packet(self, key, *, gated=False):
        check(self.enabled)
        row = Queue(self.db, 'vwp_to_oa').get(key)
        check(row is not None and row['message_type'] == 'SOURCE_PART')
        try:
            packet = validate_packet(unpack_json(row, max_bytes=PART_BYTES))
            expected = {name: packet[name] for name in HEADER}
            if row['payload'].get(GATE) is True:
                expected[GATE] = True
            check(not gated or expected.get(GATE) is True)
            check(row['payload'] == expected and row['message_id'] == message_id('SOURCE_PART', packet)
                  and row['correlation_id'] == packet['submission_id'] and row['project'] == packet['scope']['project_key']
                  and row['run_id'] is None and row['bundle_id'] is None)
            SecretGuard().ensure_public(packet)
            return packet
        except (ReviewError, KeyError, TypeError, ValueError) as exc:
            raise ReviewError('SHARED_SOURCE_CORRUPT', 'Cannot restore complete source packet') from exc

    def stage(self, value):
        """Stage a complete immutable part; common claims wait for final COMMIT."""
        check(self.enabled and self.db.side == 'vwp' and value['kind'] == 'SOURCE_PART')
        packet = validate_packet(value['packet'])
        SecretGuard().ensure_public(packet)
        check(value['submission_id'] == packet['submission_id']
              and value['message_id'] == message_id('SOURCE_PART', packet))
        queue = Queue(self.db, 'vwp_to_oa')
        if queue.get(value['message_id']) is None:
            queue.enqueue({'message_id': value['message_id'], 'message_type': 'SOURCE_PART',
                'project': packet['scope']['project_key'], 'run_id': None, 'bundle_id': None,
                'correlation_id': value['submission_id'],
                'payload': {**{name: packet[name] for name in HEADER}, GATE: True},
                **pack_json(packet, max_bytes=PART_BYTES)})
        check(canonical_json(self.packet(value['message_id'], gated=True)) == canonical_json(packet))
        header = {key: value[key] for key in ('submission_id', 'position', 'kind', 'message_id')}
        header.update(packet={'collection_id': packet['collection_id']},
                      **{MARKER: {'packet_sha256': digest(packet)}})
        return header, value['message_id']

    def restore_outbox(self, row):
        if MARKER not in row['payload']:
            check(row.get('source_packet_ref') is None)
            return row
        header, key = row['payload'], row.get('source_packet_ref')
        check(self.enabled and row['record_type'] == 'SOURCE_OUTBOX' and key == header.get('message_id')
              and header.get('kind') == 'SOURCE_PART' and row.get('payload_bytes') is None
              and set(header) == {'submission_id', 'position', 'kind', 'message_id', 'packet', MARKER}
              and type(header.get('position')) is int and header['position'] >= 0
              and row['entity_key'] == header.get('submission_id', '') + '.' + str(header['position'])
              and row['record_id'] == stable_uuid('submission-v2', 'SOURCE_OUTBOX', row['entity_key']))
        packet = self.packet(key, gated=True)
        expected = {name: header[name] for name in ('submission_id', 'position', 'kind', 'message_id')}
        expected.update(packet={'collection_id': packet['collection_id']},
                        **{MARKER: {'packet_sha256': digest(packet)}})
        check(canonical_json(header) == canonical_json(expected)
              and header['submission_id'] == packet['submission_id'])
        return {**row, 'payload': {**{name: header[name] for name in
                ('submission_id', 'position', 'kind', 'message_id')}, 'packet': packet}}

    def part_header(self, packet):
        check(self.enabled and self.db.side == 'oa')
        key = message_id('SOURCE_PART', packet)
        check(canonical_json(self.packet(key)) == canonical_json(packet))
        body = packet['body']['body']
        owner, count = part_identity(body)
        return {'report_id': owner, 'part_index': body['part_index'],
                MARKER: {'message_id': key, 'packet_sha256': digest(packet),
                         'body_sha256': digest(body), 'record_count': count}}, key

    def restore_part(self, row):
        if MARKER not in row['payload']:
            check(row.get('source_packet_ref') is None)
            return row
        header, key = row['payload'], row.get('source_packet_ref')
        check(self.enabled and row['record_type'] == 'WS_SOURCE_PART'
              and isinstance(header.get(MARKER), dict) and key == header[MARKER].get('message_id'))
        packet = self.packet(key)
        value = packet['body']
        body = value['body']
        owner, count = part_identity(body)
        expected = {'report_id': owner, 'part_index': body['part_index'],
                    MARKER: {'message_id': key, 'packet_sha256': digest(packet),
                             'body_sha256': digest(body), 'record_count': count}}
        check(canonical_json(header) == canonical_json(expected) and value['id'] == 'sp-'+digest(body)
              and row['workspace_key'] == value['id']
              and row['workspace_project_ref'] == stable_uuid('workspace-v1', 'WS_PROJECT', packet['scope']['project_key'])
              and row['workspace_block_ref'] == stable_uuid('workspace-v1', 'WS_BLOCK', packet['scope']['block_key'])
              and row['project'] == packet['scope']['project_key'])
        return {**row, 'payload': body}
