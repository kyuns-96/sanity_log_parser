"""Actual TLS -> private archive -> PostgreSQL source receiver -> HTTPS receipt."""
from pathlib import Path
import os
import shutil
import tempfile
from unittest.mock import patch

from common.sanity_common import ReviewError
from common.sanity_db import Database
from common.sanity_parsers import ParserRegistry
from common.sanity_submission_store import SubmissionStore
from devtools.sanity_submission_fixture import FixtureIdentity, fixture
from oa.sanity_workspace import seed_workspace
from oa.submission_receiver import SourceReceiver
from oa.test_workspace import fixture as workspace_fixture
from tests.submission_https_support import RemoteAuthority, TOKEN, configs, tls_server
from tests.support import PgTestCase, ROOT
from vwp.submission_cli import SubmissionClient
from vwp_bridge.submission_api import create_app
from vwp_bridge.submission_worker import SubmissionWorker


class HttpsSubmissionDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.admin.execute((ROOT/'db/008_submissions.sql').read_text().replace('public.',self.schema+'.'))
        temporary=tempfile.TemporaryDirectory(prefix='sanity-https-db-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.f=fixture(self.root)
        self.client_config,self.server_config=configs(self.root,self.f['actor'])
        self.authority=RemoteAuthority(self.f['authority'])
        self.app=create_app(self.server_config,self.authority)
        shutil.rmtree(self.root/'spool')
        with Database('oa') as db,db.transaction():
            seed_workspace(db,workspace_fixture())

    def bridge(self):
        with Database('vwp') as db:
            return SubmissionWorker(db,self.server_config,self.authority,ParserRegistry()).once()

    def receive(self):
        with Database('oa') as db:
            receiver=SourceReceiver(db,FixtureIdentity())
            while receiver.receive_one()['status'] != 'NO_MESSAGE':
                pass

    def test_real_tls_upload_reaches_oa_and_returns_its_verified_receipt_without_shared_storage(self):
        with tls_server(self.app,self.root) as (endpoint,ca_file),patch.dict(os.environ,{'SANITY_VWP_SUBMIT_TOKEN':TOKEN}):
            client=SubmissionClient({**self.client_config,'endpoint':endpoint,'ca_file':ca_file},
                self.f['provider'],self.f['authority'],state_root=self.root/'state')
            sent=client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
            self.assertEqual('VWP_UPLOADED',sent['delivery_state'])
            self.assertEqual(0,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='SUBMISSION'").fetchone()[0])
            self.assertEqual(1,self.bridge()['processed'])
            self.assertEqual('DB_QUEUED',client.status(sent['submission_id'])['delivery_state'])
            self.receive()
            self.bridge()
            result=client.status(sent['submission_id'])
            self.assertEqual(('OA_STORED',True,'READY'),
                (result['delivery_state'],result['verified_oa_state'],result['review_readiness']))
            self.assertTrue(result['workspace_links'])
            self.assertEqual(8,self.admin_query("SELECT sum((payload->>'record_count')::int) FROM {oa} "
                "WHERE record_type='WS_REPORT' AND payload->>'source_storage'='PARTS_V2'").fetchone()[0])
            self.assertFalse((self.root/'spool').exists())

    def test_worker_resumes_partial_analysis_from_the_private_http_archive_after_restart(self):
        from fastapi.testclient import TestClient
        from tests.submission_https_support import AsgiTransport
        with TestClient(self.app,base_url='https://testserver') as http:
            client=SubmissionClient(self.client_config,self.f['provider'],self.f['authority'],state_root=self.root/'state',
                transport=AsgiTransport(self.client_config,http))
            sent=client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
            original=SubmissionStore.put_outbox
            calls=[]
            def interrupted(store,*args):
                if calls:
                    raise RuntimeError('synthetic interruption after one part')
                result=original(store,*args)
                calls.append(result)
                return result
            with patch.object(SubmissionStore,'put_outbox',new=interrupted):
                with self.assertRaises(RuntimeError):self.bridge()
            self.assertEqual(1,len(calls))
            self.assertEqual('PARSING',client.status(sent['submission_id'])['delivery_state'])
            self.assertEqual(1,self.bridge()['processed'])
            self.receive()
            self.bridge()
            self.assertEqual('OA_STORED',client.status(sent['submission_id'])['delivery_state'])
            self.assertEqual(1,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='SUBMISSION'").fetchone()[0])

    def test_worker_rechecks_remote_identity_and_current_block_assignment_before_analysis(self):
        from fastapi.testclient import TestClient
        from tests.submission_https_support import AsgiTransport
        with TestClient(self.app,base_url='https://testserver') as http:
            client=SubmissionClient(self.client_config,self.f['provider'],self.f['authority'],state_root=self.root/'state',
                transport=AsgiTransport(self.client_config,http))
            client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
        self.f['authority'].allowed=False
        with self.assertRaises(ReviewError) as error:self.bridge()
        self.assertEqual('FORBIDDEN',error.exception.code)
        self.assertEqual(0,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type IN ('SUBMISSION','EVIDENCE')").fetchone()[0])
        self.assertEqual(0,self.admin_query('SELECT count(*) FROM {vwp_to_oa}').fetchone()[0])
