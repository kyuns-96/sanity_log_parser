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

    def test_collection_only_runs_owner_intake_ai_and_human_review_without_changing_source(self):
        import httpx
        from oa.sanity_ai_jobs import AIJobService, run_once
        from oa.sanity_ai_model import OAAdvisor
        self.collection_input()
        sid=self.submit()
        self.assertEqual('AWAITING_CONFIRMATION',self.detail(sid)['delivery_state'])
        self.assertEqual(0,self.report_count())
        self.assertEqual(403,self.decide(sid,user=TOP).status_code)
        self.success(self.decide(sid));self.bridge()
        self.assertEqual('READY',self.client.status(sid)['review_readiness'])
        original=self.collection_report()
        self.assertEqual(('COMPLETE','UNKNOWN',2,True),
            (original['parse_status'],original['check_status'],original['not_evaluated_count'],original['analysis_ready']))
        base='/api/workbench/blocks/block-a1/versions/'+original['version_id']+'/reports/'+original['id']
        records=self.success(self.clients[OWNER].get(base+'/clustering/records?expected_revision=0'))['items']
        self.assertEqual(2,len(records))
        self.assertTrue(all(r['evaluation_status']=='NOT_PERFORMED' and not r['reasons'] for r in records))
        self.assertEqual(['clock fact A','clock fact B'],[r['fields']['message'] for r in records])
        self.assertTrue(all('status' not in r['fields'] for r in records))
        packets=[]
        def model(request):
            body=json.loads(request.content);packet=json.loads(body['messages'][1]['content']);packets.append(packet)
            context=packet['context']
            if 'identity_fields' in packet:
                ids=[r['id'] for r in context['report']['records']]
                reply={'groups':[{'id':'facts','label':'클록 관측 사실','record_ids':ids}],
                    'reason':'같은 검사에서 수집한 사실을 검토한다.','reference_ids':[],
                    'group_rationales':[{'group_id':'facts','reason':'같은 클록 검사 범위의 두 사실을 함께 검토한다.',
                        'evidence_ids':ids,'reference_groups':[]}]}
            else:
                reply={'schema_version':2,'conclusion':'PASS','reason':'이번 두 사실을 확인된 기준과 비교했다.',
                    'evidence_ids':[r['id'] for r in context['group_evidence']],
                    'reference_ids':[],'criterion_ids':['synthetic-observation']}
            return httpx.Response(200,json={'model':'synthetic-model','choices':[{'finish_reason':'stop',
                'message':{'role':'assistant','content':json.dumps(reply,ensure_ascii=False)}}]})
        advisor=OAAdvisor({'SANITY_OA_AI_ENABLED':'true','SANITY_OA_AI_URL':'https://synthetic.invalid/chat',
            'SANITY_OA_AI_TOKEN':'synthetic-collection-token','SANITY_OA_AI_MODEL':'synthetic-model'},
            transport=httpx.MockTransport(model))
        criterion={'id':'synthetic-observation','version':'1',
            'scope':{**{key:original[key] for key in ('project_id','block_id','item_id','family','parser','rule_version')},
                     'layout':'POST'},'text':'합성 클록 관측 사실이 모두 확인되면 PASS를 제안한다.'}
        criteria_path=self.root/'collection-criteria.json'
        criteria_path.write_text(json.dumps({'schema_version':1,'criteria':[criterion]},ensure_ascii=False))
        workspace=lambda db:ProductionWorkspaceStore(db,AccessStore(db,ADMIN))
        factory=lambda scope:AIJobService(scope.db,scope.ws,advisor=advisor)
        with patch.dict(os.environ,{'SANITY_OA_AI_CRITERIA_FILE':str(criteria_path)}), \
             patch('oa.ai_workbench_api.jobs',side_effect=factory):
            response=self.post(OWNER,base+'/clustering/ai',{'request_id':str(uuid.uuid4()),'expected_revision':0})
            self.assertEqual(202,response.status_code,response.text)
            self.assertEqual('COMPLETED',run_once(lambda:Database('oa'),workspace,advisor)['status'])
            view=self.success(self.clients[OWNER].get(base+'/workbench'))
            self.assertFalse(view['current']['confirmed'])
            self.assertFalse(view['summary']['complete'])
            confirmed=self.success(self.post(OWNER,base+'/clustering?compact=true',{
                'action':'CONFIRM','expected_revision':1,'request_id':str(uuid.uuid4()),'reason':'사람이 두 사실과 그룹 근거를 확인했다.'}))
            review=base+'/groups/facts/review'
            response=self.post(OWNER,review+'/ai',{'expected_revision':0,'expected_cluster_revision':confirmed['revision'],
                                                 'request_id':str(uuid.uuid4())})
            self.assertEqual(202,response.status_code,response.text)
            self.assertEqual('COMPLETED',run_once(lambda:Database('oa'),workspace,advisor)['status'])
            value=self.success(self.clients[OWNER].get(review+'?compact=true'))
            self.assertIsNone(value['current'])
            self.assertEqual('PASS',value['proposal']['conclusion'])
            self.assertEqual(2,len(value['proposal']['evidence_ids']))
            self.success(self.post(OWNER,review+'?compact=true',{'expected_revision':0,
                'expected_cluster_revision':confirmed['revision'],'request_id':str(uuid.uuid4()),
                'conclusion':'PASS','reason':'사람이 현재 원본과 기준을 확인했다.','proposal_id':value['proposal']['id']}))
        self.assertEqual(2,len(packets))
        self.assertEqual('none',packets[0]['context']['report']['evaluation_mode'])
        self.assertEqual(2,len(packets[1]['context']['group_evidence']))
        self.assertEqual(original,self.collection_report())
        private=self.admin_query("SELECT payload FROM {vwp} WHERE record_type='EVIDENCE' "
            "AND payload->>'evaluation_status'='NOT_PERFORMED'").fetchall()
        self.assertEqual(2,len(private))
        self.assertTrue(all(row[0]['check_status']=='UNKNOWN' for row in private))

    def test_collection_only_cannot_bypass_numeric_policy_or_publish_an_analysis(self):
        unit=self.collection_input();unit['evaluation']={}
        self.client.submit(self.f['target'],confirm=lambda _:True,preview=lambda _:None)
        with self.assertRaises(ReviewError) as error:self.bridge()
        self.assertEqual('PARSER_SCOPE_MISMATCH',error.exception.code)
        self.assertEqual(0,self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type IN ('RUN','SOURCE_OUTBOX')").fetchone()[0])
        self.assertEqual(0,self.report_count())

    def test_collection_only_truncated_report_stays_blocked_after_owner_confirmation(self):
        self.collection_input('SYNTHETIC REPORT v1\nA|P1|M1|C1|INFO|fact\n')
        sid=self.submit();self.success(self.decide(sid));self.bridge()
        report=self.collection_report()
        self.assertEqual('PARTIAL',report['parse_status']);self.assertFalse(report['analysis_ready'])
        self.assertEqual('BLOCKED',self.client.status(sid)['review_readiness'])
        self.assertIn('MISSING_END_MARKER',report['blockers'])
        base='/api/workbench/blocks/block-a1/versions/'+report['version_id']+'/reports/'+report['id']
        self.assertFalse(self.success(self.clients[OWNER].get(base+'/workbench'))['ready'])

    def test_collection_only_receiver_rejects_scope_producer_and_count_tampering(self):
        self.collection_input();sid=self.submit()
        row=self.admin_query("SELECT state FROM {oa} WHERE record_type='WS_SUBMISSION' AND workspace_key=%s",(sid,)).fetchone()[0]
        manifest=row['manifest']
        with Database('oa') as db:
            receiver=self.receiver(db)
            for mutation in ('producer','contract','policy','mode','count','evaluated_boolean','result_policy'):
                changed=copy.deepcopy(manifest);v=changed['reports'][1];u=changed['plan']['inventory']['units'][1]
                if mutation=='producer':changed['producer']='postgresql-source-2.1'
                elif mutation=='contract':v['parser_contract']=[]
                elif mutation=='policy':u['evaluation']={}
                elif mutation=='mode':v['result'].pop('evaluation_mode')
                elif mutation=='evaluated_boolean':v['result']['evaluated_count']=False
                elif mutation=='result_policy':v['result']['evaluation']={}
                else:v['result']['not_evaluated_count']=0
                with self.subTest(mutation=mutation),self.assertRaises(ReviewError):receiver.header(changed,u,v)
        # Coherent manifest/commit hashes cannot hide a disagreement with the actual part markers.
        changed=copy.deepcopy(row);result=changed['manifest']['reports'][1]['result']
        result.update(parse_status='PARTIAL',blockers=['SYNTHETIC_BLOCKER'],not_evaluated_count=0)
        changed['commit']['manifest_sha256']=digest(changed['manifest'])
        self.admin_query("UPDATE {oa} SET state=%s WHERE record_type='WS_SUBMISSION' AND workspace_key=%s",(json_value(changed),sid))
        response=self.decide(sid)
        self.assertEqual(400,response.status_code,response.text)
        self.assertEqual('SOURCE_EVALUATION_MISMATCH',response.json()['code'])
        self.assertEqual(0,self.report_count())
        self.assertIsNone(self.detail(sid)['decision'])

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
