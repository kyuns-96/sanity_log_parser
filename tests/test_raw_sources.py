"""Real PostgreSQL raw-byte preservation, recovery, isolation and HTTPS ingestion."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from common.sanity_common import digest
from common.sanity_db import Database
from common.sanity_parsers import ParserRegistry
from common.sanity_raw_sources import DatabaseUploadStore
from common.sanity_submission_files import snapshot, verify_bundle, write_json_file
from devtools.sanity_submission_fixture import FixtureIdentity, fixture
from oa.sanity_workspace import seed_workspace
from oa.submission_receiver import SourceReceiver
from oa.test_workspace import fixture as workspace_fixture
from tests.submission_https_support import AsgiTransport, RemoteAuthority, TOKEN, tls_server
from tests.support import PgTestCase, ROOT
from vwp.submission_cli import SubmissionClient
from vwp_bridge.submission_api import create_app
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
        for name in ('011_payload_storage.sql', '012_source_evidence_pages.sql', '013_shared_source_packets.sql'):
            self.admin.execute((ROOT/'db'/name).read_text().replace('public.', self.schema+'.'))
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
