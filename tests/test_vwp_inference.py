"""Synthetic-only VWP inference, real restricted DB queues and complete OA jobs."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet

from common.sanity_common import ReviewError
from common.sanity_db import Database
from common.sanity_ai_execution import executing_on
from oa.sanity_ai_budget import BatchSettings
from oa.sanity_ai_connections import ConnectionStore
from oa.sanity_vwp_inference import LocalClient, publish_catalog
from tests.support import PgTestCase
from common.sanity_vwp_ai_profiles import ProfileStore


PERSONAL_TOKEN = "synthetic-vwp-personal-credential"


def profile(**changes):
    return {"id":"synthetic-vwp", "label":"합성 VWP AI", "endpoint":"https://vwp-model.example.test/chat/completions",
        "fallback_endpoint":"", "api":"chat_completions", "models":["synthetic-model", "vwp-fast"],
        "auth_mode":"bearer", "token_source":"user", "client_api_key":"",
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


class VwpInferenceDatabaseTests(PgTestCase):
    def setUp(self):
        from tests.test_ai_jobs import AIJobDatabaseTests
        self.c=AIJobDatabaseTests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.admin,self.schema=self.c.admin,self.c.schema
        self.settings={"SANITY_OA_AI_TOKEN_STORE_SECRET":Fernet.generate_key().decode()}
        private_settings=patch("oa.sanity_ai_connections.load_environment",return_value=self.settings)
        private_settings.start();self.addCleanup(private_settings.stop)
        self.private=profile();self.wire=[]
        def respond(request):
            self.wire.append(request)
            self.assertEqual("Bearer "+PERSONAL_TOKEN,request.headers["authorization"])
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
            yield ConnectionStore(service.db,self.settings),actor,service

    def create(self,config=None):
        connection=str(uuid4())
        with self.store() as (store,actor,_):
            store.save(actor,connection,config or configuration(),0)
            store.tokens(store.get(connection)).save("owner",PERSONAL_TOKEN,0)
        return connection

    def finish(self):
        with executing_on("vwp"):
            for _ in range(30):
                result=self.c.run_job()
                if result is None or result["status"] in ("COMPLETED","STALE","FAILED","UNKNOWN","CANCELLED"):return result
        self.fail("VWP inference did not finish")
