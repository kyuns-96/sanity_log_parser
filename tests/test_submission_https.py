"""HTTPS handoff without a shared filesystem: auth, bytes, retries and real TLS."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from common.sanity_common import ReviewError, canonical_json
from common.sanity_submission_files import load_config, write_json_file
from common.sanity_submission_https import validate_https_config, validate_http_receipt
from devtools.sanity_submission_fixture import fixture
from tests.submission_https_support import AsgiTransport, RemoteAuthority, TOKEN, configs, tls_server
from vwp.submission_cli import SubmissionClient
from vwp.submission_https import HttpsTransport
from vwp_bridge.submission_api import create_app


class HttpsSubmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sanity-https-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.f = fixture(self.root)
        self.client_config,self.server_config = configs(self.root,self.f['actor'])
        self.authority = RemoteAuthority(self.f['authority'])
        self.app = create_app(self.server_config,self.authority)
        self.http = TestClient(self.app,base_url='https://testserver')
        self.addCleanup(self.http.close)
        self.transport = AsgiTransport(self.client_config,self.http)
        self.client = SubmissionClient(self.client_config,self.f['provider'],self.f['authority'],
            state_root=self.root/'state',transport=self.transport)
        # There is no shared storage on either side of this deployment.
        shutil.rmtree(self.root/'spool')

    def submit(self):
        return self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)


    def request(self, method, path, *, value=None, content=None, token=TOKEN, **kwargs):
        headers = {'Authorization':'Bearer '+token}
        if value is not None:
            content = canonical_json(value)
            headers['Content-Type']='application/json'
        elif content is not None:
            headers['Content-Type']='application/octet-stream'
        headers.update(kwargs.pop('headers',{}))
        return self.http.request(method,path,content=content,headers=headers,**kwargs)


    def test_transport_config_is_explicit_https_and_receipts_are_bound_to_the_owner(self):
        for endpoint in ('http://testserver','https://a:b@testserver','https://testserver/path','https://testserver/?token=x'):
            with self.assertRaises(ReviewError):validate_https_config({**self.client_config,'endpoint':endpoint})
        configfile=self.root/'client.json'
        write_json_file(configfile,self.client_config)
        self.assertEqual(self.client_config,load_config(configfile))
        sent=self.submit()
        sent['actor_key']='foreign-owner'
        with self.assertRaises(ReviewError):validate_http_receipt(sent,sent['submission_id'],self.f['actor'])


    def test_real_https_transport_checks_ca_and_completes_a_submission(self):
        with tls_server(self.app,self.root) as (endpoint,ca_file),patch.dict(os.environ,{'SANITY_VWP_SUBMIT_TOKEN':TOKEN}):
            config={**self.client_config,'endpoint':endpoint,'ca_file':ca_file}
            client=SubmissionClient(config,self.f['provider'],self.f['authority'],state_root=self.root/'state')
            sent=client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
            self.assertEqual('VWP_UPLOADED',sent['delivery_state'])
            self.assertEqual(sent,client.status(sent['submission_id']))
            untrusted=HttpsTransport({**config,'ca_file':None})
            with self.assertRaises(ReviewError) as error:untrusted.status(sent['submission_id'],self.f['actor'])
            self.assertEqual('SUBMISSION_CONNECTION_FAILED',error.exception.code)
