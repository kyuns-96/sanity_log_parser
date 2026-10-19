"""Private VWP submission controls and immutable source/outbox records."""
from common.sanity_common import digest
from common.sanity_db import json_value, stable_uuid
from common.sanity_submission import check


def require_schema(db):
    value = db.conn.execute("SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND c.convalidated "
        "AND c.conname=ANY(%s)", (db.schema, ['sanity_vwp_submission_parent_v3','sanity_oa_workspace_scope_v6',
                                          'sanity_vwp_message_scope_v3','sanity_oa_message_scope_v3'])).fetchone()
    count = next(iter(value.values())) if isinstance(value, dict) else value[0]
    check(count == 4, "SUBMISSION_SCHEMA_REQUIRED", "DBA migration 008 is required")


class SubmissionStore:
    def __init__(self, db):
        check(db.side == "vwp", "FORBIDDEN")
        self.db = db
        require_schema(db)

    def get(self, kind, key, *, lock=False):
        return self.db.execute("SELECT * FROM {vwp} WHERE record_type=%s AND entity_key=%s"
                               + (" FOR UPDATE" if lock else ""), (kind, key)).fetchone()

    def immutable(self, kind, key, payload):
        check(kind in ('SOURCE_RUN','SOURCE_OUTBOX'))
        rid = stable_uuid("submission-v2", kind, key)
        self.db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,status) "
                        "VALUES (%s,%s,NULL,%s,%s,'RECORDED') ON CONFLICT DO NOTHING",
                        (rid, kind, key, json_value(payload)))
        existing = self.get(kind, key)
        check(existing and existing['record_id'] == rid and existing['payload'] == payload,
              "SOURCE_IDENTITY_CONFLICT" if kind == 'SOURCE_RUN' else "SUBMISSION_CONFLICT")
        return existing

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
        return self.db.execute("SELECT payload FROM {vwp} WHERE record_type='SOURCE_OUTBOX' "
            "AND payload->>'submission_id'=%s ORDER BY (payload->>'position')::bigint",(sid,)).fetchall()

    def put_outbox(self, sid, position, kind, packet):
        from common.sanity_submission import message_id
        mid = message_id(kind,packet)
        value = {'submission_id':sid,'position':position,'kind':kind,'packet':packet,'message_id':mid}
        self.immutable('SOURCE_OUTBOX',sid+'.'+str(position),value)
        return mid
