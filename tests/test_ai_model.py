"""Offline OA inference contracts. All report content and responses are synthetic."""
import copy
from dataclasses import replace
import json
import traceback
import unittest
from unittest.mock import patch

import httpx

from common.sanity_ai_contracts import validate_review_v2
from common.sanity_ai_knowledge import shared_case
from common.sanity_common import ReviewError, canonical_json, digest
from common.sanity_integration_examples import clustering_example, group_review_example
from common.sanity_review_advice import validate_advice
from oa.sanity_ai_model import OAAdvisor, OASettings


def criterion(context):
    return {"id": "synthetic-clock-policy", "version": "1", "text": "합성 검사에서 필요한 클록 정의가 없으면 실패로 제안한다.",
            "scope": {key: context["scope"][key] for key in ("project_id", "block_id", "item_id")}}


def review_reply(context, *, conclusion="FAIL", references=None, criteria=None):
    return {"schema_version": 2, "conclusion": conclusion,
            "reason": "현재 두 로그는 클록 정의 누락을 보고합니다. 제공된 점검 기준의 필수 조건을 충족하지 않아 실패를 제안합니다.",
            "evidence_ids": [context["group_evidence"][0]["id"]],
            "reference_ids": references or [], "criterion_ids": criteria or []}


def environment(**extra):
    return {"SANITY_OA_AI_ENABLED": "true", "SANITY_OA_AI_URL": "https://model.invalid/v1/chat/completions",
            "SANITY_OA_AI_TOKEN": "synthetic-only-credential", "SANITY_OA_AI_MODEL": "synthetic-model-v1", **extra}


def chat(reply, **extra):
    return {"model": "synthetic-model-v1", "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": canonical_json(reply).decode("utf-8")}}], **extra}


class ReviewV2Tests(unittest.TestCase):
    def setUp(self):
        self.packet = group_review_example()
        self.context = self.packet["context"]
        self.criteria = [criterion(self.context)]
        self.reply = review_reply(self.context, criteria=[self.criteria[0]["id"]])

    def test_first_judgment_uses_direct_evidence_and_an_approved_criterion(self):
        before = copy.deepcopy((self.reply, self.context, self.criteria))
        result = validate_review_v2(self.reply, self.context, [], self.criteria)
        self.assertEqual("FAIL", result["conclusion"])
        self.assertEqual([], result["reference_ids"])
        self.assertEqual(before, (self.reply, self.context, self.criteria))
        self.assertIsNot(result["evidence_ids"], self.reply["evidence_ids"])

    def test_a_confirmed_human_case_is_an_alternative_basis_without_reuse_checkbox(self):
        packet = group_review_example(with_history=True)
        self.assertFalse(packet["cases"][0]["reusable"])
        reply = review_reply(packet["context"], conclusion="PASS", references=[packet["cases"][0]["id"]])
        self.assertEqual("PASS", validate_review_v2(reply, packet["context"], packet["cases"], [])["conclusion"])

    def test_no_history_or_criterion_allows_only_defer(self):
        for conclusion in ("PASS", "FAIL"):
            with self.subTest(conclusion=conclusion), self.assertRaises(ReviewError):
                validate_review_v2(review_reply(self.context, conclusion=conclusion), self.context, [], [])
        reply = review_reply(self.context, conclusion="DEFER")
        reply.update(evidence_ids=[], reason="확인된 점검 기준과 적용할 과거 사례가 없어 판정을 보류합니다.")
        self.assertEqual(reply, validate_review_v2(reply, self.context, [], []))

    def test_unknown_duplicate_and_other_group_citations_are_rejected(self):
        for key, ids in (("evidence_ids", ["current-record-3"]), ("evidence_ids", []),
                         ("evidence_ids", ["current-record-1", "current-record-1"]),
                         ("reference_ids", ["invented-case"]), ("criterion_ids", ["invented-criterion"]),
                         ("criterion_ids", [self.criteria[0]["id"]] * 2)):
            with self.subTest(key=key, ids=ids), self.assertRaises(ReviewError):
                validate_review_v2({**self.reply, key: ids}, self.context, [], self.criteria)

    def test_exact_schema_and_ready_input_are_required(self):
        for reply in ({**self.reply, "schema_version": True}, {**self.reply, "schema_version": 1},
                      {**self.reply, "reason": " "}, {**self.reply, "sql": "SELECT 1"},
                      {**self.reply, "conclusion": "UNKNOWN"}, {**self.reply, "reference_ids": "case"}):
            with self.subTest(reply=reply), self.assertRaises(ReviewError):
                validate_review_v2(reply, self.context, [], self.criteria)
        for field in ("ready", "can_review"):
            with self.subTest(field=field), self.assertRaises(ReviewError):
                validate_review_v2(self.reply, {**self.context, field: False}, [], self.criteria)

    def test_partial_group_and_unrelated_criteria_fail_closed(self):
        broken = copy.deepcopy(self.context)
        broken["group_evidence"].pop()
        with self.assertRaises(ReviewError) as error:
            validate_review_v2(self.reply, broken, [], self.criteria)
        self.assertEqual("INVALID_AI_INPUT", error.exception.code)
        for criteria in ([{**self.criteria[0], "scope": {"block_id": "another-block"}}],
                         [{**self.criteria[0], "scope": {}}], self.criteria * 2,
                         [{**self.criteria[0], "version": ""}], [{**self.criteria[0], "extra": True}]):
            with self.subTest(criteria=criteria), self.assertRaises(ReviewError) as error:
                validate_review_v2(self.reply, self.context, [], criteria)
            self.assertEqual("INVALID_AI_CRITERIA", error.exception.code)

    def test_legacy_unreferenced_final_judgment_is_still_rejected(self):
        with self.assertRaises(ReviewError):
            validate_advice({"conclusion": "PASS", "reason": "과거 계약은 그대로 유지합니다.", "reference_ids": []}, [])


