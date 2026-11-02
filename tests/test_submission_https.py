"""HTTPS handoff without a shared filesystem: auth, bytes, retries and real TLS."""
import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

from tests.http_support import TestClient

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_submission_files import load_config, verify_bundle, write_json_file
from common.sanity_submission_https import API, validate_https_config, validate_http_receipt
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

    def staged(self):
        self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None,dry_run=True)
        sid = str(uuid.uuid4())
        from common.sanity_submission_files import snapshot
        bundle = self.root/'state'/sid/'bundle'
        bundle.mkdir(parents=True,mode=0o700)
        manifest = snapshot(self.f['prepared'],bundle/'snapshot')
        submission = {'schema_version':2,'submission_id':sid,'actor':self.f['actor'].actor,
                      'content_sha256':digest(manifest),'plan':manifest['plan']}
        write_json_file(bundle/'submission.json',submission)
        return sid,bundle,{'submission':submission,'manifest':manifest}

    def request(self, method, path, *, value=None, content=None, token=TOKEN, **kwargs):
        headers = {'Authorization':'Bearer '+token}
        if value is not None:
            content = canonical_json(value)
            headers['Content-Type']='application/json'
        elif content is not None:
            headers['Content-Type']='application/octet-stream'
        headers.update(kwargs.pop('headers',{}))
        return self.http.request(method,path,content=content,headers=headers,**kwargs)

    def test_complete_upload_preserves_original_bytes_without_any_shared_mount(self):
        value = self.submit()
        sid = value['submission_id']
        self.assertEqual('VWP_UPLOADED',value['delivery_state'])
        self.assertFalse(value['verified_oa_state'])
        submission,manifest = verify_bundle(self.root/'archive'/sid)
        self.assertEqual(self.f['actor'].actor,submission['actor'])
        self.assertEqual(2,len(manifest['files']))
        for relative,original in self.f['prepared'].reports.items():
            self.assertEqual(original.read_bytes(),(self.root/'archive'/sid/'snapshot'/relative).read_bytes())
        self.assertEqual(value,self.client.status(sid))
        self.assertFalse((self.root/'spool').exists())

    def test_dry_run_and_cancel_do_not_contact_the_server(self):
        self.client.submit(self.f['target'],confirm=lambda _:self.fail(),preview=lambda _:None,dry_run=True)
        with self.assertRaises(ReviewError):
            self.client.submit(self.f['target'],confirm=lambda _:False,preview=lambda _:None)
        self.assertEqual([],self.transport.calls)
        self.assertEqual([],list((self.root/'state').iterdir()))

    def test_unknown_credentials_and_company_authentication_default_fail_closed(self):
        sid,_,value = self.staged()
        response = self.request('POST',API+'/'+sid,value=value,token='invalid')
        self.assertEqual(401,response.status_code)
        app = create_app(self.server_config,self.f['authority'])
        with TestClient(app,base_url='https://testserver') as http:
            response = http.post(API+'/'+sid,json=value,headers={'Authorization':'Bearer '+TOKEN})
        self.assertEqual((503,'SUBMISSION_AUTH_NOT_CONFIGURED'),(response.status_code,response.json()['code']))
        self.assertFalse((self.root/'uploads'/sid).exists())

    def test_manifest_actor_cannot_impersonate_another_user(self):
        sid,_,value = self.staged()
        value['submission']['actor']['subject']='forged-account'
        response = self.request('POST',API+'/'+sid,value=value)
        self.assertEqual(403,response.status_code)
        self.assertFalse((self.root/'uploads'/sid).exists())

    def test_http_host_forwarded_identity_origin_and_query_are_rejected(self):
        sid,_,value = self.staged()
        url = API+'/'+sid
        with TestClient(self.app,base_url='http://testserver') as http:
            result = http.post(url,json=value,headers={'Authorization':'Bearer '+TOKEN,'X-Forwarded-Proto':'https',
                                                     'X-User-Id':'user-04'})
        self.assertEqual(400,result.status_code)
        self.assertEqual(400,self.request('POST',url,value=value,headers={'Host':'foreign.invalid'}).status_code)
        self.assertEqual(400,self.request('POST',url,value=value,headers={'Origin':'https://testserver'}).status_code)
        self.assertEqual(400,self.request('POST',url+'?token=anything',value=value).status_code)
        self.assertEqual(401,self.http.post(url,json=value,headers={'X-User-Id':'user-04'}).status_code)

    def test_scope_authorization_and_approval_are_checked_before_upload(self):
        sid,_,value = self.staged()
        self.f['authority'].allowed=False
        self.assertEqual(403,self.request('POST',API+'/'+sid,value=value).status_code)
        self.assertFalse((self.root/'uploads'/sid).exists())
        self.f['authority'].allowed=True
        with patch.object(self.authority,'approval_valid',side_effect=ReviewError('SUBMISSION_APPROVAL_NOT_CONFIGURED','private')):
            self.assertEqual(503,self.request('POST',API+'/'+sid,value=value).status_code)

    def test_invalid_path_and_manifest_limits_never_create_an_upload(self):
        sid,_,value = self.staged()
        for path,size in [('reports/../../escape',1),('reports/a',16*1024**3+1)]:
            changed=copy.deepcopy(value)
            changed['manifest']['files'][0].update(path=path,size_bytes=size)
            changed['submission']['content_sha256']=digest(changed['manifest'])
            response=self.request('POST',API+'/'+sid,value=changed)
            self.assertIn(response.status_code,(413,422))
        self.assertFalse((self.root/'uploads'/sid).exists())

    def test_incomplete_and_wrong_hash_uploads_never_enter_the_archive(self):
        sid,bundle,value = self.staged()
        path=API+'/'+sid
        self.assertEqual(200,self.request('POST',path,value=value).status_code)
        self.assertNotEqual(200,self.request('POST',path+'/commit',content=b'').status_code)
        record=value['manifest']['files'][0]
        original=(bundle/'snapshot'/record['path']).read_bytes()
        result=self.request('PUT',path+'/files/0',content=b'x'*len(original))
        self.assertEqual(422,result.status_code)
        self.assertEqual([],self.request('GET',path).json()['received_files'])
        self.assertFalse((self.root/'archive'/sid).exists())
        self.assertEqual([],list((self.root/'uploads'/sid).glob('.upload-*')))

    def test_duplicate_uuid_requires_exact_same_actor_manifest_and_bytes(self):
        sent=self.submit()
        sid=sent['submission_id']
        bundle=self.root/'state'/sid/'bundle'
        submission,manifest=verify_bundle(bundle)
        value={'submission':submission,'manifest':manifest}
        self.assertEqual(200,self.request('POST',API+'/'+sid,value=value).status_code)
        value['manifest']['files'][0]['sha256']='0'*64
        value['submission']['content_sha256']=digest(value['manifest'])
        self.assertEqual(409,self.request('POST',API+'/'+sid,value=value).status_code)
        self.assertEqual(sent,self.client.retry(sid))

    def test_credential_revoked_during_upload_does_not_publish_the_file(self):
        sid,bundle,value=self.staged()
        path=API+'/'+sid
        self.assertEqual(200,self.request('POST',path,value=value).status_code)
        record=value['manifest']['files'][0]
        data=(bundle/'snapshot'/record['path']).read_bytes()
        with patch.object(self.authority,'authenticate_bearer',side_effect=[self.authority.actor,
                ReviewError('SUBMISSION_AUTH_REQUIRED','synthetic revoked token')]):
            result=self.request('PUT',path+'/files/0',content=data)
        self.assertEqual(401,result.status_code)
        self.assertEqual([],self.request('GET',path).json()['received_files'])
        self.assertFalse((self.root/'archive'/sid).exists())

    def test_truncated_or_encoded_bodies_cannot_finish_a_file(self):
        sid,bundle,value=self.staged()
        path=API+'/'+sid
        self.assertEqual(200,self.request('POST',path,value=value).status_code)
        record=value['manifest']['files'][0]
        data=(bundle/'snapshot'/record['path']).read_bytes()
        result=self.request('PUT',path+'/files/0',content=data[:-1],headers={'Content-Length':str(len(data))})
        self.assertEqual(422,result.status_code)
        result=self.request('PUT',path+'/files/0',content=data,headers={'Content-Encoding':'gzip'})
        self.assertEqual(400,result.status_code)
        self.assertEqual([],self.request('GET',path).json()['received_files'])

    def test_retry_skips_received_files_and_keeps_the_original_uuid_and_snapshot(self):
        request=self.transport.request
        failed=[]
        def interrupt(method,path,*args,**kwargs):
            if path.endswith('/files/1') and not failed:
                failed.append(True)
                raise ReviewError('SUBMISSION_CONNECTION_FAILED','synthetic interruption')
            return request(method,path,*args,**kwargs)
        with patch.object(self.transport,'request',side_effect=interrupt):
            with self.assertRaises(ReviewError):self.submit()
        sid=next((self.root/'state').iterdir()).name
        self.assertEqual([0],self.client.status(sid)['received_files'])
        self.f['prepared'].reports['reports/post/sample.rpt'].write_bytes(b'new source bytes')
        calls=len(self.transport.calls)
        result=self.client.retry(sid)
        self.assertEqual((sid,'VWP_UPLOADED'),(result['submission_id'],result['delivery_state']))
        self.assertNotIn(('PUT',API+'/'+sid+'/files/0'),self.transport.calls[calls:])
        self.assertNotEqual(b'new source bytes',(self.root/'archive'/sid/'snapshot/reports/post/sample.rpt').read_bytes())

    def test_lost_commit_response_is_resolved_by_status_without_reupload(self):
        request=self.transport.request
        def lose_response(method,path,*args,**kwargs):
            result=request(method,path,*args,**kwargs)
            if path.endswith('/commit'):
                raise ReviewError('SUBMISSION_CONNECTION_FAILED','synthetic lost response')
            return result
        with patch.object(self.transport,'request',side_effect=lose_response):
            with self.assertRaises(ReviewError):self.submit()
        sid=next((self.root/'state').iterdir()).name
        before=len(self.transport.calls)
        self.assertEqual('VWP_UPLOADED',self.client.retry(sid)['delivery_state'])
        self.assertEqual([('GET',API+'/'+sid)],self.transport.calls[before:])

    def test_status_is_private_and_rechecks_active_identity_and_block_rights(self):
        sent=self.submit()
        path=API+'/'+sent['submission_id']
        remote=self.authority.actor
        from common.sanity_submission import VerifiedRemoteSubmitter
        self.authority.actor=VerifiedRemoteSubmitter(remote.issuer,'other-subject','user-05')
        self.assertEqual(404,self.request('GET',path).status_code)
        self.authority.actor=remote
        self.f['authority'].allowed=False
        self.assertEqual(403,self.request('GET',path).status_code)
        self.authority.enabled=False
        self.assertEqual(401,self.request('GET',path).status_code)

    def test_commit_is_idempotent_and_serializes_concurrent_updates(self):
        result=self.submit()
        sid=result['submission_id']
        self.assertEqual(result,self.request('POST',API+'/'+sid+'/commit',content=b'').json())
        with self.app.state.uploads.lock(sid):
            self.assertEqual(409,self.request('GET',API+'/'+sid).status_code)

    def test_transport_config_is_explicit_https_and_receipts_are_bound_to_the_owner(self):
        for endpoint in ('http://testserver','https://a:b@testserver','https://testserver/path','https://testserver/?token=x'):
            with self.assertRaises(ReviewError):validate_https_config({**self.client_config,'endpoint':endpoint})
        configfile=self.root/'client.json'
        write_json_file(configfile,self.client_config)
        self.assertEqual(self.client_config,load_config(configfile))
        sent=self.submit()
        sent['actor_key']='foreign-owner'
        with self.assertRaises(ReviewError):validate_http_receipt(sent,sent['submission_id'],self.f['actor'])

    def test_unexpected_sdk_errors_return_a_fixed_code_without_secret_messages(self):
        sid,_,value=self.staged()
        with patch.object(self.authority,'authenticate_bearer',side_effect=RuntimeError('private-token-diagnostic')):
            response=self.request('POST',API+'/'+sid,value=value)
        self.assertEqual((503,{'code':'SUBMISSION_ADAPTER_FAILED'}),(response.status_code,response.json()))
        self.assertEqual('no-store',response.headers['cache-control'])

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

    def test_transport_rejects_missing_credentials_before_connecting(self):
        transport=HttpsTransport(self.client_config)
        with patch.dict(os.environ,{},clear=True),patch('http.client.HTTPSConnection') as connection:
            with self.assertRaises(ReviewError) as error:transport.status(str(uuid.uuid4()),self.f['actor'])
        self.assertEqual('SUBMISSION_AUTH_REQUIRED',error.exception.code)
        connection.assert_not_called()

    def test_separate_mounts_that_cannot_atomically_publish_are_rejected_at_startup(self):
        import errno
        from vwp_bridge.submission_uploads import UploadStore
        with patch('vwp_bridge.submission_uploads.os.rename',side_effect=OSError(errno.EXDEV,'synthetic cross-mount')):
            with self.assertRaises(ReviewError) as error:UploadStore(self.server_config)
        self.assertEqual('SUBMISSION_STORAGE_NOT_CONFIGURED',error.exception.code)
        self.assertEqual([],list((self.root/'uploads').glob('.rename-check-*')))

    def test_http_failures_keep_fixed_codes_and_never_follow_redirects_or_retry(self):
        transport=HttpsTransport(self.client_config)
        for status,payload,expected in ((503,{'code':'AUTHORIZATION_NOT_IMPLEMENTED'},'AUTHORIZATION_NOT_IMPLEMENTED'),
                (503,{'code':'private-diagnostic'},'SUBMISSION_HTTP_FAILED'),
                (503,{'code':['private-diagnostic']},'SUBMISSION_HTTP_FAILED'),
                (302,{'location':'https://foreign.invalid'},'SUBMISSION_HTTP_FAILED')):
            with self.subTest(status=status,payload=payload):
                response=MagicMock(status=status)
                response.read.return_value=canonical_json(payload)
                response.getheader.side_effect=lambda key,default=None: {'Content-Type':'application/json'}.get(key,default)
                with patch.dict(os.environ,{'SANITY_VWP_SUBMIT_TOKEN':TOKEN}),patch('http.client.HTTPSConnection') as connection:
                    connection.return_value.getresponse.return_value=response
                    with self.assertRaises(ReviewError) as error:transport.status(str(uuid.uuid4()),self.f['actor'])
                self.assertEqual(expected,error.exception.code)
                connection.assert_called_once()
                self.assertEqual(1,connection.return_value.request.call_count)
