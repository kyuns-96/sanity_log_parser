"""Synthetic-only VWP inference, real restricted DB queues and complete OA jobs."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_db import Database, Queue, json_value
from common.sanity_ai_execution import executing_on
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_connections import ConnectionStore, validate_config
from oa.sanity_ai_model import OAAdvisor
from oa.sanity_vwp_inference import Catalog, LocalClient, publish_catalog, now
from oa.sanity_workspace_clustering import WorkspaceClusteringStore
from tests.support import PgTestCase, ROOT
from common.sanity_vwp_ai_http import invoke
from common.sanity_vwp_ai_profiles import ProfileStore


def profile(**changes):
    return {"id":"synthetic-vwp", "label":"합성 VWP AI", "endpoint":"https://vwp-model.example.test/chat/completions",
        "fallback_endpoint":"", "api":"chat_completions", "models":["synthetic-model", "vwp-fast"],
        "auth_mode":"bearer", "token":"synthetic-vwp-personal-credential", "client_api_key":"",
        "client_name":"", "client_version":"", "session_mode":"disabled", "session_id":"", "ca_path":"",
        "timeout":120, "max_input_bytes":1048576, "max_output_bytes":2097152, "allowed_user_ids":["owner"], **changes}


def configuration(**changes):
    return {"label":"VWP 분류·판정", "enabled":True, "execution_site":"vwp", "profile_id":"synthetic-vwp",
        "api":"chat_completions", "model":"synthetic-model", "models":["synthetic-model", "vwp-fast"],
        "json_mode":False, "timeout":120, "max_input_bytes":1048576, "max_output_bytes":2097152,
        "batch":BatchSettings().public(), **changes}


def body(**changes):
    return {"model":"synthetic-model", "stream":False,
            "messages":[{"role":"system", "content":"Return JSON only."}, {"role":"user", "content":"Synthetic log."}], **changes}


class VwpInferenceContractTests(unittest.TestCase):
    def test_profile_catalog_contains_no_credentials_or_endpoints(self):
        private = profile()
        public = ProfileStore([private]).public[private["id"]]
        rendered = json.dumps(public)
        self.assertNotIn(private["token"], rendered)
        self.assertNotIn(private["endpoint"], rendered)
        self.assertEqual(digest(private), public["sha256"])
        self.assertEqual(["owner"], public["allowed_user_ids"])

    def test_remote_registration_cannot_supply_endpoints_credentials_or_commands(self):
        self.assertEqual("vwp", validate_config(configuration())["execution_site"])
        for extra in ({"endpoint":"https://another.example.test"}, {"token":"synthetic-secret"},
                      {"client_api_key":"synthetic-secret"}, {"argv":["/bin/false"]}, {"issuance_url":"https://issue.example.test"}):
            with self.subTest(extra=extra), self.assertRaises(ReviewError):
                validate_config(configuration(**extra))

    def test_profiles_require_explicit_callers_http_or_https_and_finite_limits(self):
        for changes in ({"allowed_user_ids":[]}, {"allowed_user_ids":["owner","owner"]},
                        {"endpoint":"ftp://model.example.test"}, {"endpoint":"https://model.example.test?key=synthetic"},
                        {"timeout":True}, {"max_input_bytes":0}, {"models":[{}]}, {"token":""}):
            with self.subTest(changes=changes), self.assertRaises(ReviewError):
                ProfileStore([profile(**changes)])

    def test_profile_file_uses_synthetic_fstat_to_check_owner_mode_links_and_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"profiles.json"
            path.write_bytes(canonical_json({"schema_version":1, "profiles":[profile()]}))
            path.chmod(0o600)
            actual = os.fstat
            def info(fd, **changes):
                source = actual(fd)
                return SimpleNamespace(**{**{key:getattr(source,key) for key in
                    ("st_dev","st_ino","st_size","st_mtime_ns","st_ctime_ns")},
                    "st_nlink":1,"st_mode":stat.S_IFREG|0o600,"st_uid":os.geteuid(),**changes})
            with patch("common.sanity_vwp_ai_profiles.os.fstat", side_effect=lambda fd:info(fd)):
                self.assertIn("synthetic-vwp", ProfileStore.load({"SANITY_VWP_AI_PROFILES_FILE":str(path)}).public)
            for changes in ({"st_nlink":2}, {"st_mode":stat.S_IFREG|0o640}, {"st_uid":os.geteuid()+10001}):
                with self.subTest(changes=changes), patch("common.sanity_vwp_ai_profiles.os.fstat",
                        side_effect=lambda fd, values=changes:info(fd,**values)), self.assertRaises(ReviewError):
                    ProfileStore.load({"SANITY_VWP_AI_PROFILES_FILE":str(path)})
            reads = [0]
            def changed(fd):
                reads[0] += 1
                return info(fd,st_mtime_ns=reads[0])
            with patch("common.sanity_vwp_ai_profiles.os.fstat",side_effect=changed), self.assertRaises(ReviewError):
                ProfileStore.load({"SANITY_VWP_AI_PROFILES_FILE":str(path)})

    def test_http_uses_only_vwp_credentials_and_keeps_the_prepared_body(self):
        for mode in ("bearer","x_client_api_key","bearer_x_client"):
            extra = {"client_api_key":"synthetic-vwp-client-key", "client_name":"synthetic-app", "client_version":"1",
                     "session_mode":"per_request"} if mode == "bearer_x_client" else {}
            private = profile(auth_mode=mode,**extra)
            request_body = body(**({"extraData":{"clientName":"synthetic-app","clientVersion":"1","sessionId":str(uuid4())}}
                                  if mode == "bearer_x_client" else {}))
            raw = canonical_json(request_body)
            seen = []
            def respond(request):
                seen.append(request)
                return httpx.Response(200,json={"model":"synthetic-model", "choices":[]})
            response, _ = invoke(raw,private,timeout=30,max_output_bytes=4096,transport=httpx.MockTransport(respond))
            self.assertTrue(response)
            self.assertEqual(raw,seen[0].content)
            self.assertEqual(mode!="x_client_api_key","authorization" in seen[0].headers)
            self.assertEqual(mode!="bearer","x-client-api-key" in seen[0].headers)

    def test_only_connect_failure_uses_same_model_backup(self):
        private = profile(fallback_endpoint="https://backup.example.test/chat/completions")
        seen=[]
        def respond(request):
            seen.append(request)
            if len(seen)==1:raise httpx.ConnectError("synthetic",request=request)
            return httpx.Response(200,json={"model":"synthetic-model"})
        _, routing=invoke(canonical_json(body()),private,timeout=30,max_output_bytes=4096,transport=httpx.MockTransport(respond))
        self.assertEqual({"endpoint":"fallback","attempted_endpoints":["primary","fallback"]},routing)
        self.assertEqual(seen[0].content,seen[1].content)
        seen.clear()
        def timeout(request):
            seen.append(request);raise httpx.ReadTimeout("synthetic",request=request)
        with self.assertRaises(ReviewError) as caught:
            invoke(canonical_json(body()),private,timeout=30,max_output_bytes=4096,transport=httpx.MockTransport(timeout))
        self.assertEqual("AI_OUTCOME_UNKNOWN",caught.exception.code)
        self.assertEqual(1,len(seen))

    def test_vwp_response_budget_and_secret_guard_are_enforced(self):
        for response in (httpx.Response(200,content=b"x"*5000),httpx.Response(200,json={"echo":profile()["token"]})):
            with self.assertRaises(ReviewError):
                invoke(canonical_json(body()),profile(),timeout=30,max_output_bytes=4096,
                       transport=httpx.MockTransport(lambda request:response))


class VwpInferenceDatabaseTests(PgTestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.admin,self.schema=self.c.admin,self.c.schema
        self.private=profile();self.wire=[]
        def respond(request):
            self.wire.append(request)
            self.assertEqual("Bearer "+self.private["token"],request.headers["authorization"])
            return self.c.respond(request)
        self.transport=httpx.MockTransport(respond)
        self.client=LocalClient(self.profiles,transport=self.transport)
        client=patch("oa.sanity_vwp_inference.LocalClient",return_value=self.client)
        client.start();self.addCleanup(client.stop)
        self.publish()

    def profiles(self):return ProfileStore([self.private])

    def publish(self):
        with executing_on("vwp"):publish_catalog(lambda:Database("oa"),self.profiles)

    @contextmanager
    def store(self):
        with self.c.service() as service:
            def admin(actor):
                if actor.user_id!="owner":raise ReviewError("FORBIDDEN","Synthetic admin only")
            actor=SimpleNamespace(user_id="owner",auth_method="sso",access=SimpleNamespace(db=service.db,_admin=admin))
            yield ConnectionStore(service.db,{}),actor,service

    def create(self,config=None):
        connection=str(uuid4())
        with self.store() as (store,actor,_):store.save(actor,connection,config or configuration(),0)
        return connection

    def finish(self):
        with executing_on("vwp"):
            for _ in range(30):
                result=self.c.run_job()
                if result is None or result["status"] in ("COMPLETED","STALE","FAILED","UNKNOWN","CANCELLED"):return result
        self.fail("VWP inference did not finish")

    def test_registration_is_admin_only_and_needs_no_oa_token_encryption(self):
        with self.store() as (store,actor,_):
            denied=SimpleNamespace(user_id="other",auth_method="sso",access=actor.access)
            with self.assertRaises(ReviewError):store.save(denied,str(uuid4()),configuration(),0)
        connection=self.create()
        with self.store() as (store,_,_):
            choice=store.user_connections("owner")[0]
            self.assertEqual(connection,choice["id"]);self.assertFalse(choice["credential_required"])
            self.assertTrue(choice["credential_ready"])
            with self.assertRaises(ReviewError) as caught:store.tokens(store.get(connection))
            self.assertEqual("AI_VWP_CREDENTIALS_MANAGED",caught.exception.code)

    def test_whole_report_is_not_sampled_and_is_executed_on_vwp(self):
        connection=self.create()
        rows=[{"id":"r"+str(i),"raw":"합성 전체 로그 "+("node-path/"*400),
               "fields":{"object":"top/node-"+str(i),"check_point":"clock","mode":"M1"}} for i in range(130)]
        report=self.c.admin_query("SELECT payload FROM {oa} WHERE record_type='WS_REPORT'").fetchone()[0]
        report.update(records=rows,record_count=len(rows))
        self.c.admin_query("UPDATE {oa} SET payload=%s WHERE record_type='WS_REPORT'",(json_value(report),))
        self.c.enqueue(connection_id=connection)
        with patch.object(OAAdvisor,"_call",side_effect=AssertionError("VWP uses its local model client")):result=self.finish()
        self.assertEqual("COMPLETED",result["status"],result)
        self.assertEqual({r["id"] for r in rows},{r["id"] for r in self.c.requests[0]["context"]["report"]["records"]})
        with self.c.service() as service:
            event,_=WorkspaceClusteringStore(service.db,ws=service.ws).current("report")
            self.assertEqual("VWP_OPENAI_COMPATIBLE",event["model_metadata"]["provider"])
            self.assertEqual("vwp",event["model_metadata"]["execution"]["site"])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa_to_vwp}").fetchone()[0])
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {vwp_to_oa}").fetchone()[0])

    def test_group_review_preserves_full_evidence_and_human_confirmation(self):
        connection=self.create();self.c.enqueue(connection_id=connection)
        first=self.finish();self.assertEqual("COMPLETED",first["status"],first)
        with self.c.service() as service:
            store=WorkspaceClusteringStore(service.db,ws=service.ws)
            event,revision=store.current("report")
            store.change(*self.c.path,action="CONFIRM",expected_revision=revision,request_id=uuid4())
            event,revision=store.current("report");group=event["groups"][0]["id"]
        self.c.enqueue(connection_id=connection,expected_revision=0,group_id=group,expected_cluster_revision=revision)
        result=self.finish();self.assertEqual("COMPLETED",result["status"],result)
        self.assertEqual({"a","b"},{v["id"] for v in self.c.requests[-1]["context"]["group_evidence"]})
        self.assertEqual(0,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_EVENT'").fetchone()[0])
        self.assertEqual(1,self.c.admin_query("SELECT count(*) FROM {oa} WHERE record_type='WS_GROUP_REVIEW_PROPOSAL'").fetchone()[0])

    def test_batch_leaves_and_merge_keep_the_same_profile(self):
        settings=BatchSettings(enabled=True,context_tokens=32768,input_tokens=16000,output_tokens=4096,
                               overhead_tokens=1024,max_records=1,output_parameter="max_tokens")
        connection=self.create(configuration(batch=settings.public()));self.c.enqueue(connection_id=connection)
        result=self.finish();self.assertEqual("COMPLETED",result["status"],result)
        self.assertGreaterEqual(len(self.wire),3)
        self.assertTrue(all(str(request.url)==self.private["endpoint"] for request in self.wire))

    def test_http_profile_and_http_backup_run_the_complete_vwp_job(self):
        self.private=profile(endpoint="http://vwp-model.example.test/chat/completions",
            fallback_endpoint="http://vwp-backup.example.test/chat/completions")
        self.publish();connection=self.create();self.c.enqueue(connection_id=connection)
        def refuse_first(request):
            if len(self.wire)==1:raise httpx.ConnectError("synthetic connection refused",request=request)
        self.c.callback=refuse_first
        result=self.finish()
        self.assertEqual("COMPLETED",result["status"],result)
        self.assertEqual([self.private["endpoint"],self.private["fallback_endpoint"]],[str(r.url) for r in self.wire])
        self.assertEqual(self.wire[0].content,self.wire[1].content)
        self.assertIsNone(self.finish());self.assertEqual(2,len(self.wire))

    def test_timeout_is_unknown_and_restart_never_repeats_the_call(self):
        connection=self.create();self.c.enqueue(connection_id=connection)
        self.c.callback=lambda request:(_ for _ in ()).throw(httpx.ReadTimeout("synthetic",request=request))
        result=self.finish();self.assertEqual(("UNKNOWN","AI_OUTCOME_UNKNOWN"),(result["status"],result["error"]))
        self.assertEqual(1,len(self.wire));self.assertIsNone(self.finish());self.assertEqual(1,len(self.wire))

    def test_current_permissions_are_checked_before_registration_and_result_publication(self):
        connection=self.create();self.private=profile(allowed_user_ids=["other"]);self.publish()
        with self.assertRaises(ReviewError) as caught:self.c.enqueue(connection_id=connection)
        self.assertEqual("FORBIDDEN",caught.exception.code)
        self.private=profile();self.publish();self.c.enqueue(connection_id=connection)
        def disable(request):
            self.c.admin_query("UPDATE {oa} SET state=%s,version=version+1,updated_at=clock_timestamp() WHERE record_type='WS_USER'",
                               (json_value({"active":False}),))
        self.c.callback=disable
        result=self.finish();self.assertEqual(("STALE","UNAUTHORIZED"),(result["status"],result["error"]))

    def test_vwp_credentials_and_endpoint_never_enter_oa_storage(self):
        connection=self.create();self.c.enqueue(connection_id=connection)
        result=self.finish();self.assertEqual("COMPLETED",result["status"],result)
        rows=self.c.admin_query("SELECT payload FROM {oa}").fetchall()
        rendered=json.dumps(rows,default=str)
        self.assertNotIn(self.private["token"],rendered);self.assertNotIn(self.private["endpoint"],rendered)

    def test_offline_connection_can_be_disabled_without_attaching_oa_credentials(self):
        connection=self.create()
        with self.store() as (store,actor,_):
            value=store.get(connection)
            with self.assertRaises(ReviewError):store.save(actor,connection,value["config"],1,"synthetic-unexpected-key")
        with Database("oa") as db:future=now(db)+timedelta(seconds=1300)
        with patch("oa.sanity_vwp_inference.now",return_value=future),self.store() as (store,actor,_):
            value=store.get(connection)
            result=store.save(actor,connection,{**value["config"],"enabled":False},1)
            self.assertFalse(result["config"]["enabled"])

    def test_profile_changes_before_or_during_inference_stale_the_job(self):
        connection=self.create();self.c.enqueue(connection_id=connection)
        self.private=profile(token="synthetic-rotated-vwp-token");self.publish()
        first=self.finish();self.assertEqual(("STALE","AI_CONFIG_CHANGED"),(first["status"],first["error"]))
        self.assertEqual([],self.wire)
        self.c.enqueue(connection_id=connection)
        def rotate(request):
            self.private=profile(token="synthetic-second-vwp-rotation");self.publish()
        self.c.callback=rotate
        second=self.finish();self.assertEqual(("STALE","AI_CONFIG_CHANGED"),(second["status"],second["error"]))
        self.assertEqual(1,len(self.wire))

    def test_responses_api_uses_the_selected_model(self):
        self.private=profile(api="responses");self.publish()
        connection=self.create(configuration(api="responses",model="vwp-fast"))
        def respond(request):
            self.wire.append(request);value=json.loads(request.content)
            self.assertFalse(value["store"]);self.assertNotIn("messages",value)
            adapted=httpx.Request("POST",str(request.url),json={"model":value["model"],"stream":False,"messages":value["input"]})
            response=self.c.respond(adapted).json()
            return httpx.Response(200,json={"model":value["model"],"status":"completed","output":[{"type":"message",
                "content":[{"type":"output_text","text":response["choices"][0]["message"]["content"]}]}]})
        self.client.transport=httpx.MockTransport(respond)
        self.c.enqueue(connection_id=connection);result=self.finish()
        self.assertEqual("COMPLETED",result["status"],result);self.assertEqual("vwp-fast",result["model"])

    def test_gateway_body_and_headers_are_bound_to_the_local_profile(self):
        self.private=profile(auth_mode="bearer_x_client",client_api_key="synthetic-vwp-gateway-key",
            client_name="synthetic-vwp-app",client_version="1",session_mode="per_request")
        self.publish();connection=self.create();self.c.enqueue(connection_id=connection)
        result=self.finish();self.assertEqual("COMPLETED",result["status"],result)
        request=self.wire[0]
        self.assertEqual(self.private["client_api_key"],request.headers["x-client-api-key"])
        self.assertEqual("synthetic-vwp-app",json.loads(request.content)["extraData"]["clientName"])