class OASettingsTests(unittest.TestCase):
    def test_explicit_enablement_and_validated_private_settings(self):
        self.assertFalse(OASettings.from_env({}).enabled)
        with self.assertRaises(ReviewError) as error:
            OAAdvisor({})
        self.assertEqual("AI_NOT_CONFIGURED", error.exception.code)
        settings = OASettings.from_env(environment())
        self.assertNotIn(settings.token, repr(settings))
        self.assertNotIn(settings.endpoint, repr(settings))
        metadata = OAAdvisor(settings, transport=httpx.MockTransport(lambda _: None)).describe()
        self.assertTrue(metadata["enabled"])
        self.assertEqual("synthetic-model-v1", metadata["model"])
        self.assertEqual(64, len(metadata["config_sha256"]))
        self.assertNotIn(settings.token, json.dumps(metadata))
        self.assertNotIn(settings.endpoint, json.dumps(metadata))
        rotated = OAAdvisor(replace(settings, token="rotated-synthetic-credential")).describe()
        self.assertEqual(metadata, rotated)

    def test_invalid_flags_endpoint_credentials_and_budgets_never_call_http(self):
        values = {"ENABLED": ["yes", "TRUE", True], "JSON_MODE": ["on", "1"],
                  "URL": ["ftp://model.invalid/api", "https://model.invalid:bad/api",
                          "https://name:secret@model.invalid/api", "https://model.invalid/api?q=private",
                          "https://model.invalid/api#part", "https://model.invalid/", "https://model.invalid/\napi"],
                  "TOKEN": ["", "bad\r\nheader", "한글"], "MODEL": ["", "has spaces"],
                  "API": ["unknown"], "TIMEOUT": ["0", "601", "1.5"],
                  "MAX_INPUT_BYTES": ["0", str(65 * 1024 * 1024)], "MAX_OUTPUT_BYTES": ["-1"],
                  "CA_PATH": ["relative-ca.pem"]}
        for key, choices in values.items():
            for value in choices:
                with self.subTest(key=key, value=value), self.assertRaises(ReviewError) as error:
                    OAAdvisor(environment(**{"SANITY_OA_AI_" + key: value}),
                              transport=httpx.MockTransport(lambda _: self.fail("Must not call")))
                self.assertEqual("AI_CONFIG", error.exception.code)

    def test_tls_configuration_errors_do_not_expose_the_path(self):
        with self.assertRaises(ReviewError) as error:
            OAAdvisor(environment(SANITY_OA_AI_CA_PATH="/nonexistent/private/ca.pem"))
        self.assertEqual("AI_CONFIG", error.exception.code)
        self.assertNotIn("private", str(error.exception))


