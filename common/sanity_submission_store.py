"""Private VWP submission controls and immutable source/outbox records."""
from common.sanity_common import canonical_json, digest
from common.sanity_db import json_value, stable_uuid
from common.sanity_submission import check
from common.sanity_payload_storage import PrivatePayloadStorage, encode_payload
from common.sanity_shared_sources import SharedSourcePackets


def require_schema(db):
    expected = {
        'sanity_vwp_data': {'sanity_vwp_submission_parent_v3', 'sanity_vwp_submission_parent_v4'},
        'sanity_oa_data': {'sanity_oa_workspace_scope_v6', 'sanity_oa_workspace_scope_v7'},
        'sanity_vwp_to_oa': {'sanity_vwp_message_scope_v3'},
        'sanity_oa_to_vwp': {'sanity_oa_message_scope_v3'},
    }
    # Migration 009 replaces the OA scope constraint with v7 to include strict
    # SSO identities; its submission constraints remain in force. Match each
    # validated constraint to its own table, not an aggregate name count.
    rows = db.conn.execute("SELECT t.relname,c.conname FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND c.convalidated "
        "AND c.conname=ANY(%s)", (db.schema, sorted(set().union(*expected.values())))).fetchall()
    present = {(row['relname'], row['conname']) if isinstance(row, dict) else tuple(row) for row in rows}
    check(all(any((table, name) in present for name in names) for table, names in expected.items()),
          "SUBMISSION_SCHEMA_REQUIRED", "DBA migration 008 or compatible 009 is required")


class SubmissionStore:
    def __init__(self, db):
        check(db.side == "vwp", "FORBIDDEN")
        self.db = db
        require_schema(db)
        self.payload_storage = PrivatePayloadStorage(db)
        self.shared = SharedSourcePackets(db)

    def get(self, kind, key, *, lock=False):
        row = self.db.execute("SELECT * FROM {vwp} WHERE record_type=%s AND entity_key=%s"
                              + (" FOR UPDATE" if lock else ""), (kind, key)).fetchone()
        if row is None:
            return None
        row = self.payload_storage.restore(kind, row)
        return self.shared.restore_outbox(row) if kind == 'SOURCE_OUTBOX' else row

    def immutable(self, kind, key, payload):
        check(kind in ('SOURCE_RUN','SOURCE_OUTBOX'))
        if kind == 'SOURCE_OUTBOX' and payload.get('kind') == 'SOURCE_PART' and self.shared.enabled:
            return self.shared_outbox(key, payload)
        rid = stable_uuid("submission-v2", kind, key)
        stored, packed = payload, None
        if kind == 'SOURCE_OUTBOX' and self.payload_storage.enabled:
            stored, packed = encode_payload(kind, payload)
        extra = self.payload_storage.selection
        values = (rid, kind, key, json_value(stored)) + ((packed,) if extra else ())
        self.db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,status" + extra + ") "
                        "VALUES (%s,%s,NULL,%s,%s,'RECORDED'" + (",%s" if extra else '') + ") ON CONFLICT DO NOTHING", values)
        existing = self.get(kind, key)
        check(existing and existing['record_id'] == rid and existing['payload'] == payload,
              "SOURCE_IDENTITY_CONFLICT" if kind == 'SOURCE_RUN' else "SUBMISSION_CONFLICT")
        return existing

    def shared_outbox(self, key, value):
        with self.db.transaction():
            existing = self.get('SOURCE_OUTBOX', key)
            if existing is not None:
                check(canonical_json(existing['payload']) == canonical_json(value), 'SUBMISSION_CONFLICT')
                return existing
            header, reference = self.shared.stage(value)
            rid = stable_uuid('submission-v2', 'SOURCE_OUTBOX', key)
            self.db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,status,source_packet_ref) "
                "VALUES (%s,'SOURCE_OUTBOX',NULL,%s,%s,'RECORDED',%s) ON CONFLICT DO NOTHING",
                (rid, key, json_value(header), reference))
            saved = self.get('SOURCE_OUTBOX', key)
            check(saved and saved['record_id'] == rid and canonical_json(saved['payload']) == canonical_json(value),
                  'SUBMISSION_CONFLICT')
            return saved

    def accept(self, submission, *, node_id):
        sid = submission['submission_id']
        payload = {**submission, 'node_id': node_id}
        self.db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,status) "
                        "VALUES (%s,'SUBMISSION',NULL,%s,%s,'VWP_ACCEPTED') ON CONFLICT DO NOTHING",
                        (stable_uuid('submission-v2','SUBMISSION',sid), sid, json_value(payload)))
        row = self.get('SUBMISSION',sid,lock=True)
        check(row and row['payload'] == payload, 'SUBMISSION_CONFLICT')
        return row

    def state(self, sid, status, state):
        row = self.get('SUBMISSION',sid,lock=True)
        check(row, 'NOT_FOUND')
        if row['status'] in ('OA_STORED','REJECTED'):
            check(row['status'] == status and row['state'] == state, 'SUBMISSION_STATE_CONFLICT')
            return row
        if row['status'] == status and row['state'] == state:
            return row
        self.db.execute("UPDATE {vwp} SET state=%s,status=%s,version=version+1,updated_at=clock_timestamp() "
                        "WHERE record_id=%s", (json_value(state),status,row['record_id']))
        return self.get('SUBMISSION',sid)

    def outbox(self, sid):
        selection = '*' if self.shared.enabled else 'payload' + self.payload_storage.selection
        rows = self.db.execute("SELECT " + selection + " FROM {vwp} WHERE record_type='SOURCE_OUTBOX' "
            "AND payload->>'submission_id'=%s ORDER BY (payload->>'position')::bigint",(sid,)).fetchall()
        restored = [self.payload_storage.restore('SOURCE_OUTBOX', row) for row in rows]
        if self.shared.enabled:
            restored = [self.shared.restore_outbox(row) for row in restored]
        return [{'payload': row['payload']} for row in restored]

    def put_outbox(self, sid, position, kind, packet):
        from common.sanity_submission import message_id
        mid = message_id(kind,packet)
        value = {'submission_id':sid,'position':position,'kind':kind,'packet':packet,'message_id':mid}
        self.immutable('SOURCE_OUTBOX',sid+'.'+str(position),value)
        return mid
