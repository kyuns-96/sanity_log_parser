"""Private, immutable raw report chunks in the existing VWP PostgreSQL table.

No whole-file bytea, new table, DELETE, local archive or implicit DDL. Files are
visible only after their full hash is checked; a submission is published only
after every manifest file has been verified. Unreferenced chunks are harmless
and reusable after an interrupted request, never accepted as a finished file.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from common.sanity_common import canonical_json, digest
from common.sanity_db import Database, json_value, lock_key, stable_uuid
from common.sanity_submission import HEX, check, submission_receipt, submission_uuid
from common.sanity_submission_files import (directory, validate_submission, verify_bundle, write_json_file)
from common.sanity_submission_https import validate_https_config
from common.sanity_submission_intake import PENDING_BLOCKER, unverified

RAW_PROTOCOL = 'raw-source-v1'
CHUNK_BYTES = 1024 * 1024
KINDS = ('RAW_SUBMISSION','RAW_FILE','RAW_CHUNK')


def require_raw_schema(db):
    check(db.side == 'vwp','FORBIDDEN')
    base = {'sanity_vwp_submission_parent_v4','sanity_vwp_raw_scope_v4',
            'sanity_vwp_raw_payload_v4','sanity_vwp_raw_immutable_v4'}
    old = base | {'sanity_vwp_kind_v4','sanity_vwp_status_v4'}
    paged = base | {'sanity_vwp_kind_v5','sanity_vwp_status_v5',
                    'sanity_vwp_payload_storage_v1','sanity_vwp_source_page_v1'}
    names = old | paged
    rows = db.conn.execute("SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname='sanity_vwp_data' "
        "AND c.contype='c' AND c.convalidated AND NOT c.connoinherit AND c.conname=ANY(%s)",
        (db.schema,sorted(names))).fetchall()
    actual = {r['conname'] for r in rows}
    check(old <= actual or paged <= actual,'RAW_SOURCE_SCHEMA_REQUIRED','DBA migration 010 is required')
    from common.sanity_evidence_pages import EvidencePageStorage
    pages = EvidencePageStorage(db)
    check(not pages.enabled or paged <= actual, 'RAW_SOURCE_SCHEMA_REQUIRED')
    rows = db.conn.execute("SELECT i.indisunique,i.indisvalid,i.indisready,i.indislive,i.indimmediate,"
        "pg_get_expr(i.indpred,i.indrelid) AS predicate,"
        "ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(attnum,position) "
        "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.position) AS columns "
        "FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname=%s AND t.relname='sanity_vwp_data' AND i.indexprs IS NULL",(db.schema,)).fetchall()
    predicate = "((record_type)::text = ANY ((ARRAY['RAW_SUBMISSION'::character varying, 'RAW_FILE'::character varying, 'RAW_CHUNK'::character varying])::text[]))"
    check(any(all(r[k] for k in ('indisunique','indisvalid','indisready','indislive','indimmediate'))
              and tuple(r['columns']) == ('record_type','entity_key') and r['predicate'] == predicate for r in rows),
          'RAW_SOURCE_SCHEMA_REQUIRED')


def raw_id(kind, key):
    return stable_uuid(RAW_PROTOCOL,kind,key)


class RawSourceStore:
    def __init__(self, db, node_id, *, temp_root=None):
        require_raw_schema(db)
        self.db, self.node_id, self.temp_root = db,node_id,temp_root

    def get(self, kind, key):
        row = self.db.execute('SELECT * FROM {vwp} WHERE record_type=%s AND entity_key=%s',(kind,key)).fetchone()
        if row:
            check(row['record_id'] == raw_id(kind,key) and row['analysis_id'] is None and row['parent_id'] is None
                  and row['review_unit_id'] is None,'RAW_SOURCE_CORRUPT')
            if kind != 'RAW_SUBMISSION':
                check(row['state'] == {} and row['version'] == 0 and row['updated_at'] == row['created_at'],
                      'RAW_SOURCE_CORRUPT')
        return row

    def immutable(self, kind, key, payload, raw=None):
        check(kind in ('RAW_FILE','RAW_CHUNK'))
        sha = hashlib.sha256(raw).hexdigest() if raw is not None else None
        self.db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,status,raw_bytes,raw_sha256) "
            "VALUES (%s,%s,NULL,%s,%s,'RECORDED',%s,%s) ON CONFLICT DO NOTHING",
            (raw_id(kind,key),kind,key,json_value(payload),raw,sha))
        row = self.get(kind,key)
        check(row and row['payload'] == payload and row['raw_sha256'] == sha
              and (bytes(row['raw_bytes']) if row['raw_bytes'] is not None else None) == raw,'SUBMISSION_CONFLICT')
        return row

    def load(self, sid, actor=None):
        sid = submission_uuid(sid)
        row = self.get('RAW_SUBMISSION','raws.'+sid)
        check(row,'NOT_FOUND')
        payload = row['payload']
        check(set(payload) == {'schema_version','protocol','node_id','request'} and payload['schema_version'] == 1
              and payload['protocol'] == RAW_PROTOCOL and payload['node_id'] == self.node_id,'RAW_SOURCE_CORRUPT')
        value = payload['request']
        check(isinstance(value,dict) and set(value) == {'submission','manifest'},'RAW_SOURCE_CORRUPT')
        validate_submission(value['submission'],value['manifest'])
        check(value['submission']['submission_id'] == sid,'RAW_SOURCE_CORRUPT')
        if actor is not None:
            check(value['submission']['actor'] == actor.actor,'NOT_FOUND')
        return value

    def initialize(self, sid, actor, value):
        sid = submission_uuid(sid)
        check(isinstance(value,dict) and set(value) == {'submission','manifest'})
        validate_submission(value['submission'],value['manifest'])
        check(value['submission']['submission_id'] == sid and value['submission']['actor'] == actor.actor,'FORBIDDEN')
        payload = {'schema_version':1,'protocol':RAW_PROTOCOL,'node_id':self.node_id,'request':value}
        key = 'raws.'+sid
        self.db.execute("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,status) "
            "VALUES (%s,'RAW_SUBMISSION',NULL,%s,%s,'UPLOADING') ON CONFLICT DO NOTHING",
            (raw_id('RAW_SUBMISSION',key),key,json_value(payload)))
        check(self.load(sid,actor) == value,'SUBMISSION_CONFLICT')
        return self.status(sid,actor)

    def file(self, sid, actor, index):
        value = self.load(sid,actor)
        files = value['manifest']['files']
        check(type(index) is int and 0 <= index < len(files),'NOT_FOUND')
        return None,files[index],self.get('RAW_SUBMISSION','raws.'+sid)['status'] == 'VWP_UPLOADED'

    @contextmanager
    def receiving(self, sid, actor, index):
        _,record,committed = self.file(sid,actor,index)
        check(not committed,'SUBMISSION_CONFLICT')
        writer = RawWriter(self,sid,index,record)
        try:
            yield writer,record
            writer.finish()
        finally:
            writer.buffer.clear()

    def file_record(self, sid, index, expected):
        key = f'rawf.{sid}.{index:05d}'
        row = self.get('RAW_FILE',key)
        check(row is not None,'RAW_SOURCE_INCOMPLETE')
        payload = row['payload']
        check(set(payload) == {'schema_version','protocol','submission_id','file_index','file','chunks'}
              and payload['schema_version'] == 1 and payload['protocol'] == RAW_PROTOCOL
              and payload['submission_id'] == sid and payload['file_index'] == index and payload['file'] == expected
              and isinstance(payload['chunks'],list),'RAW_SOURCE_CORRUPT')
        expected_parts = (expected['size_bytes']+CHUNK_BYTES-1)//CHUNK_BYTES
        check(len(payload['chunks']) == expected_parts,'RAW_SOURCE_CORRUPT')
        check(all(isinstance(sha,str) and HEX.fullmatch(sha) for sha in payload['chunks']),'RAW_SOURCE_CORRUPT')
        return payload

    def iter_file(self, sid, index, expected):
        payload = self.file_record(sid,index,expected)
        total, whole = 0,hashlib.sha256()
        for position,sha in enumerate(payload['chunks']):
            row = self.get('RAW_CHUNK','rawc.'+sha)
            check(row is not None and row['raw_bytes'] is not None,'RAW_SOURCE_CORRUPT')
            raw = bytes(row['raw_bytes'])
            length = min(CHUNK_BYTES,expected['size_bytes']-total)
            check(row['payload'] == {'schema_version':1,'protocol':RAW_PROTOCOL,'sha256':sha,'size_bytes':length}
                  and row['raw_sha256'] == sha and len(raw) == length and hashlib.sha256(raw).hexdigest() == sha,
                  'RAW_SOURCE_CORRUPT')
            total += len(raw)
            whole.update(raw)
            yield raw
        check(total == expected['size_bytes'] and whole.hexdigest() == expected['sha256'],'FILE_INTEGRITY')

    def commit(self, sid, actor):
        value = self.load(sid,actor)
        for index,record in enumerate(value['manifest']['files']):
            for _ in self.iter_file(sid,index,record):
                pass
        self.db.execute("UPDATE {vwp} SET status='VWP_UPLOADED',version=version+1,updated_at=clock_timestamp() "
            "WHERE record_id=%s AND status='UPLOADING'",(raw_id('RAW_SUBMISSION','raws.'+sid),))
        return self.status(sid,actor)

    def status(self, sid, actor):
        value = self.load(sid,actor)
        row = self.get('RAW_SUBMISSION','raws.'+sid)
        check(row['status'] in ('UPLOADING','VWP_UPLOADED') and row['state'] == {},'RAW_SOURCE_CORRUPT')
        files = self.db.execute("SELECT entity_key,payload FROM {vwp} WHERE record_type='RAW_FILE' "
            "AND payload->>'submission_id'=%s ORDER BY (payload->>'file_index')::int",(sid,)).fetchall()
        received = []
        for stored in files:
            index = stored['payload'].get('file_index')
            check(type(index) is int and 0 <= index < len(value['manifest']['files']),'RAW_SOURCE_CORRUPT')
            self.file_record(sid,index,value['manifest']['files'][index])
            received.append(index)
        check(len(set(received)) == len(received),'RAW_SOURCE_CORRUPT')
        if row['status'] == 'VWP_UPLOADED':
            check(len(received) == len(value['manifest']['files']),'RAW_SOURCE_CORRUPT')
        result = {'schema_version':1,'submission_id':sid,'actor_key':actor.key,'delivery_state':row['status'],
                  'verified_oa_state':False,'review_readiness':'BLOCKED',
                  'blockers':[PENDING_BLOCKER] if unverified(value['submission']['actor']) else [],'workspace_links':[]}
        saved = self.db.execute("SELECT * FROM {vwp} WHERE record_type='SUBMISSION' AND entity_key=%s",(sid,)).fetchone()
        if saved:
            check(saved['payload'] == {**value['submission'],'node_id':self.node_id},'RAW_SOURCE_CORRUPT')
            if row['status'] == 'VWP_UPLOADED':
                result.update(submission_receipt(saved))
        result.update(content_sha256=value['submission']['content_sha256'],received_files=received,
                      total_files=len(value['manifest']['files']))
        return result

    @contextmanager
    def materialize(self, sid):
        value = self.load(sid)
        check(self.get('RAW_SUBMISSION','raws.'+sid)['status'] == 'VWP_UPLOADED','RAW_SOURCE_INCOMPLETE')
        parent = directory(self.temp_root or tempfile.gettempdir())
        total = sum(r['size_bytes'] for r in value['manifest']['files'])
        check(shutil.disk_usage(parent).free >= total+4*1024*1024,'SUBMISSION_TEMP_LIMIT')
        with tempfile.TemporaryDirectory(prefix='sanity-raw-',dir=parent) as temporary:
            bundle = Path(temporary)
            (bundle/'snapshot/reports').mkdir(parents=True,mode=0o700)
            write_json_file(bundle/'submission.json',value['submission'])
            write_json_file(bundle/'snapshot/collection.json',value['manifest'])
            for index,record in enumerate(value['manifest']['files']):
                path = bundle/'snapshot'/record['path']
                path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
                with path.open('xb') as output:
                    for chunk in self.iter_file(sid,index,record):
                        output.write(chunk)
            verify_bundle(bundle)
            yield bundle


class RawWriter:
    def __init__(self, store, sid, index, expected):
        self.store,self.sid,self.index,self.expected = store,sid,index,expected
        self.buffer,self.parts,self.whole,self.total = bytearray(),[],hashlib.sha256(),0

    def write(self, data):
        check(self.total+len(data) <= self.expected['size_bytes'],'FILE_INTEGRITY')
        self.total += len(data)
        self.whole.update(data)
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            count = min(CHUNK_BYTES-len(self.buffer),len(view)-offset)
            self.buffer.extend(view[offset:offset+count])
            offset += count
            if len(self.buffer) == CHUNK_BYTES:
                self.flush()
        return len(data)

    def flush(self):
        if self.buffer:
            raw = bytes(self.buffer)
            sha = hashlib.sha256(raw).hexdigest()
            self.store.immutable('RAW_CHUNK','rawc.'+sha,
                {'schema_version':1,'protocol':RAW_PROTOCOL,'sha256':sha,'size_bytes':len(raw)},raw)
            self.parts.append(sha)
            self.buffer.clear()

    def finish(self):
        check(self.total == self.expected['size_bytes'] and self.whole.hexdigest() == self.expected['sha256'],
              'FILE_INTEGRITY')
        self.flush()
        payload = {'schema_version':1,'protocol':RAW_PROTOCOL,'submission_id':self.sid,'file_index':self.index,
                   'file':self.expected,'chunks':self.parts}
        self.store.immutable('RAW_FILE',f'rawf.{self.sid}.{self.index:05d}',payload)


class DatabaseUploadStore:
    """Factory for per-request connections; never hold a transaction over HTTP I/O."""
    def __init__(self, config, database_factory=None):
        self.config = validate_https_config(config,bridge=True)
        check(config.get('storage') == 'postgresql' and os.geteuid() == config['service_uid'],'SUBMISSION_CONFIG')
        self.connect = database_factory or (lambda:Database('vwp'))
        with self.connect() as db:
            require_raw_schema(db)

    @contextmanager
    def session(self, sid):
        sid = submission_uuid(sid)
        with self.connect() as db:
            token = lock_key(RAW_PROTOCOL+':'+sid)
            check(db.conn.execute('SELECT pg_try_advisory_lock(%s) AS locked',(token,)).fetchone()['locked'],
                  'SUBMISSION_BUSY')
            try:
                yield RawSourceStore(db,self.config['node_id'],temp_root=self.config['temp_root'])
            finally:
                db.conn.execute('SELECT pg_advisory_unlock(%s)',(token,))
