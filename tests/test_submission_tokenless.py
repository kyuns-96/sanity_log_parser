"""Real token-free HTTPS intake and current SSO OWNER confirmation boundaries."""
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from common.sanity_common import ReviewError
from common.sanity_db import Database, json_value, stable_uuid
from common.sanity_parsers import ParserRegistry
from common.sanity_submission_https import validate_https_config
from common.sanity_submission_intake import require_internal_peer
from devtools.sanity_submission_fixture import fixture
from oa.dashboard_api import create_app as oa_app
from oa.sanity_access import AccessStore
from oa.sanity_production_workspace import ProductionWorkspaceStore
from oa.submission_receiver import SourceReceiver
from oa.sanity_auth import AuthError, LocalUser, SSOIdentityProvider, UserDirectory, VerifiedIdentity
from tests.support import PgTestCase, ROOT
from vwp.submission_cli import SubmissionClient
from vwp.submission_https import HttpsTransport
from vwp_bridge.submission_api import create_app
from vwp_bridge.submission_worker import SubmissionWorker


ISSUER = "https://synthetic-idp.example.test/entity"

USERS = ("synthetic.admin", "synthetic.owner", "synthetic.top", "synthetic.other")

ADMIN, OWNER, TOP, OTHER = USERS

STAMP = "2026-10-22T00:00:00.000000Z"

class VerifiedFixtureProvider(SSOIdentityProvider):
    """Fixed test credentials, without authorization roles or header identity."""
    def authenticate(self, request):
        token = request.cookies.get("synthetic_session")
        for index, user in enumerate(USERS):
            if token == "synthetic-verified-session-" + str(index):
                return VerifiedIdentity(ISSUER, user)
        raise AuthError("UNAUTHORIZED")

class FixtureDirectory(UserDirectory):
    def resolve(self, identity):
        if identity.issuer == ISSUER and identity.subject in USERS:
            return LocalUser(identity.subject, "합성 표시 이름 " + identity.subject)
        return None


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
    def post(self, user, path, body):
        return self.clients[user].post(path, json=body, headers={"X-Sanity-Request": "1"})

    def success(self, response):
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def register(self):
        for project in ("project-a", "project-b"):
            self.success(self.post(ADMIN, "/api/admin/projects", {
                "request_id": str(uuid.uuid4()), "project_id": project, "name": "합성 " + project}))
        for project, block in (("project-a", "block-a1"), ("project-a", "block-a2"), ("project-b", "block-b1")):
            self.success(self.post(ADMIN, "/api/admin/projects/" + project + "/blocks", {
                "request_id": str(uuid.uuid4()), "block_id": block, "name": "합성 " + block}))

    def grant(self, user, role, project="project-a", block=None):
        request_id = str(uuid.uuid4())
        value = self.success(self.post(user, "/api/access/requests", {
            "request_id": request_id, "role": role, "project_id": project, "block_id": block,
            "reason": "합성 업무 권한 요청"}))
        self.success(self.post(ADMIN, "/api/admin/requests/" + request_id + "/decision", {
            "command_id": str(uuid.uuid4()), "decision": "APPROVE", "reason": "합성 승인",
            "expected_revision": value["revision"]}))
        return request_id

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
            return SubmissionWorker(db,self.config,self.no_identity,
                                    getattr(self,'parser_registry',None) or ParserRegistry()).once()

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

    def collection_input(self, body=None):
        from tests.test_collection_only import collecting_registry, prepare_collection
        self.parser_registry=collecting_registry()
        return prepare_collection(self.f,body)

    def collection_report(self):
        return self.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT' "
            "AND payload->>'evaluation_mode'='none'").fetchone()[0]


    def test_collection_only_cannot_bypass_numeric_policy_or_publish_an_analysis(self):
        unit=self.collection_input();unit['evaluation']={}
        self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
        with self.assertRaises(ReviewError) as error:self.bridge()
        self.assertEqual('PARSER_SCOPE_MISMATCH',error.exception.code)
        self.assertEqual(0,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type IN ('RUN','SOURCE_OUTBOX')").fetchone()[0])
        self.assertEqual(0,self.report_count())


    def test_only_current_owner_can_confirm_and_digest_revision_and_request_are_bound(self):
        sid=self.submit();body=self.body(sid)
        self.assertEqual(200,self.clients[TOP].get('/api/workbench/blocks/block-a1/intake/'+sid).status_code)
        for user in (OTHER,ADMIN):self.assertIn(self.decide(sid,body,user).status_code,(403,404))
        for change in ({'confirmed_scope':False},{'manifest_sha256':'0'*64},{'content_sha256':'0'*64},
                       {'scope_sha256':'0'*64},{'expected_revision':body['expected_revision']+1}):
            self.assertEqual(409,self.decide(sid,{**body,**change}).status_code)
        self.assertEqual(400,self.decide(sid,{**body,'user_id':OWNER}).status_code)
        self.assertEqual(403,self.clients[OWNER].post('/api/workbench/blocks/block-a1/intake/'+sid,json=body).status_code)
        self.assertEqual(0,self.report_count())
        first=self.success(self.decide(sid,body));second=self.success(self.decide(sid,body))
        self.assertEqual(first,second);self.assertEqual(2,self.report_count())
        peer_retry=self.decide(sid,body,user=TOP)
        self.assertEqual(409,peer_retry.status_code)
        self.assertEqual('REQUEST_CONFLICT',peer_retry.json()['code'])
        self.assertEqual(409,self.decide(sid,{**body,'request_id':str(uuid.uuid4())}).status_code)
        self.assertEqual(409,self.decide(sid,{**body,'reason':'different'}).status_code)
