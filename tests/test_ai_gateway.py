"""Offline company-gateway contracts; only synthetic credentials and reports."""
from copy import deepcopy
from dataclasses import replace
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import patch
from uuid import UUID

import httpx

from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_config import load_environment
from common.sanity_integration_examples import clustering_example, group_review_example
from common.sanity_privacy import MASK, SecretGuard
from oa.sanity_ai_model import OAAdvisor, OASettings
from tests.test_ai_model import chat, criterion, environment, review_reply


def gateway_environment(**extra):
    return environment(SANITY_OA_AI_CLIENT_API_KEY="synthetic-client-key-private",
                       SANITY_OA_AI_CLIENT_NAME="synthetic-client-private",
                       SANITY_OA_AI_CLIENT_VERSION="synthetic-release-private",
                       SANITY_OA_AI_SESSION_MODE="per_request") | extra


class GatewaySettingsTests(unittest.TestCase):
    def test_gateway_settings_load_without_interpolation_and_exported_values_win(self):
        values = gateway_environment(SANITY_OA_AI_CLIENT_API_KEY="synthetic-${LITERAL}-key")
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / ".env"
            path.write_text("".join(key + "=" + value + "\n" for key, value in values.items())
                            + "UNRELATED=ignored\n", encoding="utf-8")
            loaded = load_environment(path)
            self.assertEqual(values, loaded)
            self.assertEqual(values["SANITY_OA_AI_CLIENT_API_KEY"], OASettings.from_env(loaded).client_api_key)
            self.assertNotIn("SANITY_OA_AI_CLIENT_API_KEY", os.environ)
            with patch.dict(os.environ, {"SANITY_OA_AI_CLIENT_API_KEY": "synthetic-rotated-key"}):
                self.assertEqual("synthetic-rotated-key", OASettings.from_env(load_environment(path)).client_api_key)
            with patch.dict(os.environ, {"SANITY_OA_AI_CLIENT_API_KEY": ""}):
                with self.assertRaises(ReviewError) as error:
                    OASettings.from_env(load_environment(path))
                self.assertEqual("AI_CONFIG", error.exception.code)

    def test_partial_gateway_configuration_and_implicit_session_policy_fail_closed(self):
        for key in ("CLIENT_API_KEY", "CLIENT_NAME", "CLIENT_VERSION", "SESSION_MODE"):
            with self.subTest(key=key), self.assertRaises(ReviewError) as error:
                OAAdvisor(gateway_environment(**{"SANITY_OA_AI_" + key: ""}),
                          transport=httpx.MockTransport(lambda _: self.fail("Must not dispatch")))
            self.assertEqual("AI_CONFIG", error.exception.code)
        for key, value in (("CLIENT_API_KEY", "synthetic-only-key"), ("CLIENT_NAME", "synthetic-only-name"),
                           ("CLIENT_VERSION", "1"), ("SESSION_MODE", "per_request"), ("SESSION_ID", "synthetic-id")):
            with self.subTest(key=key), self.assertRaises(ReviewError) as error:
                OASettings.from_env(environment(**{"SANITY_OA_AI_" + key: value}))
            self.assertEqual("AI_CONFIG", error.exception.code)

    def test_invalid_gateway_values_never_become_headers_or_body(self):
        cases = {"CLIENT_API_KEY": [None, True, "bad\r\nInjected: value", "has spaces", "한글", "x" * 8193],
                 "CLIENT_NAME": [None, 1, " ", "name\nvalue", "name\x00", " leading", "x" * 201],
                 "CLIENT_VERSION": [None, True, "\t", "version\r", "trailing ", "x" * 201],
                 "SESSION_MODE": [None, True, "auto", "PER_REQUEST", "disabled"]}
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ReviewError) as error:
                    OAAdvisor(gateway_environment(**{"SANITY_OA_AI_" + key: value}),
                              transport=httpx.MockTransport(lambda _: self.fail("Must not dispatch")))
                self.assertEqual("AI_CONFIG", error.exception.code)

    def test_fixed_sessions_require_an_explicit_valid_id_and_per_request_rejects_one(self):
        for value in ("", None, True, " ", "bad\r\nid", "trailing ", "x" * 201):
            with self.subTest(value=value), self.assertRaises(ReviewError) as error:
                OASettings.from_env(gateway_environment(SANITY_OA_AI_SESSION_MODE="fixed", SANITY_OA_AI_SESSION_ID=value))
            self.assertEqual("AI_CONFIG", error.exception.code)
        with self.assertRaises(ReviewError):
            OASettings.from_env(gateway_environment(SANITY_OA_AI_SESSION_ID="synthetic-fixed-id"))
        settings = OASettings.from_env(gateway_environment(SANITY_OA_AI_SESSION_MODE="fixed",
                                                         SANITY_OA_AI_SESSION_ID="synthetic-fixed-id"))
        self.assertEqual("synthetic-fixed-id", settings.session_id)

    def test_gateway_is_limited_to_the_confirmed_chat_completions_contract(self):
        with self.assertRaises(ReviewError) as error:
            OASettings.from_env(gateway_environment(SANITY_OA_AI_API="responses"))
        self.assertEqual("AI_CONFIG", error.exception.code)

    def test_private_gateway_settings_are_not_in_repr_or_public_description(self):
        settings = OASettings.from_env(gateway_environment(SANITY_OA_AI_SESSION_MODE="fixed",
                                                         SANITY_OA_AI_SESSION_ID="synthetic-session-private"))
        public = OAAdvisor(settings).describe()
        rendered = repr(settings) + json.dumps(public)
        for value in (settings.client_api_key, settings.client_name, settings.client_version, settings.session_id):
            self.assertNotIn(value, rendered)
        self.assertFalse({"client_name", "client_version", "session_mode", "session_id", "extraData"}.intersection(public))

    def test_credential_rotation_preserves_pins_but_gateway_policy_changes_do_not(self):
        settings = OASettings.from_env(gateway_environment())
        original = OAAdvisor(settings).describe()
        for updated in (replace(settings, token="synthetic-rotated-bearer"),
                        replace(settings, client_api_key="synthetic-rotated-client-key")):
            self.assertEqual(original, OAAdvisor(updated).describe())
        for updated in (replace(settings, client_name="synthetic-other-client"),
                        replace(settings, client_version="synthetic-other-release"),
                        replace(settings, session_mode="fixed", session_id="synthetic-session-a")):
            self.assertNotEqual(original["config_sha256"], OAAdvisor(updated).describe()["config_sha256"])
        fixed = replace(settings, session_mode="fixed", session_id="synthetic-session-a")
        self.assertNotEqual(OAAdvisor(fixed).describe()["config_sha256"],
                            OAAdvisor(replace(fixed, session_id="synthetic-session-b")).describe()["config_sha256"])
        self.assertEqual(original, OAAdvisor(settings).describe())

    def test_existing_non_gateway_settings_keep_their_original_configuration_pin(self):
        description = OAAdvisor(environment()).describe()
        public = {key: value for key, value in description.items() if key != "config_sha256"}
        self.assertEqual(digest({**public, "endpoint": environment()["SANITY_OA_AI_URL"]}), description["config_sha256"])


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

    def test_each_actual_model_call_gets_a_new_uuid_without_changing_the_config_pin(self):
        sessions = []

        def respond(request):
            sessions.append(json.loads(request.content)["extraData"]["sessionId"])
            return httpx.Response(200, json=chat(self.packet["reply"]))

        advisor = OAAdvisor(gateway_environment(), transport=httpx.MockTransport(respond))
        before = advisor.describe()
        for _ in range(2):
            self.assertEqual(before["config_sha256"], self.invoke(advisor)["metadata"]["config_sha256"])
        self.assertEqual(2, len(set(sessions)))
        self.assertTrue(all(UUID(value).version == 4 for value in sessions))
        self.assertEqual(before, advisor.describe())

    def test_fixed_session_is_used_exactly_and_short_noncredential_metadata_is_supported(self):
        sessions = []

        def respond(request):
            extra = json.loads(request.content)["extraData"]
            sessions.append(extra["sessionId"])
            self.assertEqual("1", extra["clientVersion"])
            return httpx.Response(200, json=chat(self.packet["reply"]))

        advisor = OAAdvisor(gateway_environment(SANITY_OA_AI_CLIENT_NAME="OA", SANITY_OA_AI_CLIENT_VERSION="1",
                            SANITY_OA_AI_SESSION_MODE="fixed", SANITY_OA_AI_SESSION_ID="1"),
                            transport=httpx.MockTransport(respond))
        for _ in range(2):
            self.invoke(advisor)
        self.assertEqual(["1", "1"], sessions)

    def test_confirmed_group_review_uses_the_same_gateway_contract_and_only_its_evidence(self):
        packet = group_review_example()
        criteria = [criterion(packet["context"])]
        captured = []

        def respond(request):
            captured.append(request)
            body = json.loads(request.content)
            self.assertIn("x-client-api-key", request.headers)
            self.assertEqual(4, UUID(body["extraData"]["sessionId"]).version)
            actual = json.loads(body["messages"][1]["content"])
            self.assertEqual(packet["context"]["group_evidence"], actual["context"]["group_evidence"])
            self.assertNotIn("records", actual["context"]["report"])
            return httpx.Response(200, json=chat(review_reply(packet["context"], criteria=[criteria[0]["id"]])))

        advisor = OAAdvisor(gateway_environment(), transport=httpx.MockTransport(respond))
        result = advisor.review(packet["context"], packet["cases"], criteria)
        self.assertEqual("FAIL", result["advice"]["conclusion"])
        self.assertEqual(1, len(captured))

    def test_no_gateway_configuration_preserves_both_original_api_request_shapes(self):
        for api in ("chat_completions", "responses"):
            captured = []

            def respond(request):
                captured.append(request)
                body = json.loads(request.content)
                self.assertNotIn("x-client-api-key", request.headers)
                self.assertNotIn("extraData", body)
                if api == "chat_completions":
                    self.assertEqual({"model", "stream", "messages"}, set(body))
                    return httpx.Response(200, json=chat(self.packet["reply"]))
                self.assertEqual({"model", "stream", "input", "store"}, set(body))
                return httpx.Response(200, json={"status": "completed", "output": [{"type": "message",
                    "status": "completed", "role": "assistant", "content": [{"type": "output_text",
                    "text": canonical_json(self.packet["reply"]).decode()}]}]})

            with self.subTest(api=api):
                self.invoke(OAAdvisor(environment(SANITY_OA_AI_API=api), transport=httpx.MockTransport(respond)))
                self.assertEqual(1, len(captured))

    def test_extra_data_counts_toward_the_input_limit_before_dispatch(self):
        captured = []
        self.invoke(OAAdvisor(environment(), transport=httpx.MockTransport(
            lambda request: captured.append(request) or httpx.Response(200, json=chat(self.packet["reply"])))))
        bound = len(captured[0].content)
        advisor = OAAdvisor(gateway_environment(SANITY_OA_AI_MAX_INPUT_BYTES=str(bound)),
                            transport=httpx.MockTransport(lambda _: self.fail("Must not dispatch oversized metadata")))
        with self.assertRaises(ReviewError) as error:
            self.invoke(advisor)
        self.assertEqual("AI_INPUT_LIMIT", error.exception.code)
        self.assertFalse(error.exception.model_called)

    def test_api_key_in_input_or_model_output_is_rejected_without_exposing_the_key(self):
        key = gateway_environment()["SANITY_OA_AI_CLIENT_API_KEY"]
        self.packet["context"]["report"]["records"][0]["raw"] = key
        with self.assertRaises(ReviewError) as error:
            self.invoke(OAAdvisor(gateway_environment(), transport=httpx.MockTransport(lambda _: self.fail("Must not dispatch"))))
        self.assertEqual("SENSITIVE_CONTENT", error.exception.code)
        self.assertFalse(error.exception.model_called)
        self.packet = clustering_example(with_history=True)
        reply = deepcopy(self.packet["reply"])
        reply["reason"] = key
        with self.assertRaises(ReviewError) as error:
            self.invoke(OAAdvisor(gateway_environment(), transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=chat(reply)))))
        self.assertEqual("SENSITIVE_CONTENT", error.exception.code)
        self.assertTrue(error.exception.model_called)
        self.assertNotIn(key, str(error.exception))
        self.assertEqual(MASK, SecretGuard(gateway_environment()).text(key))

    def test_credentials_cannot_be_mistakenly_copied_into_gateway_body_metadata(self):
        values = gateway_environment()
        for key, extra in (("CLIENT_NAME", {}), ("CLIENT_VERSION", {}),
                           ("SESSION_ID", {"SANITY_OA_AI_SESSION_MODE": "fixed"})):
            settings = values | extra | {"SANITY_OA_AI_" + key: values["SANITY_OA_AI_CLIENT_API_KEY"]}
            with self.subTest(key=key), self.assertRaises(ReviewError) as error:
                self.invoke(OAAdvisor(settings, transport=httpx.MockTransport(lambda _: self.fail("Must not dispatch"))))
            self.assertEqual("SENSITIVE_CONTENT", error.exception.code)
            self.assertFalse(error.exception.model_called)

    def test_auth_failures_transport_errors_and_redirects_are_not_retried_or_logged(self):
        key = gateway_environment()["SANITY_OA_AI_CLIENT_API_KEY"]
        calls = []
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger("httpx.gateway-contract-test")
        original_level = logger.level
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, original_level)
        for failure, expected in ((401, "AI_CALL_FAILED"), (403, "AI_CALL_FAILED"), (307, "AI_CALL_FAILED"),
                                  ("transport", "AI_OUTCOME_UNKNOWN")):
            def respond(request):
                calls.append(request)
                logger.debug("private gateway header: %s", key)
                if failure == "transport":
                    raise httpx.ReadError("synthetic private error " + key, request=request)
                return httpx.Response(failure, text=key, headers={"Location": "https://redirect.invalid/private"})

            with self.subTest(failure=failure):
                logger.setLevel(logging.DEBUG)
                try:
                    self.invoke(OAAdvisor(gateway_environment(), transport=httpx.MockTransport(respond)))
                except ReviewError as error:
                    self.assertEqual(expected, error.code)
                    self.assertTrue(error.model_called)
                    self.assertNotIn(key, traceback.format_exc())
                else:
                    self.fail("Expected a rejected model request")
        self.assertEqual(4, len(calls))
        self.assertEqual("", output.getvalue())

    def test_compressed_oversized_and_non_strict_json_responses_remain_rejected(self):
        test = self
        class Unreadable(httpx.SyncByteStream):
            def __iter__(self):
                test.fail("Compressed body must be rejected before reading")
                yield b""
        replies = ((httpx.Response(200, stream=Unreadable(), headers={"Content-Encoding": "gzip"}), {}, "INVALID_AI_RESPONSE"),
                   (httpx.Response(200, content=b"x" * 101), {"SANITY_OA_AI_MAX_OUTPUT_BYTES": "100"}, "AI_OUTPUT_LIMIT"),
                   (httpx.Response(200, content=b'{"choices":[],"choices":[]}'), {}, "INVALID_AI_RESPONSE"))
        for response, extra, expected in replies:
            calls = []
            with self.subTest(code=expected), self.assertRaises(ReviewError) as error:
                self.invoke(OAAdvisor(gateway_environment(**extra), transport=httpx.MockTransport(
                    lambda request: calls.append(request) or response)))
            self.assertEqual(expected, error.exception.code)
            self.assertTrue(error.exception.model_called)
            self.assertEqual(1, len(calls))

    def test_gateway_configuration_keeps_https_and_ca_validation(self):
        for extra in ({"SANITY_OA_AI_URL": "http://gateway.invalid/completions"},
                      {"SANITY_OA_AI_CA_PATH": "relative-ca.pem"},
                      {"SANITY_OA_AI_CA_PATH": "/nonexistent/synthetic/private-root.pem"}):
            with self.subTest(extra=extra), self.assertRaises(ReviewError) as error:
                OAAdvisor(gateway_environment(**extra), transport=httpx.MockTransport(lambda _: self.fail("Must not dispatch")))
            self.assertEqual("AI_CONFIG", error.exception.code)
