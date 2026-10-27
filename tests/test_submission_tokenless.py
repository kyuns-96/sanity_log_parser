"""Real token-free HTTPS intake and current SSO OWNER confirmation boundaries."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import Database, Queue, json_value, stable_uuid
from common.sanity_parsers import ParserRegistry
from common.sanity_raw_sources import DatabaseUploadStore
from common.sanity_submission_files import verify_bundle
from common.sanity_submission_https import validate_https_config
from common.sanity_submission_intake import (PENDING_BLOCKER, UnverifiedSubmitter, local_label,
    require_internal_peer, unverified)
from devtools.sanity_submission_fixture import fixture
from oa.dashboard_api import create_app as oa_app
from oa.sanity_access import AccessStore
from oa.sanity_production_workspace import ProductionWorkspaceStore
from oa.submission_receiver import SourceReceiver
from tests.submission_https_support import tls_server
from tests.test_scoped_dashboard import (ADMIN, OWNER, TOP, OTHER, USERS, ISSUER, STAMP,
    VerifiedFixtureProvider, FixtureDirectory)
from tests import test_scoped_dashboard as scoped
from tests.support import PgTestCase, ROOT
from vwp.submission_cli import SubmissionClient
from vwp.submission_https import HttpsTransport
from vwp_bridge.submission_api import create_app
from vwp_bridge.submission_worker import SubmissionWorker


class NoIdentityService:
    def __getattr__(self, name):
        raise AssertionError('Token-free intake must not call identity/authorization adapters: '+name)


class InternalTransport(HttpsTransport):
    def __init__(self, config, http):
        super().__init__(config)
        self.http=http

    def request(self, method, path, body=None, *, length=None, content_type='application/json'):
        if hasattr(body,'read'):body=body.read()
        response=self.http.request(method,path,content=body,headers={'Content-Type':content_type})
        if response.status_code not in (200,201,202):
            raise ReviewError(response.json()['code'],'Synthetic internal request rejected')
        return response.json()


class InternalIntakeContracts(unittest.TestCase):
    def test_unverified_label_is_not_a_verified_company_actor(self):
        from common.sanity_submission import VerifiedRemoteSubmitter
        value=local_label()
        self.assertTrue(unverified(value.actor))
        self.assertNotIsInstance(value,VerifiedRemoteSubmitter)
        with self.assertRaises(ReviewError):UnverifiedSubmitter(value.issuer,value.subject,OWNER)

    def test_explicit_config_and_real_peer_network_are_required(self):
        base={'schema_version':4,'transport':'https','submission_auth':'internal_network','storage':'postgresql',
              'service_uid':10001,'node_id':'test','temp_root':None,'allowed_hosts':['bridge.example.invalid'],
              'allowed_client_networks':['192.0.2.10/32']}
        self.assertEqual(base,validate_https_config(base,bridge=True))
        require_internal_peer(base,'192.0.2.10')
        require_internal_peer(base,'::ffff:192.0.2.10')
        for peer in (None,'hostname','192.0.2.11','127.0.0.1'):
            with self.assertRaises(ReviewError):require_internal_peer(base,peer)
        for networks in ([],['0.0.0.0/0'],['::/0'],['192.0.2.10/24'],['bad']):
            with self.assertRaises(ReviewError):validate_https_config({**base,'allowed_client_networks':networks},bridge=True)
        with self.assertRaises(ReviewError):validate_https_config({**base,'schema_version':3},bridge=True)


class TokenlessSubmissionTests(PgTestCase):
    post=scoped.ScopedDashboardTests.post
    success=scoped.ScopedDashboardTests.success
    register=scoped.ScopedDashboardTests.register
    grant=scoped.ScopedDashboardTests.grant
    revoke_body=scoped.ScopedDashboardTests.revoke_body

    def setUp(self):
        super().setUp()
        for name in ('008_submissions.sql','009_sso_identity.sql','010_raw_sources.sql'):
            self.admin.execute((ROOT/'db'/name).read_text().replace('public.',self.schema+'.'))
        env=patch.dict(os.environ,{'SANITY_AUTH_MODE':'sso','SANITY_ADMIN_USER_ID':ADMIN,'SANITY_AI_MODE':'disabled'})
        env.start();self.addCleanup(env.stop)
        for user in USERS:
            self.admin_query("INSERT INTO {oa}(record_id,record_type,workspace_key,payload,state) "
                "VALUES (%s,'SSO_IDENTITY',%s,%s,%s)",(stable_uuid('sso-identity-v1','SSO_IDENTITY',user),user,
                json_value({'issuer':ISSUER,'subject':user,'display_name':'Synthetic '+user,'first_seen_at':STAMP}),
                json_value({'enabled':True,'last_seen_at':STAMP})))
        self.oa=oa_app(auth_mode='sso',identity_provider=VerifiedFixtureProvider(),user_directory=FixtureDirectory())
        self.clients={user:TestClient(self.oa,base_url='http://127.0.0.1',raise_server_exceptions=False,
            cookies={'synthetic_session':'synthetic-verified-session-'+str(USERS.index(user))}) for user in USERS}
        for client in self.clients.values():self.addCleanup(client.close)
        self.register();self.owner_grant=self.grant(OWNER,'OWNER',block='block-a1');self.grant(TOP,'TOP')
        tmp=tempfile.TemporaryDirectory(prefix='sanity-tokenless-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.f=fixture(self.root)
        self.f['prepared'].plan['collection'].update(project_key='project-a',block_key='block-a1')
        self.f['target'].update(project='project-a',block='block-a1')
        for name in ('archive','spool'):shutil.rmtree(self.root/name)
        (self.root/'scratch').mkdir()
        self.config={'schema_version':4,'transport':'https','submission_auth':'internal_network','storage':'postgresql',
            'allowed_client_networks':['127.0.0.1/32'],'allowed_hosts':['localhost','testserver','127.0.0.1'],
            'service_uid':os.geteuid(),'node_id':'test-internal','temp_root':str(self.root/'scratch')}
        self.client_config={'schema_version':3,'transport':'https','submission_auth':'internal_network',
            'endpoint':'https://testserver','ca_file':None,'timeout_seconds':10,'default_profile':'synthetic-top'}
        self.no_identity=NoIdentityService()
        self.app=create_app(self.config,self.no_identity)
        self.http=TestClient(self.app,base_url='https://testserver',client=('127.0.0.1',12345))
        self.addCleanup(self.http.close)
        self.client=SubmissionClient(self.client_config,self.f['provider'],self.no_identity,state_root=self.root/'state',
                                     transport=InternalTransport(self.client_config,self.http))

    def bridge(self):
        with Database('vwp') as db:
            return SubmissionWorker(db,self.config,self.no_identity,ParserRegistry()).once()

    def receiver(self, db):
        return SourceReceiver(db,self.no_identity,workspace=ProductionWorkspaceStore(db,AccessStore(db,ADMIN)))

    def receive(self):
        with Database('oa') as db:
            receiver=self.receiver(db)
            while receiver.receive_one()['status']!='NO_MESSAGE':pass

    def submit(self, *, receive=True):
        sent=self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
        self.bridge()
        if receive:self.receive()
        return sent['submission_id']

    def detail(self, sid, user=OWNER, suffix=''):
        return self.success(self.clients[user].get('/api/workbench/blocks/block-a1/intake/'+sid+suffix))

    def body(self, sid, decision='CONFIRM'):
        detail=self.detail(sid)
        return {'decision':decision,'expected_revision':detail['revision'],'request_id':str(uuid.uuid4()),
            'reason':'Synthetic owner reviewed the exact declared scope','confirmed_scope':decision=='CONFIRM',
            **{k:detail[k] for k in ('content_sha256','manifest_sha256','scope_sha256')}}

    def decide(self, sid, body=None, user=OWNER):
        return self.post(user,'/api/workbench/blocks/block-a1/intake/'+sid,body or self.body(sid))

    def report_count(self):
        return self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_REPORT'").fetchone()[0]

    def test_real_tls_without_token_reaches_pending_then_sso_owner_publishes(self):
        headers=[]
        @self.app.middleware('http')
        async def capture(request,next):
            headers.append(request.headers.get('authorization'))
            return await next(request)
        with tls_server(self.app,self.root) as (endpoint,ca),patch.dict(os.environ,{'SANITY_VWP_SUBMIT_TOKEN':'unused-invalid token'}),\
             patch('vwp.submission_https.bearer',side_effect=AssertionError('No bearer parsing allowed')):
            client=SubmissionClient({**self.client_config,'endpoint':endpoint,'ca_file':ca},self.f['provider'],
                self.no_identity,state_root=self.root/'state')
            sent=client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None);sid=sent['submission_id']
            self.assertEqual('VWP_UPLOADED',sent['delivery_state']);self.assertEqual(0,self.report_count())
            self.bridge();self.receive();self.bridge()
            status=client.status(sid)
            self.assertEqual(('DB_QUEUED','BLOCKED',[PENDING_BLOCKER]),
                (status['delivery_state'],status['review_readiness'],status['blockers']))
            self.assertEqual('AWAITING_CONFIRMATION',self.detail(sid)['delivery_state'])
            listing=self.success(self.clients[OWNER].get('/api/workbench/blocks/block-a1/submissions'))
            self.assertIsNone(listing['items'][0]['submitted_by'])
            self.assertFalse(listing['items'][0]['submitter_verified'])
            self.assertEqual(local_label().subject,listing['items'][0]['reported_by'])
            self.assertEqual(0,self.report_count())
            self.assertEqual(0,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='SOURCE_RUN'").fetchone()[0])
            result=self.success(self.decide(sid))
            self.assertEqual('OA_STORED',result['delivery_state']);self.assertEqual(OWNER,result['decision']['user_id'])
            self.assertFalse(result['submitter_verified']);self.assertEqual(2,self.report_count())
            self.bridge();final=client.status(sid)
            self.assertEqual(('OA_STORED','READY',True),(final['delivery_state'],final['review_readiness'],final['verified_oa_state']))
        self.assertTrue(headers);self.assertEqual({None},set(headers))
        self.assertFalse((self.root/'archive').exists());self.assertEqual([],list((self.root/'scratch').iterdir()))
        self.assertEqual(0,self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_POR_EVENT'").fetchone()[0])

    def test_network_origin_and_legacy_identity_cannot_be_forged(self):
        self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
        sid=next((self.root/'state').iterdir()).name
        bundle=verify_bundle(self.root/'state'/sid/'bundle');value={'submission':bundle[0],'manifest':bundle[1]}
        path='/v1/submissions/'+sid
        for peer in ('192.0.2.9','testclient'):
            with TestClient(self.app,base_url='https://testserver',client=(peer,1)) as http:
                response=http.get(path,headers={'X-Forwarded-For':'127.0.0.1'})
                self.assertEqual(403,response.status_code)
        self.assertEqual(400,self.http.get(path,headers={'Authorization':'Bearer ignored'}).status_code)
        self.assertEqual(400,self.http.get(path,headers={'Origin':'https://testserver'}).status_code)
        with TestClient(self.app,base_url='http://testserver',client=('127.0.0.1',1)) as http:
            self.assertEqual(400,http.get(path,headers={'X-Forwarded-Proto':'https'}).status_code)
        forged=copy.deepcopy(value);forged['submission']['actor']=self.f['actor'].actor
        self.assertEqual(422,self.http.post(path,json=forged).status_code)
        forged=copy.deepcopy(value);forged['submission']['actor']['user_id']=OWNER
        self.assertEqual(422,self.http.post(path,json=forged).status_code)
        forged=copy.deepcopy(value);forged['manifest']['plan']['inventory']['confirmed']=True
        forged['submission']['plan']=forged['manifest']['plan'];forged['submission']['content_sha256']=digest(forged['manifest'])
        self.assertEqual(422,self.http.post(path,json=forged).status_code)

    def test_tokenless_routes_cannot_read_or_modify_a_legacy_authenticated_upload(self):
        sid=str(uuid.uuid4())
        prepared=self.f['provider'].prepare(self.f['target'])
        from common.sanity_submission_files import snapshot
        manifest=snapshot(prepared,self.root/'legacy-snapshot')
        value={'submission':{'schema_version':2,'submission_id':sid,'actor':self.f['actor'].actor,
            'content_sha256':digest(manifest),'plan':prepared.plan},'manifest':manifest}
        with DatabaseUploadStore(self.config).session(sid) as store:
            store.initialize(sid,self.f['actor'],value)
        path='/v1/submissions/'+sid
        for method,suffix,kwargs in [('GET','',{}),('POST','/commit',{'content':b''}),
                                    ('PUT','/files/0',{'content':b'x','headers':{'Content-Type':'application/octet-stream'}})]:
            self.assertEqual(404,self.http.request(method,path+suffix,**kwargs).status_code)

    def test_only_current_owner_can_confirm_and_digest_revision_and_request_are_bound(self):
        sid=self.submit();body=self.body(sid)
        self.assertEqual(200,self.clients[TOP].get('/api/workbench/blocks/block-a1/intake/'+sid).status_code)
        for user in (TOP,OTHER,ADMIN):self.assertIn(self.decide(sid,body,user).status_code,(403,404))
        for change in ({'confirmed_scope':False},{'manifest_sha256':'0'*64},{'content_sha256':'0'*64},
                       {'scope_sha256':'0'*64},{'expected_revision':body['expected_revision']+1}):
            self.assertEqual(409,self.decide(sid,{**body,**change}).status_code)
        self.assertEqual(400,self.decide(sid,{**body,'user_id':OWNER}).status_code)
        self.assertEqual(403,self.clients[OWNER].post('/api/workbench/blocks/block-a1/intake/'+sid,json=body).status_code)
        self.assertEqual(0,self.report_count())
        first=self.success(self.decide(sid,body));second=self.success(self.decide(sid,body))
        self.assertEqual(first,second);self.assertEqual(2,self.report_count())
        self.assertEqual(409,self.decide(sid,{**body,'request_id':str(uuid.uuid4())}).status_code)
        self.assertEqual(409,self.decide(sid,{**body,'reason':'different'}).status_code)

    def test_confirmation_revalidates_staged_parts_and_rolls_back_partial_publication(self):
        sid=self.submit();body=self.body(sid)
        original=SourceReceiver.save_report;count=[]
        def fail_after_first(receiver,*args):
            if count:raise RuntimeError('synthetic interrupted publication')
            count.append(1)
            return original(receiver,*args)
        with patch.object(SourceReceiver,'save_report',fail_after_first):
            self.assertEqual(500,self.decide(sid,body).status_code)
        self.assertEqual(0,self.report_count())
        self.assertEqual('AWAITING_CONFIRMATION',self.detail(sid)['delivery_state'])
        self.assertIsNone(self.detail(sid)['decision'])
        self.assertEqual(body['expected_revision'],self.detail(sid)['revision'])
        self.success(self.decide(sid,body));self.assertEqual(2,self.report_count())

    def test_corrupt_staged_record_cannot_be_confirmed_or_become_model_input(self):
        sid=self.submit();body=self.body(sid)
        self.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{records,0,raw}}',%s) "
            "WHERE record_type='WS_SOURCE_PART'",(json_value('synthetic tampered raw'),))
        response=self.decide(sid,body)
        self.assertEqual(400,response.status_code)
        self.assertEqual('SOURCE_PART_CONFLICT',response.json()['code'])
        self.assertEqual(0,self.report_count());self.assertIsNone(self.detail(sid)['decision'])

    def test_owner_revocation_and_unconfigured_sso_do_not_allow_confirmation(self):
        sid=self.submit();body=self.body(sid)
        self.success(self.post(ADMIN,'/api/admin/grants/'+self.owner_grant+'/revoke',self.revoke_body()))
        self.assertIn(self.decide(sid,body).status_code,(403,404))
        with patch.dict(os.environ,{'SANITY_ADMIN_USER_ID':''}):
            self.assertEqual(503,self.decide(sid,body).status_code)
        self.assertEqual(0,self.report_count())

    def test_partial_source_parts_are_not_confirmable(self):
        sid=self.submit(receive=False)
        with Database('oa') as db:
            receiver=self.receiver(db)
            with Database('vwp') as sender:
                messages=sender.execute("SELECT payload FROM {vwp} WHERE record_type='SOURCE_OUTBOX' "
                    "AND payload->>'submission_id'=%s ORDER BY (payload->>'position')::int",(sid,)).fetchall()
            skipped=messages[1]['payload']['message_id']
            for row in messages:
                if row['payload']['message_id']!=skipped:receiver.receive_one(row['payload']['message_id'])
            self.assertEqual('IMPORTING',self.detail(sid)['delivery_state'])
            self.assertEqual(409,self.decide(sid).status_code);self.assertEqual(0,self.report_count())
            receiver.receive_one(skipped)
        self.assertEqual('AWAITING_CONFIRMATION',self.detail(sid)['delivery_state'])
        self.success(self.decide(sid));self.assertEqual(2,self.report_count())

    def test_rejected_claim_does_not_reserve_execution_or_publish_reports(self):
        original=self.f['prepared'].reports['reports/post/sample.rpt'].read_bytes()
        self.f['prepared'].reports['reports/post/sample.rpt'].write_bytes(original.replace(b',12,ns',b',13,ns'))
        sid=self.submit();self.success(self.decide(sid,self.body(sid,'REJECT')));self.bridge()
        self.assertEqual('REJECTED',self.client.status(sid)['delivery_state']);self.assertEqual(0,self.report_count())
        self.assertEqual(0,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='SOURCE_RUN'").fetchone()[0])
        self.f['prepared'].reports['reports/post/sample.rpt'].write_bytes(original)
        next_sid=self.submit();self.success(self.decide(next_sid));self.bridge()
        self.assertEqual('OA_STORED',self.client.status(next_sid)['delivery_state']);self.assertEqual(2,self.report_count())

    def test_delayed_pending_receipt_cannot_replace_final_owner_confirmation(self):
        sid=self.submit();self.success(self.decide(sid))
        with Database('vwp') as db:
            queue=Queue(db,'oa_to_vwp')
            final=db.execute("SELECT message_id FROM {oa_to_vwp} WHERE message_type='SOURCE_RECEIPT' "
                "AND payload->>'submission_id'=%s AND payload->>'delivery_state'='OA_STORED'",(sid,)).fetchone()['message_id']
            worker=SubmissionWorker(db,self.config,self.no_identity,ParserRegistry())
            original=Queue.claim
            with patch.object(Queue,'claim',lambda q,k:original(q,k,final)):
                self.assertTrue(worker.receive_receipt())
            self.assertTrue(worker.receive_receipt())
        self.assertEqual('OA_STORED',self.client.status(sid)['delivery_state'])

    def test_interrupted_tokenless_upload_resumes_after_api_restart(self):
        with patch.object(self.client.transport,'enqueue',side_effect=ReviewError('SYNTHETIC_STOP','stop')):
            with self.assertRaises(ReviewError):self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
        sid=next((self.root/'state').iterdir()).name
        submission,manifest=verify_bundle(self.root/'state'/sid/'bundle')
        path='/v1/submissions/'+sid
        self.assertEqual(200,self.http.post(path,json={'submission':submission,'manifest':manifest}).status_code)
        self.assertEqual(422,self.http.put(path+'/files/0',content=b'x',headers={'Content-Type':'application/octet-stream'}).status_code)
        with TestClient(create_app(self.config,self.no_identity),base_url='https://testserver',client=('127.0.0.1',2)) as http:
            client=SubmissionClient(self.client_config,self.f['provider'],self.no_identity,state_root=self.root/'state',
                                    transport=InternalTransport(self.client_config,http))
            self.assertEqual('VWP_UPLOADED',client.retry(sid)['delivery_state'])
        self.bridge();self.receive();self.success(self.decide(sid));self.bridge()
        self.assertEqual('OA_STORED',self.client.status(sid)['delivery_state'])

    def test_paginated_exact_scope_keeps_missing_and_extra_report_blockers(self):
        units=self.f['prepared'].plan['inventory']['units']
        template=copy.deepcopy(units[-1]);source=self.f['prepared'].reports[template['report']]
        for i in range(51):
            unit=copy.deepcopy(template);unit.update(review_unit_id='extra-'+str(i),item_id='item-'+str(i),report='reports/extra-'+str(i)+'.rpt')
            units.append(unit)
            if i!=50:self.f['prepared'].reports[unit['report']]=source
        self.f['prepared'].reports['reports/unexpected.rpt']=source
        sid=self.submit();first=self.detail(sid);last=self.detail(sid,suffix='?offset=50&limit=50')
        self.assertEqual(53,first['total_units']);self.assertEqual(50,len(first['units']));self.assertEqual(3,len(last['units']))
        self.assertEqual(first['scope_sha256'],last['scope_sha256'])
        self.assertEqual('MISSING',last['units'][-1]['parse_status'])
        self.success(self.decide(sid));self.bridge();status=self.client.status(sid)
        self.assertEqual('BLOCKED',status['review_readiness']);self.assertIn('UNEXPECTED_REPORTS',status['blockers'])
        self.assertIn('MISSING',status['blockers']);self.assertNotIn('INVENTORY_UNCONFIRMED',status['blockers'])
