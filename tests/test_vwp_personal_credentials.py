"""Synthetic VWP personal credentials, immutable pins and private transport."""
from datetime import timedelta
import json
from types import SimpleNamespace
import unittest
from uuid import uuid4
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.sanity_ai_execution import executing_on
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import json_value
from common.sanity_vwp_ai_profiles import ProfileStore
from oa.sanity_ai_jobs import _now
from oa.sanity_ai_model import OASettings
from oa.sanity_vwp_inference import LocalClient
from tests.support import PgTestCase
from tests.test_vwp_inference import body, profile


class VwpPersonalProfileTests(unittest.TestCase):
    def test_prepared_input_checks_personal_key_and_all_private_profile_values(self):
        personal='synthetic-personal-"key'
        private=profile(auth_mode='bearer_x_client',client_api_key='synthetic-shared-client',
            client_name='synthetic-client',client_version='1',session_mode='per_request',
            fallback_endpoint='https://fallback.example.invalid/v1')
        store=ProfileStore([private]);seen=[]
        settings=OASettings(enabled=True,endpoint='https://vwp.invalid/inference',token_source='user',token='',
            model='synthetic-model',models=('synthetic-model',),timeout=120,max_input_bytes=1048576,max_output_bytes=2097152)
        client=LocalClient(lambda:store,transport=httpx.MockTransport(lambda request:seen.append(request)))
        for secret in (personal,private['client_api_key'],private['endpoint'],private['fallback_endpoint']):
            packet=body(extraData={'clientName':private['client_name'],'clientVersion':'1','sessionId':str(uuid4())})
            packet['messages'][1]['content']=json.dumps({'raw':secret})
            with self.subTest(secret_kind=('personal' if secret==personal else 'profile')),executing_on('vwp'):
                with self.assertRaises(ReviewError) as error:
                    client.call(canonical_json(packet),store.public[private['id']],settings,{'actor_id':'owner'},personal_token=personal)
                self.assertEqual('SENSITIVE_CONTENT',error.exception.code)
                self.assertIs(error.exception.model_called,False)
        private={**private,'client_name':personal};store=ProfileStore([private])
        client=LocalClient(lambda:store,transport=httpx.MockTransport(lambda request:seen.append(request)))
        packet=body(extraData={'clientName':personal,'clientVersion':'1','sessionId':str(uuid4())})
        with executing_on('vwp'),self.assertRaises(ReviewError) as error:
            client.call(canonical_json(packet),store.public[private['id']],settings,{'actor_id':'owner'},personal_token=personal)
        self.assertEqual('SENSITIVE_CONTENT',error.exception.code)
        self.assertIs(error.exception.model_called,False)
        self.assertEqual([],seen)

    def test_profiles_reject_shared_token_and_expose_only_personal_auth_contract(self):
        for mode in ("bearer", "x_client_api_key", "bearer_x_client"):
            extra = {"client_api_key":"synthetic-gateway-key", "client_name":"synthetic-client",
                     "client_version":"1", "session_mode":"per_request"} if mode == "bearer_x_client" else {}
            private=profile(auth_mode=mode,**extra)
            public=ProfileStore([private]).public[private["id"]]
            self.assertEqual(mode,public["auth_mode"])
            self.assertEqual("user",public["token_source"])
            self.assertNotIn(private["endpoint"],json.dumps(public))
            self.assertNotIn("synthetic-gateway-key",json.dumps(public))
            for changes in ({"token":"synthetic-old-shared-key"}, {"token_source":"server"}):
                with self.subTest(mode=mode,changes=changes),self.assertRaises(ReviewError):
                    ProfileStore([{**private,**changes}])
            self.assertIn(private["id"],ProfileStore([{**private,"token":""}]).public)
            without_source={k:v for k,v in private.items() if k!="token_source"}
            self.assertEqual("user",ProfileStore([without_source]).public[private["id"]]["token_source"])

    def test_personal_key_routes_only_to_the_approved_header(self):
        for mode in ("bearer", "x_client_api_key", "bearer_x_client"):
            extra = {"client_api_key":"synthetic-gateway-key", "client_name":"synthetic-client",
                     "client_version":"1", "session_mode":"per_request"} if mode == "bearer_x_client" else {}
            private=profile(auth_mode=mode,**extra);store=ProfileStore([private]);seen=[]
            packet=body()
            if mode=="bearer_x_client":packet["extraData"]={"clientName":"synthetic-client","clientVersion":"1","sessionId":str(uuid4())}
            settings=OASettings(enabled=True,endpoint="https://vwp.invalid/inference",token_source="user",token="",
                model="synthetic-model",models=("synthetic-model",),timeout=120,max_input_bytes=1048576,max_output_bytes=2097152)
            def respond(request):
                seen.append(request)
                return httpx.Response(200,json={"model":"synthetic-model","choices":[]})
            client=LocalClient(lambda:store,transport=httpx.MockTransport(respond))
            binding={"actor_id":"owner"}
            with executing_on("vwp"):
                client.call(canonical_json(packet),store.public[private["id"]],settings,binding,personal_token="synthetic-personal-key")
            headers=seen[0].headers
            if mode=="x_client_api_key":
                self.assertNotIn("authorization",headers);self.assertEqual("synthetic-personal-key",headers["x-client-api-key"])
            else:
                self.assertEqual("Bearer synthetic-personal-key",headers["authorization"])
                if mode=="bearer_x_client":self.assertEqual("synthetic-gateway-key",headers["x-client-api-key"])
                else:self.assertNotIn("x-client-api-key",headers)

    def test_missing_key_never_falls_back_and_reflected_key_is_rejected(self):
        private=profile();store=ProfileStore([private]);seen=[]
        settings=OASettings(enabled=True,endpoint="https://vwp.invalid/inference",token_source="user",token="",
            model="synthetic-model",models=("synthetic-model",),timeout=120,max_input_bytes=1048576,max_output_bytes=2097152)
        def echo(request):
            seen.append(request);return httpx.Response(200,json={"echo":"synthetic-personal-key"})
        client=LocalClient(lambda:store,transport=httpx.MockTransport(echo))
        with executing_on("vwp"):
            with self.assertRaises(ReviewError) as error:
                client.call(canonical_json(body()),store.public[private["id"]],settings,{"actor_id":"owner"})
            self.assertEqual("AI_USER_TOKEN_REQUIRED",error.exception.code);self.assertEqual([],seen)
            with self.assertRaises(ReviewError):
                client.call(canonical_json(body()),store.public[private["id"]],settings,{"actor_id":"owner"},personal_token="synthetic-personal-key")
        self.assertEqual(1,len(seen))


