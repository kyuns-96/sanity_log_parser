"""Administrator connection catalogs and isolated per-user/per-connection credentials."""
from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
import httpx

from common.sanity_common import ReviewError
from common.sanity_db import json_value
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_connections import ConnectionStore
from oa.sanity_ai_model import OAAdvisor
from tests.support import PgTestCase


def configuration(**changes):
    return {"label":"합성 AI 연결", "enabled":True, "endpoint":"https://one.example.test/v1/chat/completions",
        "fallback_endpoint":"", "api":"chat_completions", "auth_mode":"bearer", "model":"model-a",
        "models":["model-a","model-b"], "issuance_url":"https://identity.example.test/one",
        "json_mode":False, "timeout":120, "max_input_bytes":1048576, "max_output_bytes":2097152,
        "client_name":"", "client_version":"", "session_mode":"disabled", "session_id":"",
        "batch":BatchSettings().public(), **changes}


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


    def token(self, connection, token="synthetic-personal-key", *, user="owner", revision=0):
        with self.store() as (store,_,_):return store.tokens(store.get(connection,enabled=False)).save(user,token,revision)


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

        original=self.c.admin_query("SELECT payload->>'ciphertext' FROM {oa} WHERE payload->>'namespace'='oa-ai-user-token-v2' "
            "AND payload->>'connection_id'=%s AND payload->>'user_id'='owner'",(one,)).fetchone()[0]
        for connection,user in ((two,"owner"),(one,"other")):
            self.c.admin_query("UPDATE {oa} SET payload=jsonb_set(payload,'{{ciphertext}}',%s) "
                "WHERE payload->>'namespace'='oa-ai-user-token-v2' AND payload->>'connection_id'=%s AND payload->>'user_id'=%s",
                (json_value(original),connection,user))
            with self.store() as (store,_,_),self.assertRaises(ReviewError) as caught:
                store.tokens(store.get(connection)).get_token(user)
            self.assertEqual("AI_TOKEN_UNAVAILABLE",caught.exception.code)
