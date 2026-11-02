"""Real PostgreSQL raw-byte preservation, recovery, isolation and HTTPS ingestion."""
from contextlib import contextmanager
import copy
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from unittest.mock import patch

from tests.http_support import TestClient

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database, json_value
from common.sanity_parsers import ParserRegistry
from common.sanity_raw_sources import CHUNK_BYTES, DatabaseUploadStore, RawSourceStore
from common.sanity_submission_files import snapshot, verify_bundle, write_json_file
from common.sanity_submission_store import SubmissionStore
from devtools.sanity_submission_fixture import FixtureIdentity, fixture
from oa.sanity_workspace import seed_workspace
from oa.submission_receiver import SourceReceiver
from oa.test_workspace import fixture as workspace_fixture
from tests.submission_https_support import AsgiTransport, RemoteAuthority, TOKEN, tls_server
from tests.support import PgTestCase, ROOT, psycopg2
from vwp.submission_cli import SubmissionClient
from vwp_bridge.submission_api import create_app
from vwp_bridge.submission_archive import import_bundle
from vwp_bridge.submission_worker import SubmissionWorker


class RawSourcesDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql','009_sso_identity.sql','010_raw_sources.sql'):
            self.admin.execute((ROOT/'db'/name).read_text().replace('public.',self.schema+'.'))
        temporary = tempfile.TemporaryDirectory(prefix='sanity-db-original-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.f = fixture(self.root)
        for name in ('archive','spool'):
            shutil.rmtree(self.root/name)
        (self.root/'scratch').mkdir(mode=0o700)
        self.config = {'schema_version':3,'transport':'https','storage':'postgresql','service_uid':os.geteuid(),
            'node_id':'synthetic-raw-node','allowed_hosts':['testserver','localhost','127.0.0.1'],
            'temp_root':str(self.root/'scratch')}
        self.client_config = {'schema_version':2,'transport':'https','endpoint':'https://testserver',
            'ca_file':None,'timeout_seconds':10,'default_profile':'synthetic-top'}
        self.authority = RemoteAuthority(self.f['authority'])
        self.app = create_app(self.config,self.authority)
        self.http = TestClient(self.app,base_url='https://testserver')
        self.addCleanup(self.http.close)
        self.transport = AsgiTransport(self.client_config,self.http)
        self.client = SubmissionClient(self.client_config,self.f['provider'],self.f['authority'],
            state_root=self.root/'state',transport=self.transport)
        with Database('oa') as db,db.transaction():
            seed_workspace(db,workspace_fixture())

    def submit(self):
        return self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)

    def bundle(self):
        sid = str(uuid.uuid4())
        bundle = self.root/'state'/sid/'bundle'
        bundle.mkdir(parents=True,mode=0o700)
        manifest = snapshot(self.f['prepared'],bundle/'snapshot')
        submission = {'schema_version':2,'submission_id':sid,'actor':self.f['actor'].actor,
                      'content_sha256':digest(manifest),'plan':manifest['plan']}
        write_json_file(bundle/'submission.json',submission)
        return sid,bundle,{'submission':submission,'manifest':manifest}

    @contextmanager
    def store(self, sid):
        with DatabaseUploadStore(self.config).session(sid) as store:
            yield store

    def bridge(self):
        with Database('vwp') as db:
            return SubmissionWorker(db,self.config,self.authority,ParserRegistry()).once()

    def receive(self):
        with Database('oa') as db:
            receiver = SourceReceiver(db,FixtureIdentity())
            while receiver.receive_one()['status'] != 'NO_MESSAGE':
                pass

    def test_real_tls_db_originals_reach_oa_and_receipts_survive_without_server_files(self):
        with tls_server(self.app,self.root) as (endpoint,ca_file),patch.dict(os.environ,{'SANITY_VWP_SUBMIT_TOKEN':TOKEN}):
            client = SubmissionClient({**self.client_config,'endpoint':endpoint,'ca_file':ca_file},
                self.f['provider'],self.f['authority'],state_root=self.root/'state')
            sent = client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
            sid = sent['submission_id']
            self.assertEqual('VWP_UPLOADED',sent['delivery_state'])
            self.assertFalse((self.root/'archive').exists())
            self.assertEqual([],list((self.root/'scratch').iterdir()))
            self.assertEqual(1,self.bridge()['processed'])
            self.assertEqual('DB_QUEUED',client.status(sid)['delivery_state'])
            self.receive(); self.bridge()
            result = client.status(sid)
            self.assertEqual(('OA_STORED',True,'READY'),
                (result['delivery_state'],result['verified_oa_state'],result['review_readiness']))
            self.assertEqual([],list((self.root/'scratch').iterdir()))
            self.assertFalse((self.root/'archive').exists())
            self.assertFalse((self.root/'uploads').exists())
            with self.store(sid) as store,store.materialize(sid) as restored:
                _,manifest = verify_bundle(restored)
                for record in manifest['files']:
                    self.assertEqual(self.f['prepared'].reports[record['path']].read_bytes(),
                                     (restored/'snapshot'/record['path']).read_bytes())
            self.assertFalse(restored.exists())

    def test_binary_bytes_empty_files_and_multiple_parts_are_preserved(self):
        binary = self.root/'input-post/opaque.rpt'
        data = (b'\x00\xff\r\n'+bytes(range(256)))*(CHUNK_BYTES//100)+b'last'
        self.assertGreater(len(data),2*CHUNK_BYTES)
        binary.write_bytes(data)
        empty = self.root/'input-post/empty.rpt'
        empty.write_bytes(b'')
        self.f['prepared'].reports.update({'reports/opaque.rpt':binary,'reports/empty.rpt':empty})
        sent = self.submit()
        sid = sent['submission_id']
        with self.store(sid) as store,store.materialize(sid) as restored:
            self.assertEqual(data,(restored/'snapshot/reports/opaque.rpt').read_bytes())
            self.assertEqual(b'',(restored/'snapshot/reports/empty.rpt').read_bytes())
            manifest = verify_bundle(restored)[1]
            index = next(i for i,f in enumerate(manifest['files']) if f['path']=='reports/opaque.rpt')
            self.assertGreater(len(store.file_record(sid,index,manifest['files'][index])['chunks']),2)
        maximum = self.admin_query("SELECT max(octet_length(raw_bytes)) FROM {vwp} WHERE record_type='RAW_CHUNK'").fetchone()[0]
        self.assertLessEqual(maximum,CHUNK_BYTES)
        self.assertEqual([],list((self.root/'scratch').iterdir()))

    def test_corrupt_upload_chunks_cannot_poison_retry_or_become_a_finished_file(self):
        sid,bundle,value = self.bundle()
        actor = self.f['actor']
        with self.store(sid) as store:
            store.initialize(sid,actor,value)
            record = value['manifest']['files'][0]
            original = (bundle/'snapshot'/record['path']).read_bytes()
            with self.assertRaises(ReviewError):
                with store.receiving(sid,actor,0) as (writer,_):writer.write(b'x'*len(original))
            self.assertEqual([],store.status(sid,actor)['received_files'])
            self.assertEqual('UPLOADING',store.status(sid,actor)['delivery_state'])
        result = self.client.retry(sid)
        self.assertEqual('VWP_UPLOADED',result['delivery_state'])
        with self.store(sid) as store,store.materialize(sid) as restored:
            self.assertEqual(original,(restored/'snapshot'/record['path']).read_bytes())

    def test_interrupted_file_never_appears_complete_and_can_resume_after_api_restart(self):
        path = self.f['prepared'].reports['reports/post/sample.rpt']
        path.write_bytes(b'repeated opaque data\x00'*(CHUNK_BYTES//10))
        sid,bundle,value = self.bundle()
        actor = self.f['actor']
        index = next(i for i,f in enumerate(value['manifest']['files']) if f['path']=='reports/post/sample.rpt')
        with self.store(sid) as store:
            store.initialize(sid,actor,value)
            with self.assertRaises(RuntimeError):
                with store.receiving(sid,actor,index) as (writer,_):
                    writer.write(path.read_bytes()[:CHUNK_BYTES])
                    raise RuntimeError('synthetic interrupted request')
            self.assertEqual([],store.status(sid,actor)['received_files'])
        self.assertEqual(1,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='RAW_CHUNK'").fetchone()[0])
        with TestClient(create_app(self.config,self.authority),base_url='https://testserver') as http:
            client = SubmissionClient(self.client_config,self.f['provider'],self.f['authority'],state_root=self.root/'state',
                transport=AsgiTransport(self.client_config,http))
            self.assertEqual('VWP_UPLOADED',client.retry(sid)['delivery_state'])
        self.assertEqual([],list((self.root/'scratch').iterdir()))

    def test_incomplete_submission_is_not_parsed_or_published(self):
        sid,_,value = self.bundle()
        with self.store(sid) as store:
            store.initialize(sid,self.f['actor'],value)
            with self.assertRaises(ReviewError):store.commit(sid,self.f['actor'])
        self.assertEqual(0,self.bridge()['processed'])
        self.assertEqual(0,self.admin_query('SELECT count(*) FROM {vwp_to_oa}').fetchone()[0])
        self.assertEqual('UPLOADING',self.client.status(sid)['delivery_state'])

    def test_analysis_crash_removes_scratch_and_restarts_from_database_originals(self):
        sent = self.submit()
        original = SubmissionStore.put_outbox
        calls = []
        def interrupted(store,*args):
            if calls:raise RuntimeError('synthetic crash after a persisted source part')
            result = original(store,*args)
            calls.append(result)
            return result
        with patch.object(SubmissionStore,'put_outbox',new=interrupted):
            with self.assertRaises(RuntimeError):self.bridge()
        self.assertEqual(1,len(calls))
        self.assertEqual('PARSING',self.client.status(sent['submission_id'])['delivery_state'])
        self.assertEqual([],list((self.root/'scratch').iterdir()))
        self.assertEqual(1,self.bridge()['processed'])
        self.receive(); self.bridge()
        self.assertEqual('OA_STORED',self.client.status(sent['submission_id'])['delivery_state'])
        self.assertEqual([],list((self.root/'scratch').iterdir()))

    def test_same_uuid_conflicts_are_rejected_and_same_bytes_are_deduplicated(self):
        first = self.submit()
        count = self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='RAW_CHUNK'").fetchone()[0]
        second = self.submit()
        self.assertNotEqual(first['submission_id'],second['submission_id'])
        self.assertEqual(count,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='RAW_CHUNK'").fetchone()[0])
        value = verify_bundle(self.root/'state'/first['submission_id']/'bundle')
        changed = {'submission':copy.deepcopy(value[0]),'manifest':copy.deepcopy(value[1])}
        changed['manifest']['files'][0]['sha256'] = '0'*64
        changed['submission']['content_sha256'] = digest(changed['manifest'])
        with self.store(first['submission_id']) as store:
            with self.assertRaises(ReviewError):store.initialize(first['submission_id'],self.f['actor'],changed)

    def test_damaged_file_mapping_is_detected_before_parsing_and_temporary_files_are_removed(self):
        sent = self.submit()
        sid = sent['submission_id']
        self.admin_query("UPDATE {vwp} SET payload=jsonb_set(payload,'{{chunks}}',%s) "
            "WHERE record_type='RAW_FILE' AND payload->>'submission_id'=%s",(json_value(['0'*64]),sid))
        with self.assertRaises(ReviewError):self.bridge()
        self.assertEqual([],list((self.root/'scratch').iterdir()))
        self.assertEqual(0,self.admin_query('SELECT count(*) FROM {vwp_to_oa}').fetchone()[0])

    def test_private_raw_bytes_and_payloads_keep_restricted_database_permissions(self):
        self.submit()
        with Database('oa') as db:
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):db.execute("SELECT raw_bytes FROM {vwp}")
        with Database('vwp') as db:
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                db.execute("UPDATE {vwp} SET raw_bytes='x'::bytea WHERE record_type='RAW_CHUNK'")
            with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                db.execute("UPDATE {vwp} SET payload=%s WHERE record_type='RAW_FILE'",(json_value({}),))
            with self.assertRaises(psycopg2.errors.CheckViolation):
                db.execute("UPDATE {vwp} SET version=1 WHERE record_type='RAW_FILE'")

    def test_concurrent_api_updates_use_database_locks_and_owner_changes_remain_blocked(self):
        sent = self.submit()
        sid = sent['submission_id']
        with self.store(sid):
            self.assertEqual(409,self.http.get('/v1/submissions/'+sid,headers={'Authorization':'Bearer '+TOKEN}).status_code)
        self.f['authority'].allowed = False
        with self.assertRaises(ReviewError):self.bridge()
        self.assertEqual(0,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='SUBMISSION'").fetchone()[0])

    def test_archive_import_preserves_uuid_and_local_files_even_for_an_existing_submission(self):
        sid,bundle,value = self.bundle()
        with Database('vwp') as db,db.transaction():
            SubmissionStore(db).accept(value['submission'],node_id=self.config['node_id'])
        result = import_bundle(self.config,bundle)
        self.assertEqual((sid,False),(result['submission_id'],result['original_archive_removed']))
        self.assertTrue(bundle.exists())
        with self.store(sid) as store,store.materialize(sid) as restored:
            self.assertEqual(value['submission'],verify_bundle(restored)[0])
        self.assertEqual(result,import_bundle(self.config,bundle))

    def test_scratch_capacity_failure_keeps_db_originals_and_the_submission_recoverable(self):
        sent = self.submit()
        usage = shutil._ntuple_diskusage(100,99,1)
        with patch('common.sanity_raw_sources.shutil.disk_usage',return_value=usage):
            with self.assertRaises(ReviewError) as error:self.bridge()
        self.assertEqual('SUBMISSION_TEMP_LIMIT',error.exception.code)
        self.assertEqual('VWP_UPLOADED',self.client.status(sent['submission_id'])['delivery_state'])
        self.assertEqual(1,self.bridge()['processed'])

    def test_missing_raw_migration_fails_closed_without_creating_an_archive(self):
        self.admin_query('ALTER TABLE {vwp} DROP CONSTRAINT sanity_vwp_raw_payload_v4')
        with self.assertRaises(ReviewError) as error:create_app(self.config,self.authority)
        self.assertEqual('RAW_SOURCE_SCHEMA_REQUIRED',error.exception.code)
        self.assertFalse((self.root/'archive').exists())

    def test_larger_than_legacy_raw_field_limit_is_stored_as_bounded_chunks(self):
        # A 65MiB synthetic file exercises the raw-file contract, not a 100k-log model test.
        data = b'Z'*CHUNK_BYTES
        original = self.root/'input-post/large.rpt'
        with original.open('wb') as stream:
            for _ in range(65):stream.write(data)
            stream.write(b'last\x00\xff')
        self.f['prepared'].reports['reports/large.rpt'] = original
        sid,bundle,value = self.bundle()
        result = import_bundle(self.config,bundle)
        self.assertEqual('VWP_UPLOADED',result['delivery_state'])
        index = next(i for i,f in enumerate(value['manifest']['files']) if f['path']=='reports/large.rpt')
        with self.store(sid) as store:
            record = value['manifest']['files'][index]
            parts = store.file_record(sid,index,record)['chunks']
            self.assertEqual(66,len(parts))
            actual = hashlib.sha256()
            for raw in store.iter_file(sid,index,record):actual.update(raw)
            self.assertEqual(record['sha256'],actual.hexdigest())
        self.assertLessEqual(self.admin_query("SELECT max(octet_length(raw_bytes)) FROM {vwp} WHERE record_type='RAW_CHUNK'").fetchone()[0],CHUNK_BYTES)