class VwpPersonalCredentialDatabaseTests(PgTestCase):
    def setUp(self):
        from tests.test_vwp_worker import VwpWorkerTests
        self.w=VwpWorkerTests();self.w.setUp();self.addCleanup(self.w.doCleanups)
        self.v=self.w.v;self.c=self.w.c;self.admin,self.schema=self.c.admin,self.c.schema

    def test_personal_tokens_are_isolated_encrypted_and_can_be_deleted(self):
        connection=self.w.connection
        with self.v.store() as (store,_,_):
            value=store.get(connection);tokens=store.tokens(value)
            tokens.save("other","synthetic-other-private",0)
            self.assertEqual(("synthetic-vwp-personal-credential",1),tokens.get_token("owner"))
            self.assertEqual(("synthetic-other-private",1),tokens.get_token("other"))
            tokens.save("owner","synthetic-owner-rotated",1)
            self.assertEqual(("synthetic-owner-rotated",2),tokens.get_token("owner"))
            tokens.save("owner",None,2)
            self.assertFalse(tokens.status("owner")["registered"])
            self.assertEqual(("synthetic-other-private",1),tokens.get_token("other"))
        rows=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_CONFIG'").fetchall()
        rendered=json.dumps(rows,default=str)
        for secret in ("synthetic-vwp-personal-credential","synthetic-other-private","synthetic-owner-rotated"):
            self.assertNotIn(secret,rendered)

    def test_profile_changes_require_personal_key_reregistration(self):
        connection=self.w.connection
        self.v.private={**self.v.private,"fallback_endpoint":"https://changed.example.test/v1/chat/completions"}
        self.v.publish()
        with self.v.store() as (store,_,_):
            value=store.get(connection);tokens=store.tokens(value)
            self.assertTrue(tokens.status("owner")["connection_changed"])
            with self.assertRaises(ReviewError) as error:tokens.get_token("owner")
            self.assertEqual("AI_CREDENTIAL_SCOPE_CHANGED",error.exception.code)
            tokens.save("owner","synthetic-new-profile-key",1)
            self.assertEqual(("synthetic-new-profile-key",2),tokens.get_token("owner"))

    def test_owner_can_remove_key_while_connection_disabled_and_profile_offline(self):
        from oa.ai_connections_api import create_ai_connections_router
        from oa.sanity_vwp_inference import now
        connection=self.w.connection
        with self.v.store() as (store,actor,service):
            current=store.get(connection)
            store.save(actor,connection,{**current["config"],"enabled":False},current["revision"])
            future=now(service.db)+timedelta(seconds=1300)
            current_actor=SimpleNamespace(user_id="owner",auth_method="sso",access=SimpleNamespace(db=service.db))
            app=FastAPI();app.include_router(create_ai_connections_router(lambda:current_actor))
            with patch("oa.sanity_vwp_inference.now",return_value=future),TestClient(app) as client:
                status=client.get(f"/api/ai/connections/{connection}/credentials")
                self.assertEqual(200,status.status_code,status.text)
                self.assertTrue(status.json()["registered"])
                self.assertFalse(status.json()["connection_changed"])
                self.assertEqual("AI_VWP_PROFILE_UNAVAILABLE",status.json()["credential_error"])
                response=client.post(f"/api/ai/connections/{connection}/credentials/remove",json={"expected_revision":1})
            self.assertEqual(200,response.status_code,response.text)
            self.assertFalse(response.json()["registered"])
            self.assertNotIn("synthetic-vwp-personal-credential",response.text)

    def test_missing_personal_key_blocks_submission_without_shared_fallback(self):
        with self.v.store() as (store,_,_):
            value=store.get(self.w.connection)
            store.tokens(value).save("owner",None,1)
            choice=store.user_connections("owner")[0]
            self.assertTrue(choice["credential_required"])
            self.assertFalse(choice["credential_ready"])
            self.assertEqual(("bearer","AI_USER_TOKEN_REQUIRED"),(choice["auth_mode"],choice["credential_error"]))
        with self.assertRaises(ReviewError) as error:self.w.request()
        self.assertEqual("AI_USER_TOKEN_REQUIRED",error.exception.code)
        self.assertEqual([],self.v.wire)

    def test_manual_resume_rejects_a_changed_personal_key(self):
        job=self.w.request();self.w.prepare()
        self.c.callback=lambda request:httpx.Response(503)
        for attempt in range(6):
            at=_now()+timedelta(seconds=400*attempt)
            with executing_on("vwp"),patch("oa.sanity_ai_jobs._db_now",return_value=at):result=self.c.run_job(now=at)
        self.assertEqual("FAILED",result["status"])
        self.assertTrue(result["resumable"])
        with self.v.store() as (store,_,_):store.tokens(store.get(self.w.connection)).save("owner","synthetic-resume-rotated",1)
        with self.c.service() as service,self.assertRaises(ReviewError) as error:
            service.resume("owner",job["id"],uuid4())
        self.assertEqual("AI_CREDENTIAL_CHANGED",error.exception.code)
        self.assertEqual(6,len(self.v.wire))

    def test_deleted_key_after_submission_stops_preparation_without_http(self):
        self.w.request()
        with self.v.store() as (store,_,_):store.tokens(store.get(self.w.connection)).save("owner",None,1)
        result=self.w.prepare()
        self.assertEqual(("STALE","AI_CREDENTIAL_CHANGED"),(result["status"],result["error"]))
        self.assertEqual([],self.v.wire)

    def test_rotated_key_during_retry_cannot_send_a_second_call(self):
        self.w.request();self.w.prepare()
        self.c.callback=lambda request:httpx.Response(503)
        first=self.w.prepare();self.assertEqual("PENDING",first["status"])
        self.assertEqual(1,len(self.v.wire))
        with self.v.store() as (store,_,_):store.tokens(store.get(self.w.connection)).save("owner","synthetic-rotated-key",1)
        at=_now()+timedelta(seconds=400)
        with executing_on("vwp"),patch("oa.sanity_ai_jobs._db_now",return_value=at):result=self.c.run_job(now=at)
        self.assertEqual(("STALE","AI_CREDENTIAL_CHANGED"),(result["status"],result["error"]))
        self.assertEqual(1,len(self.v.wire))

    def test_key_rotation_during_model_call_blocks_result_application(self):
        self.w.request();self.w.prepare()
        def rotate(_request):
            with self.v.store() as (store,_,_):store.tokens(store.get(self.w.connection)).save("owner","synthetic-rotated-key",1)
        self.c.callback=rotate
        result=self.w.prepare()
        self.assertEqual(("STALE","AI_CREDENTIAL_CHANGED"),(result["status"],result["error"]))
        self.assertEqual(1,len(self.v.wire))

    def reserved_job(self):
        from tests.test_vwp_inference import configuration
        from oa.sanity_ai_budget import BatchSettings
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
            overhead_tokens=1024,max_records=1,output_parameter='max_tokens')
        self.w.connection=self.v.create(configuration(batch=settings.public()))
        job=self.w.request();self.w.prepare()
        with executing_on('vwp'),patch('oa.sanity_ai_batch_model.BatchModel.execute',side_effect=SystemExit):
            with self.assertRaises(SystemExit):self.c.run_job()
        return job

    def assert_reservation_remains_unknown(self,job):
        self.assertEqual([],self.v.wire)
        at=_now()
        with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=at):result=self.c.run_job(now=at)
        self.assertEqual('RUNNING',result['status'])
        future=at+timedelta(seconds=400)
        with executing_on('vwp'),patch('oa.sanity_ai_jobs._db_now',return_value=future):result=self.c.run_job(now=future)
        self.assertEqual(('UNKNOWN','AI_OUTCOME_UNKNOWN'),(result['status'],result['error']))
        with self.c.service() as service,self.assertRaises(ReviewError) as error:
            service.resume('owner',job['id'],uuid4())
        self.assertEqual('AI_RESUME_UNAVAILABLE',error.exception.code)
        self.assertEqual([],self.v.wire)

    def test_reserved_batch_remains_unknown_if_personal_key_is_deleted(self):
        job=self.reserved_job()
        with self.v.store() as (store,_,_):store.tokens(store.get(self.w.connection)).save('owner',None,1)
        self.assert_reservation_remains_unknown(job)

    def test_legacy_reserved_batch_without_pin_remains_unknown(self):
        job=self.reserved_job()
        rows=self.c.admin_query("SELECT workspace_key,payload FROM {oa} WHERE record_type='WS_CONFIG' AND workspace_key LIKE %s ORDER BY workspace_key",
                                ('ai1.job.'+job['id']+'.%',)).fetchall()
        previous=None
        for key,event in rows:
            event['data'].pop('credential_pin',None);event['previous_sha256']=previous
            event['sha256']=digest({k:v for k,v in event.items() if k!='sha256'});previous=event['sha256']
            self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_CONFIG' AND workspace_key=%s",(json_value(event),key))
        self.assert_reservation_remains_unknown(job)
