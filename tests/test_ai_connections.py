"""Administrator connection catalogs and isolated per-user/per-connection credentials."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import Request
from fastapi.testclient import TestClient
import httpx

from common.sanity_common import ReviewError, digest
from common.sanity_db import Database, json_value
from oa import ai_connections_api
from oa.access_api import require_mutation_request
from oa.dashboard_api import authorized_actor, create_app
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_connections import ConnectionStore, validate_config
from oa.sanity_ai_jobs import AIJobService, run_once
from oa.sanity_ai_model import OAAdvisor, OASettings
from tests.support import PgTestCase
from tests.test_ai_jobs import SyntheticAIWorkspace
from tests.test_ai_model import environment, chat
from common.sanity_integration_examples import clustering_example


def configuration(**changes):
    return {"label":"합성 AI 연결", "enabled":True, "endpoint":"https://one.example.test/v1/chat/completions",
        "fallback_endpoint":"", "api":"chat_completions", "auth_mode":"bearer", "model":"model-a",
        "models":["model-a","model-b"], "issuance_url":"https://identity.example.test/one",
        "json_mode":False, "timeout":120, "max_input_bytes":1048576, "max_output_bytes":2097152,
        "client_name":"", "client_version":"", "session_mode":"disabled", "session_id":"",
        "batch":BatchSettings().public(), **changes}


class ConnectionConfigurationTests(unittest.TestCase):
    def test_bearer_and_x_client_only_do_not_require_gateway_fields(self):
        for mode in ("bearer","x_client_api_key"):
            self.assertEqual(mode, validate_config(configuration(auth_mode=mode))["auth_mode"])
        with self.assertRaises(ReviewError):validate_config(configuration(auth_mode="bearer_x_client"))

    def test_invalid_settings_cannot_add_auth_headers_urls_or_files(self):
        for changes in ({"auth_mode":"arbitrary"}, {"endpoint":"http://one.example.test/api"},
                        {"endpoint":"https://one.example.test/api?private=secret"}, {"models":[]},
                        {"models":["model-a","model-a"]}, {"model":"not-in-catalog"},
                        {"issuance_url":"javascript:alert(1)"}, {"timeout":True},
                        {"client_name":"unexpected-gateway"}, {"extra_headers":{"Authorization":"bad"}},
                        {"batch":{**BatchSettings().public(),"tokenizer_file":"/arbitrary/private/file"}}):
            with self.subTest(changes=changes), self.assertRaises(ReviewError):validate_config(configuration(**changes))

    def test_each_auth_mode_sends_only_its_own_headers_and_gateway_body(self):
        packet = clustering_example()
        for mode in ("bearer","bearer_x_client","x_client_api_key"):
            seen=[]
            def respond(request):
                seen.append(request)
                return httpx.Response(200,json=chat(packet["reply"]))
            settings=OASettings.from_env(environment())
            extras={"client_api_key":"synthetic-shared-client-key","client_name":"synthetic-app",
                    "client_version":"1","session_mode":"per_request"} if mode=="bearer_x_client" else {}
            advisor=OAAdvisor(replace(settings,auth_mode=mode,**extras),transport=httpx.MockTransport(respond))
            advisor.cluster(packet["context"],packet["cases"],packet["identity_fields"])
            self.assertEqual(1,len(seen));request=seen[0];body=json.loads(request.content)
            self.assertEqual(mode!="x_client_api_key","authorization" in request.headers)
            self.assertEqual(mode!="bearer","x-client-api-key" in request.headers)
            self.assertEqual(mode=="bearer_x_client","extraData" in body)


class ConnectionDatabaseTests(PgTestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.settings={"SANITY_OA_AI_TOKEN_STORE_SECRET":Fernet.generate_key().decode()}
        private=patch("oa.sanity_ai_connections.load_environment",return_value=self.settings)
        private.start();self.addCleanup(private.stop)
        self.wire=[]
        def respond(request):
            self.wire.append(request)
            response=self.c.respond(request)
            value=json.loads(response.content);value["model"]=json.loads(request.content)["model"]
            return httpx.Response(200,json=value)
        self.transport=httpx.MockTransport(respond)
        self.c.advisor=OAAdvisor(self.c.advisor.settings,transport=self.transport)

    @contextmanager
    def store(self):
        with self.c.service() as service:
            def admin(actor):
                if actor.user_id!="owner":raise ReviewError("FORBIDDEN","Administrator required")
            access=SimpleNamespace(db=service.db,_admin=admin)
            actor=SimpleNamespace(user_id="owner",auth_method="sso",access=access)
            yield ConnectionStore(service.db,self.settings),actor,service

    def create(self, config=None, key=None):
        connection=str(uuid4())
        with self.store() as (store,actor,_):store.save(actor,connection,config or configuration(),0,key)
        return connection

    def edit(self, connection, **changes):
        with self.store() as (store,actor,_):
            value=store.get(connection,enabled=False)
            store.save(actor,connection,{**value["config"],**changes},value["revision"])

    def token(self, connection, token="synthetic-personal-key", *, user="owner", revision=0):
        with self.store() as (store,_,_):return store.tokens(store.get(connection,enabled=False)).save(user,token,revision)

    def finish(self):
        for _ in range(30):
            result=self.c.run_job()
            if result is None or result["status"] in ("COMPLETED","STALE","FAILED","UNKNOWN","CANCELLED"):return result
        self.fail("Connection batch did not terminate")

    def test_only_admin_can_create_and_revisions_are_checked(self):
        connection=str(uuid4())
        with self.store() as (store,actor,_):
            denied=SimpleNamespace(user_id="other",auth_method="sso",access=actor.access)
            with self.assertRaises(ReviewError):store.save(denied,connection,configuration(),0)
            self.assertEqual([],store.rows())
            self.assertEqual(1,store.save(actor,connection,configuration(),0)["revision"])
            with self.assertRaises(ReviewError):store.save(actor,connection,configuration(),0)
            self.assertEqual(2,store.save(actor,connection,configuration(label="새 이름"),1)["revision"])

    def test_shared_client_key_is_encrypted_and_never_in_admin_or_user_output(self):
        key="synthetic-shared-client-key"
        cfg=configuration(auth_mode="bearer_x_client",client_name="synthetic-app",client_version="1",session_mode="per_request")
        connection=self.create(cfg,key)
        with self.store() as (store,_,_):
            value=store.get(connection)
            self.assertTrue(store.admin_public(value)["client_key_registered"])
            self.assertEqual(key,store.advisor(connection).settings.client_api_key)
            rendered=json.dumps([store.admin_public(value),store.user_connections("owner")])
            self.assertNotIn(key,rendered)
            self.assertNotIn(cfg["endpoint"],json.dumps(store.user_connections("owner")))
        rows=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG'").fetchall()
        self.assertNotIn(key,json.dumps(rows,default=str))

    def test_bearer_rejects_a_shared_client_key_instead_of_sending_it(self):
        with self.store() as (store,actor,_),self.assertRaises(ReviewError):
            store.save(actor,str(uuid4()),configuration(),0,"unexpected-client-key")

    def test_user_and_connection_tokens_are_separate_and_plaintext_is_not_stored(self):
        one,two=self.create(),self.create(configuration(label="다른 연결"))
        self.token(one,"person-one-connection-one")
        self.token(two,"person-one-connection-two")
        self.token(one,"person-two-connection-one",user="other")
        with self.store() as (store,_,_):
            self.assertEqual(("person-one-connection-one",1),store.tokens(store.get(one)).get_token("owner"))
            self.assertEqual(("person-one-connection-two",1),store.tokens(store.get(two)).get_token("owner"))
            self.assertEqual(("person-two-connection-one",1),store.tokens(store.get(one)).get_token("other"))
            self.assertFalse(store.tokens(store.get(two)).status("other")["registered"])
            self.assertEqual(2,len(store.user_connections("owner")))
        rendered=json.dumps(self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG'").fetchall(),default=str)
        for token in ("person-one-connection-one","person-one-connection-two","person-two-connection-one"):
            self.assertNotIn(token,rendered)

    def test_ciphertext_cannot_be_moved_to_another_connection_or_user(self):
        one,two=self.create(),self.create()
        self.token(one);self.token(two);self.token(one,user="other")
        original=self.c.admin_query("SELECT payload->>'ciphertext' FROM {oa} WHERE payload->>'namespace'='oa-ai-user-token-v2' "
            "AND payload->>'connection_id'=%s AND payload->>'user_id'='owner'",(one,)).fetchone()[0]
        for connection,user in ((two,"owner"),(one,"other")):
            self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{ciphertext}}',%s) "
                "WHERE payload->>'namespace'='oa-ai-user-token-v2' AND payload->>'connection_id'=%s AND payload->>'user_id'=%s",
                (json_value(original),connection,user))
            with self.store() as (store,_,_),self.assertRaises(ReviewError) as caught:
                store.tokens(store.get(connection)).get_token(user)
            self.assertEqual("AI_TOKEN_UNAVAILABLE",caught.exception.code)

    def test_endpoint_auth_fallback_and_issuance_changes_require_personal_reregistration(self):
        for changes in ({"endpoint":"https://changed.example.test/v1/chat/completions"},
                        {"auth_mode":"x_client_api_key"},
                        {"fallback_endpoint":"https://backup.example.test/v1/chat/completions"},
                        {"issuance_url":"https://identity.example.test/changed"}):
            connection=self.create();self.token(connection);self.edit(connection,**changes)
            with self.store() as (store,_,_):
                tokens=store.tokens(store.get(connection))
                self.assertTrue(tokens.status("owner")["connection_changed"])
                with self.assertRaises(ReviewError) as caught:tokens.get_token("owner")
                self.assertEqual("AI_CREDENTIAL_SCOPE_CHANGED",caught.exception.code)
                self.assertEqual(2,tokens.save("owner","new-personal-key",1)["revision"])
                self.assertEqual(("new-personal-key",2),tokens.get_token("owner"))
        self.assertEqual([],self.wire)

    def test_model_only_edit_keeps_personal_token_but_stales_a_queued_job(self):
        connection=self.create();self.token(connection)
        self.c.enqueue(connection_id=connection,model_id="model-a")
        self.edit(connection,models=["model-a","model-b","model-c"])
        with self.store() as (store,_,_):
            tokens=store.tokens(store.get(connection))
            self.assertFalse(tokens.status("owner")["connection_changed"])
            self.assertEqual(("synthetic-personal-key",1),tokens.get_token("owner"))
        result=self.c.run_job()
        self.assertEqual(("STALE","AI_CONFIG_CHANGED"),(result["status"],result["error"]))
        self.assertEqual([],self.wire)

    def test_gateway_model_edit_retains_credentials_but_client_key_rotation_requires_reregistration(self):
        cfg=configuration(auth_mode="bearer_x_client",client_name="synthetic-app",client_version="1",session_mode="per_request")
        connection=self.create(cfg,"synthetic-original-client-key");self.token(connection)
        self.edit(connection,models=["model-a","model-b","model-c"])
        with self.store() as (store,actor,_):
            value=store.get(connection)
            self.assertFalse(store.tokens(value).status("owner")["connection_changed"])
            store.save(actor,connection,value["config"],value["revision"],"synthetic-rotated-client-key")
        with self.store() as (store,_,_):
            self.assertTrue(store.tokens(store.get(connection)).status("owner")["connection_changed"])
            with self.assertRaises(ReviewError):store.tokens(store.get(connection)).get_token("owner")

    def test_each_selected_connection_uses_its_endpoint_model_and_authentication(self):
        for index,mode in enumerate(("bearer","bearer_x_client","x_client_api_key")):
            cfg=configuration(auth_mode=mode,endpoint=f"https://model-{index}.example.test/v1/chat/completions")
            key=None
            if mode=="bearer_x_client":
                cfg.update(client_name="synthetic-app",client_version="1",session_mode="per_request")
                key="synthetic-shared-client-key"
            connection=self.create(cfg,key);self.token(connection,f"personal-key-{index}")
            queued=self.c.enqueue(expected_revision=index,connection_id=connection,model_id="model-b")
            self.assertEqual(connection,queued["connection_id"])
            result=self.c.run_job();self.assertEqual("COMPLETED",result["status"],result)
            request=self.wire[-1];body=json.loads(request.content)
            self.assertEqual(cfg["endpoint"],str(request.url));self.assertEqual("model-b",body["model"])
            self.assertEqual(mode!="x_client_api_key","authorization" in request.headers)
            if mode=="bearer_x_client":self.assertEqual(key,request.headers["x-client-api-key"])
            elif mode=="x_client_api_key":self.assertEqual(f"personal-key-{index}",request.headers["x-client-api-key"])
            else:self.assertNotIn("x-client-api-key",request.headers)
            self.assertEqual(mode=="bearer_x_client","extraData" in body)

    def test_unrelated_connection_token_rotation_does_not_change_a_running_job(self):
        one,two=self.create(),self.create();self.token(one);self.token(two)
        self.c.enqueue(connection_id=one)
        self.c.callback=lambda _:self.token(two,"rotated-other-key",revision=1) and None
        self.assertEqual("COMPLETED",self.c.run_job()["status"])
        self.assertEqual("Bearer synthetic-personal-key",self.wire[0].headers["authorization"])

    def test_current_connection_change_during_http_prevents_publication(self):
        connection=self.create();self.token(connection);self.c.enqueue(connection_id=connection)
        self.c.callback=lambda _:self.edit(connection,endpoint="https://changed.example.test/v1/chat/completions")
        result=self.c.run_job()
        self.assertEqual(("STALE","AI_CONFIG_CHANGED"),(result["status"],result["error"]))
        self.assertEqual(1,len(self.wire))
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_CLUSTER_EVENT'").fetchone()[0])

    def test_disabled_connection_cannot_dispatch_and_user_can_remove_its_token(self):
        connection=self.create();self.token(connection);self.c.enqueue(connection_id=connection)
        self.edit(connection,enabled=False)
        self.assertEqual("STALE",self.c.run_job()["status"])
        with self.store() as (store,_,_):
            self.assertEqual([],store.user_connections("owner"))
            self.assertFalse(store.user_connections("owner",include_disabled=True)[0]["enabled"])
            self.assertFalse(store.tokens(store.get(connection,enabled=False)).save("owner",None,1)["registered"])
        self.assertEqual([],self.wire)

    def test_batch_resume_keeps_connection_and_publishes_its_provenance(self):
        from tests.test_ai_batches import batch_settings
        connection=self.create(configuration(batch=batch_settings(max_records=1).public()))
        self.token(connection);job=self.c.enqueue(connection_id=connection,model_id="model-b")
        self.assertEqual("RUNNING",self.c.run_job()["status"])
        self.c.advisor=OAAdvisor(self.c.advisor.settings,transport=self.transport)
        self.assertEqual("COMPLETED",self.finish()["status"])
        self.assertEqual(3,len(self.wire));self.assertIsNone(self.c.run_job())
        with self.c.service() as service:
            from oa.sanity_ai_batch_jobs import Ledger
            value=Ledger(service,service._load(job["id"])).get("final")[0]["metadata"]
            self.assertEqual(connection,value["connection_id"])
            self.assertEqual(1,value["connection_revision"])
            self.assertEqual("model-b",value["requested_model"])

    def test_admin_connections_work_without_any_legacy_endpoint_or_token(self):
        connection=self.create();self.token(connection)
        with patch("oa.sanity_ai_connections.OAAdvisor",side_effect=lambda settings,transport=None:OAAdvisor(settings,transport=self.transport)):
            with Database("oa") as db,db.transaction():
                service=AIJobService(db,SyntheticAIWorkspace(db))
                self.assertIsNone(service.advisor)
                self.assertEqual(connection,service.capability(*self.c.path)["connection_id"])
                service.enqueue(*self.c.path,expected_revision=0,request_id=uuid4(),connection_id=connection)
            result=run_once(lambda:Database("oa"),SyntheticAIWorkspace,None,criteria_provider=lambda _:self.c.criteria)
            self.assertEqual("COMPLETED",result["status"],result)
        self.assertEqual(1,len(self.wire))


class ConnectionApiTests(unittest.TestCase):
    def setUp(self):
        self.connection=str(uuid4())
        self.actor=SimpleNamespace(user_id="owner",auth_method="sso",access=SimpleNamespace(db=object()))
        def admin(actor):
            if actor.user_id!="admin":raise ReviewError("FORBIDDEN","Administrator required")
        self.actor.access._admin=admin
        self.store=Mock();self.store.rows.return_value=[]
        self.store.user_connections.return_value=[]
        self.store.get.return_value={"config":{"issuance_url":"https://identity.example.test/token"}}
        self.tokens=Mock();self.tokens.status.return_value={"registered":False,"revision":0}
        self.tokens.save.return_value={"registered":True,"revision":1}
        self.store.tokens.return_value=self.tokens
        factory=patch.object(ai_connections_api,"ConnectionStore",return_value=self.store)
        factory.start();self.addCleanup(factory.stop)
        app=create_app(auth_mode="sso")
        def actor(request:Request):
            if request.method=="POST":require_mutation_request(request)
            return self.actor
        app.dependency_overrides[authorized_actor]=actor
        self.client=TestClient(app,base_url="http://127.0.0.1");self.addCleanup(self.client.close)
        self.headers={"X-Sanity-Request":"1","Origin":"http://127.0.0.1","Sec-Fetch-Site":"same-origin"}

    def test_non_admin_cannot_read_or_write_connection_configuration(self):
        response=self.client.get('/api/admin/ai/connections');self.assertEqual(403,response.status_code)
        response=self.client.post('/api/admin/ai/connections/'+self.connection,headers=self.headers,
            json={"config":configuration(),"expected_revision":0})
        self.assertEqual(403,response.status_code);self.store.save.assert_not_called()

    def test_admin_configuration_is_forwarded_without_exposing_client_key(self):
        self.actor.user_id="admin";self.store.save.return_value={"id":self.connection,"client_key_registered":True}
        response=self.client.post('/api/admin/ai/connections/'+self.connection,headers=self.headers,
            json={"config":configuration(auth_mode="bearer_x_client",client_name="synthetic-app",client_version="1",session_mode="per_request"),"expected_revision":0,"client_api_key":"synthetic-client-key"})
        self.assertEqual(200,response.status_code)
        self.assertNotIn("synthetic-client-key",response.text)
        self.assertEqual("synthetic-client-key",self.store.save.call_args.args[-1])

    def test_personal_tokens_are_bound_to_current_actor_and_selected_connection(self):
        path='/api/ai/connections/'+self.connection+'/credentials'
        self.assertEqual(200,self.client.get(path).status_code)
        self.store.get.assert_called_with(self.connection,enabled=False)
        self.tokens.status.assert_called_once_with('owner')
        response=self.client.post(path,headers=self.headers,json={"token":"synthetic-personal-key","expected_revision":0})
        self.assertEqual(200,response.status_code);self.assertNotIn('synthetic-personal-key',response.text)
        self.tokens.save.assert_called_with('owner','synthetic-personal-key',0)
        self.client.post(path+'/remove',headers=self.headers,json={"expected_revision":1})
        self.tokens.save.assert_called_with('owner',None,1)

    def test_spoofed_owners_invalid_revisions_and_missing_csrf_are_rejected(self):
        path='/api/ai/connections/'+self.connection+'/credentials'
        for extra in ({"user_id":"other"},{"connection_id":str(uuid4())},{"expected_revision":True}):
            result=self.client.post(path,headers=self.headers,json={"token":"synthetic-personal-key","expected_revision":0,**extra})
            self.assertEqual(400,result.status_code);self.assertNotIn('synthetic-personal-key',result.text)
        self.assertEqual(403,self.client.post(path,json={"token":"synthetic-personal-key","expected_revision":0}).status_code)
        self.tokens.save.assert_not_called()

    def test_local_token_identity_cannot_use_the_catalog_or_personal_slots(self):
        self.actor.auth_method='local_token'
        for path in ('/api/ai/connections','/api/ai/connections/'+self.connection+'/credentials','/api/admin/ai/connections'):
            self.assertEqual(403,self.client.get(path).status_code)

    def test_disabled_connection_credentials_can_be_removed_without_using_the_connection(self):
        path='/api/ai/connections/'+self.connection+'/credentials/remove'
        self.assertEqual(200,self.client.post(path,headers=self.headers,json={"expected_revision":0}).status_code)
        self.store.get.assert_called_with(self.connection,enabled=False)
        self.tokens.save.assert_called_with('owner',None,0)