class OAModelTests(unittest.TestCase):
    def setUp(self):
        self.packet = clustering_example(with_history=True)

    def adapter(self, handler, **extra):
        return OAAdvisor(environment(**extra), transport=httpx.MockTransport(handler))

    def test_wire_json_guard_preserves_packet_bytes_and_scans_nested_sequences(self):
        advisor=self.adapter(lambda request:self.fail('Guard probe must not call HTTP'))
        packet={'raw':'합성 원문 😀','fields':{'flag':False,'count':0,'empty':None},'rows':({'id':'r1'},)}
        expected=canonical_json(packet).decode()
        body,_=advisor._body(packet,'합성 점검')
        self.assertEqual(expected,body['messages'][1]['content'])
        for secret in ('Bearer synthetic-private-value', 'TOKEN=synthetic-private-value', 'baSİc synthetic-private-value'):
            with self.subTest(secret=secret),self.assertRaises(ReviewError) as caught:
                advisor._body({'rows':({'raw':secret},)},'합성 점검')
            self.assertEqual('SENSITIVE_CONTENT',caught.exception.code)

    def invoke(self, advisor):
        packet = self.packet
        return advisor.cluster(packet["context"], packet["cases"], packet["identity_fields"])

    def assert_shared_history(self, cases):
        forbidden = {"records", "raw", "fields", "raw_report", "record_ids", "evidence_ids", "command", "group_evidence"}

        def verify(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    verify(child)
            elif isinstance(value, list):
                for child in value:
                    verify(child)

        verify(cases)
        for case in cases:
            self.assertEqual("SHARED_CONFIRMED_KNOWLEDGE", case["evidence_mode"])
            self.assertFalse(case["example_complete"])

    def test_clustering_receives_all_records_human_corrections_and_korean_prompt(self):
        self.packet["context"]["_actor_user_id"] = "synthetic-actor-private-to-server"
        captured = []
        def handler(request):
            captured.append(request)
            body = json.loads(request.content)
            self.assertEqual("Bearer synthetic-only-credential", request.headers["authorization"])
            self.assertNotIn("synthetic-only-credential", request.content.decode())
            self.assertEqual({"type": "json_object"}, body["response_format"])
            self.assertFalse(body["stream"])
            self.assertIn("한국어", body["messages"][0]["content"])
            self.assertIn("정확히 한 그룹", body["messages"][0]["content"])
            actual = json.loads(body["messages"][1]["content"])
            self.assertNotIn("_actor_user_id", actual["context"])
            self.assertNotIn("synthetic-actor-private-to-server", request.content.decode())
            self.assertEqual(self.packet["context"]["report"]["records"], actual["context"]["report"]["records"])
            self.assertNotIn("raw_report", actual["context"]["report"])
            self.assertEqual([shared_case(case) for case in self.packet["cases"]], actual["cases"])
            self.assert_shared_history(actual["cases"])
            return httpx.Response(200, json=chat(self.packet["reply"], usage={"prompt_tokens": 100,
                                   "completion_tokens": 50, "total_tokens": 150, "private": "not exported"}))
        before = copy.deepcopy(self.packet)
        result = self.invoke(self.adapter(handler, SANITY_OA_AI_JSON_MODE="true"))
        self.assertEqual(1, len(captured))
        self.assertEqual(before, self.packet)
        self.assertEqual("클록 정의 누락", result["advice"]["groups"][0]["label"])
        meta = result["metadata"]
        self.assertTrue(meta["model_called"])
        self.assertEqual(digest(json.loads(captured[0].content)), meta["input_sha256"])
        self.assertEqual({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}, meta["usage"])
        self.assertEqual("oa-semantic-clustering-v3", meta["prompt_version"])
        self.assertNotIn("model.invalid", json.dumps(meta))

    def test_first_review_sends_only_complete_selected_group_and_approved_criteria(self):
        packet = group_review_example()
        context = packet["context"]
        context["_actor_user_id"] = "synthetic-actor-private-to-server"
        criteria = [criterion(context)]
        reply = review_reply(context, criteria=[criteria[0]["id"]])
        def handler(request):
            body = json.loads(request.content)
            actual = json.loads(body["messages"][1]["content"])
            self.assertNotIn("_actor_user_id", actual["context"])
            self.assertNotIn("synthetic-actor-private-to-server", request.content.decode())
            self.assertEqual(context["group_evidence"], actual["context"]["group_evidence"])
            self.assertNotIn("records", actual["context"]["report"])
            self.assertNotIn("current-record-3", body["messages"][1]["content"])
            self.assertEqual(criteria, actual["criteria"])
            self.assertEqual([], actual["cases"])
            return httpx.Response(200, json=chat(reply))
        result = self.adapter(handler).review(context, [], criteria)
        self.assertEqual(reply, result["advice"])
        self.assertEqual("oa-direct-evidence-review-v5", result["metadata"]["prompt_version"])

    def test_no_history_or_criterion_reaches_model_for_explicit_deferral(self):
        packet = group_review_example()
        reply = review_reply(packet["context"], conclusion="DEFER")
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=chat(reply))
        result = self.adapter(handler).review(packet["context"], [], [])
        self.assertEqual("DEFER", result["advice"]["conclusion"])
        self.assertEqual(1, len(calls))

    def test_review_receives_structured_human_exception_and_condition_comparison_prompt(self):
        packet = group_review_example(with_history=True)
        annotation = {"rule": "CLK_MISSING", "reason": "합성 파생 클록의 정의가 별도 근거에서 확인됨",
                      "conditions": "파생 클록과 외부 정의의 연결이 이번 실행에서도 확인된 경우"}
        packet["cases"][0]["exception"] = annotation.copy()
        before = copy.deepcopy(packet)
        reply = review_reply(packet["context"], conclusion="PASS", references=[packet["cases"][0]["id"]])

        def handler(request):
            body = json.loads(request.content)
            actual = json.loads(body["messages"][1]["content"])
            self.assertEqual([shared_case(case) for case in packet["cases"]], actual["cases"])
            self.assert_shared_history(actual["cases"])
            self.assertEqual(annotation, actual["cases"][0]["exception"])
            instruction = body["messages"][0]["content"]
            self.assertIn("예외 사유·성립 조건", instruction)
            self.assertIn("일반 PASS 규칙으로 확대하지", instruction)
            self.assertIn("현재 사람 확정이 아닙니다", instruction)
            return httpx.Response(200, json=chat(reply))

        result = self.adapter(handler).review(packet["context"], packet["cases"], [])
        self.assertEqual(reply, result["advice"])
        self.assertEqual(before, packet)

    def test_responses_mode_preserves_complete_output_and_disables_storage(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertFalse(body["store"])
            self.assertFalse(body["stream"])
            self.assertEqual({"type": "json_object"}, body["text"]["format"])
            self.assertEqual("system", body["input"][0]["role"])
            return httpx.Response(200, json={"status": "completed", "output": [
                {"type": "reasoning", "summary": []}, {"type": "message", "status": "completed",
                 "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(self.packet["reply"])}]}],
                "usage": {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50}})
        result = self.invoke(self.adapter(handler, SANITY_OA_AI_API="responses", SANITY_OA_AI_JSON_MODE="true"))
        self.assertEqual(self.packet["reply"], result["advice"])
        self.assertEqual(20, result["metadata"]["usage"]["input_tokens"])

    def test_model_cannot_omit_a_record_or_group_rationale(self):
        missing, duplicate = copy.deepcopy(self.packet["reply"]), copy.deepcopy(self.packet["reply"])
        missing["groups"][0]["record_ids"].pop()
        duplicate["groups"][1]["record_ids"].append(duplicate["groups"][0]["record_ids"][0])
        for reply in (missing, duplicate):
            with self.subTest(reply=reply), self.assertRaises(ReviewError):
                self.invoke(self.adapter(lambda _: httpx.Response(200, json=chat(reply))))
        reply = copy.deepcopy(self.packet["reply"])
        reply.pop("group_rationales")
        with self.assertRaises(ReviewError) as error:
            self.invoke(self.adapter(lambda _: httpx.Response(200, json=chat(reply))))
        self.assertEqual("INVALID_RATIONALE", error.exception.code)

    def test_input_limit_fails_before_call_without_truncating_records(self):
        before = copy.deepcopy(self.packet)
        with self.assertRaises(ReviewError) as error:
            self.invoke(self.adapter(lambda _: self.fail("Oversized input must not be called"), SANITY_OA_AI_MAX_INPUT_BYTES="100"))
        self.assertEqual("AI_INPUT_LIMIT", error.exception.code)
        self.assertEqual(before, self.packet)

    def test_more_than_legacy_sample_count_is_not_silently_sampled(self):
        packet = clustering_example()
        records = packet["context"]["report"]["records"]
        template = records[0]
        records[:] = [{**template, "id": f"record-{index}"} for index in range(80)]
        packet["context"]["report"]["record_count"] = len(records)
        reply = {"groups": [{"id": "all", "label": "합성 클록 정의 누락", "record_ids": [row["id"] for row in records]}],
                 "reason": "전체 80건의 합성 로그를 검토했습니다.", "reference_ids": [], "group_rationales": [{
                     "group_id": "all", "reason": "동일한 합성 누락 패턴이며 개별 원인은 아직 미확인입니다.",
                     "evidence_ids": [records[0]["id"]], "reference_groups": []}]}
        def handler(request):
            actual = json.loads(json.loads(request.content)["messages"][1]["content"])
            self.assertEqual(80, len(actual["context"]["report"]["records"]))
            return httpx.Response(200, json=chat(reply))
        result = self.adapter(handler).cluster(packet["context"], [], packet["identity_fields"])
        self.assertEqual(80, len(result["advice"]["groups"][0]["record_ids"]))

    def test_output_limit_redirect_and_service_errors_never_retry_or_leak_body(self):
        for response, settings, expected in (
                (httpx.Response(200, content=b"x" * 101), {"SANITY_OA_AI_MAX_OUTPUT_BYTES": "100"}, "AI_OUTPUT_LIMIT"),
                (httpx.Response(307, headers={"Location": "https://private.invalid"}), {}, "AI_CALL_FAILED"),
                (httpx.Response(503, text="private-service-debug-details"), {}, "AI_CALL_FAILED")):
            calls = []
            def handler(request):
                calls.append(request)
                return response
            with self.subTest(expected=expected), self.assertRaises(ReviewError) as error:
                self.invoke(self.adapter(handler, **settings))
            self.assertEqual(expected, error.exception.code)
            self.assertEqual(1, len(calls))
            self.assertNotIn("private", str(error.exception))

    def test_timeout_is_an_unknown_outcome_with_no_retry_or_raw_exception(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("private-model-endpoint and secret", request=request)
        try:
            self.invoke(self.adapter(handler))
        except ReviewError as error:
            self.assertEqual("AI_OUTCOME_UNKNOWN", error.code)
            self.assertNotIn("private-model", "".join(traceback.format_exception(error)))
        else:
            self.fail("Timeout must fail")
        self.assertEqual(1, len(calls))

    def test_credentials_in_model_input_or_output_are_not_exported(self):
        for secret in ("synthetic-only-credential", "https://model.invalid/v1/chat/completions"):
            packet = copy.deepcopy(self.packet)
            packet["context"]["report"]["records"][0]["raw"] = secret
            with self.subTest(secret=secret, direction="input"), self.assertRaises(ReviewError) as error:
                self.adapter(lambda _: self.fail("Sensitive input must not be sent")).cluster(
                    packet["context"], packet["cases"], packet["identity_fields"])
            self.assertEqual("SENSITIVE_CONTENT", error.exception.code)
            self.assertFalse(error.exception.model_called)
            reply = {**self.packet["reply"], "reason": secret}
            with self.subTest(secret=secret, direction="output"), self.assertRaises(ReviewError) as error:
                self.invoke(self.adapter(lambda _: httpx.Response(200, json=chat(reply))))
            self.assertEqual("SENSITIVE_CONTENT", error.exception.code)
            self.assertTrue(error.exception.model_called)
            self.assertNotIn(secret, str(error.exception))

    def test_small_stream_arrivals_reach_the_whole_response_deadline(self):
        clock, consumed = [0.0], []
        class Trickle(httpx.SyncByteStream):
            def __iter__(self):
                for index in range(1000):
                    clock[0] += 0.5
                    consumed.append(index)
                    yield b"x"
        advisor = self.adapter(lambda _: httpx.Response(200, stream=Trickle()), SANITY_OA_AI_TIMEOUT="1")
        with patch("oa.sanity_ai_model.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(ReviewError) as error:
                self.invoke(advisor)
        self.assertEqual("AI_OUTCOME_UNKNOWN", error.exception.code)
        self.assertTrue(error.exception.model_called)
        self.assertEqual(3, len(consumed))

    def test_compressed_body_is_rejected_before_reading_or_decompression(self):
        test = self
        class Unreadable(httpx.SyncByteStream):
            def __iter__(self):
                test.fail("Compressed body must never be read")
                yield b""
        with self.assertRaises(ReviewError) as error:
            self.invoke(self.adapter(lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Unreadable())))
        self.assertEqual("INVALID_AI_RESPONSE", error.exception.code)
        self.assertTrue(error.exception.model_called)

    def test_truncated_refused_multiple_and_tool_outputs_are_rejected(self):
        good = chat(self.packet["reply"])
        invalid = []
        for key, value in (("finish_reason", "length"), ("finish_reason", "content_filter")):
            bad = copy.deepcopy(good)
            bad["choices"][0][key] = value
            invalid.append(bad)
        for key, value in (("refusal", "refused"), ("tool_calls", [{"id": "tool"}]), ("content", None)):
            bad = copy.deepcopy(good)
            bad["choices"][0]["message"][key] = value
            invalid.append(bad)
        invalid += [{**good, "choices": good["choices"] * 2}, {**good, "error": {"private": "details"}}]
        for envelope in invalid:
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=envelope)
            with self.subTest(envelope=envelope), self.assertRaises(ReviewError) as error:
                self.invoke(self.adapter(handler))
            self.assertEqual("INVALID_AI_RESPONSE", error.exception.code)
            self.assertEqual(1, len(calls))

    def test_responses_refusal_incomplete_and_non_message_execution_are_rejected(self):
        valid = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(self.packet["reply"])}]}]}
        cases = [{**valid, "status": "incomplete"}, {**valid, "incomplete_details": {"reason": "max_output_tokens"}},
                 {**valid, "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]},
                 {**valid, "output": [*valid["output"], {"type": "function_call"}]},
                 {**valid, "output": valid["output"] * 2}]
        for envelope in cases:
            with self.subTest(envelope=envelope), self.assertRaises(ReviewError) as error:
                self.invoke(self.adapter(lambda _: httpx.Response(200, json=envelope), SANITY_OA_AI_API="responses"))
            self.assertEqual("INVALID_AI_RESPONSE", error.exception.code)

    def test_duplicate_nonfinite_invalid_utf8_and_nonobject_json_are_rejected(self):
        raw_envelopes = [b'{"choices": [], "choices": []}', b'{"usage":NaN}', b'{"usage":1e999}',
                         b'\xff', '{}'.encode('utf-16'), b'[]']
        for raw in raw_envelopes:
            with self.subTest(raw=raw), self.assertRaises(ReviewError) as error:
                self.invoke(self.adapter(lambda _: httpx.Response(200, content=raw)))
            self.assertEqual("INVALID_AI_RESPONSE", error.exception.code)
        for content in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '[]', '"\\ud800"'):
            envelope = chat(self.packet["reply"])
            envelope["choices"][0]["message"]["content"] = content
            with self.subTest(content=content), self.assertRaises(ReviewError) as error:
                self.invoke(self.adapter(lambda _: httpx.Response(200, json=envelope)))
            self.assertEqual("INVALID_AI_RESPONSE", error.exception.code)

    def test_http_client_explicitly_disables_proxy_environment_and_redirects(self):
        original = httpx.Client
        calls = []
        def client(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)
        with patch("oa.sanity_ai_model.httpx.Client", side_effect=client):
            self.invoke(self.adapter(lambda _: httpx.Response(200, json=chat(self.packet["reply"]))))
        self.assertEqual(1, len(calls))
        self.assertFalse(calls[0]["trust_env"])
        self.assertFalse(calls[0]["follow_redirects"])


if __name__ == "__main__":
    unittest.main()
