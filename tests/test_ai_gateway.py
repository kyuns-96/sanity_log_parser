"""Offline company-gateway contracts; only synthetic credentials and reports."""
import json
import unittest
from uuid import UUID

import httpx

from common.sanity_common import digest
from common.sanity_integration_examples import clustering_example
from oa.sanity_ai_model import OAAdvisor
from tests.test_ai_model import chat, environment


def gateway_environment(**extra):
    return environment(SANITY_OA_AI_CLIENT_API_KEY="synthetic-client-key-private",
                       SANITY_OA_AI_CLIENT_NAME="synthetic-client-private",
                       SANITY_OA_AI_CLIENT_VERSION="synthetic-release-private",
                       SANITY_OA_AI_SESSION_MODE="per_request") | extra


class GatewayRequestTests(unittest.TestCase):
    def setUp(self):
        self.packet = clustering_example(with_history=True)

    def invoke(self, advisor):
        return advisor.cluster(self.packet["context"], self.packet["cases"], self.packet["identity_fields"])

    def test_both_auth_headers_and_typed_extra_data_preserve_complete_model_input(self):
        captured = []
        settings = gateway_environment(SANITY_OA_AI_JSON_MODE="true")

        def respond(request):
            captured.append(request)
            body = json.loads(request.content)
            self.assertEqual("Bearer " + settings["SANITY_OA_AI_TOKEN"], request.headers["authorization"])
            self.assertEqual(settings["SANITY_OA_AI_CLIENT_API_KEY"], request.headers["x-client-api-key"])
            self.assertEqual({"model", "stream", "messages", "response_format", "extraData"}, set(body))
            self.assertEqual({"type": "json_object"}, body["response_format"])
            self.assertFalse(body["stream"])
            extra = body["extraData"]
            self.assertEqual({"clientName", "clientVersion", "sessionId"}, set(extra))
            self.assertEqual(settings["SANITY_OA_AI_CLIENT_NAME"], extra["clientName"])
            self.assertEqual(settings["SANITY_OA_AI_CLIENT_VERSION"], extra["clientVersion"])
            self.assertEqual(4, UUID(extra["sessionId"]).version)
            packet = json.loads(body["messages"][1]["content"])
            self.assertEqual(self.packet["context"]["report"]["records"], packet["context"]["report"]["records"])
            self.assertNotIn("extraData", packet)
            for key in ("SANITY_OA_AI_CLIENT_API_KEY", "SANITY_OA_AI_CLIENT_NAME", "SANITY_OA_AI_CLIENT_VERSION"):
                self.assertNotIn(settings[key], body["messages"][1]["content"])
            self.assertNotIn(settings["SANITY_OA_AI_CLIENT_API_KEY"], request.content.decode())
            return httpx.Response(200, json=chat(self.packet["reply"]))

        result = self.invoke(OAAdvisor(settings, transport=httpx.MockTransport(respond)))
        self.assertEqual(1, len(captured))
        self.assertEqual(len(captured[0].content), result["metadata"]["input_bytes"])
        self.assertEqual(digest(json.loads(captured[0].content)), result["metadata"]["input_sha256"])
        self.assertNotIn(json.loads(captured[0].content)["extraData"]["sessionId"], json.dumps(result))
