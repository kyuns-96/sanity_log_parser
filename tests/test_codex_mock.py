import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sanity_common import ReviewError, digest
from sanity_codex_mock import (CodexMockClusteringAdvisor, decode_events, fixture_report,
                               validate_receipt)


FIELDS = ["check_point", "mode", "object"]


def context(name="codex-mock-source"):
    report = fixture_report(name)
    scope = {key: report[key] for key in ("project_id", "block_id", "item_id", "family", "parser", "rule_version", "eco")}
    return {"report": report, "source_sha256": digest(report), "scope": {**scope, "layout": "POST"}}


def answer(value):
    ids = [record["id"] for record in value["report"]["records"]]
    value = {"groups": [{"id": "clock", "label": "클록 정의 누락", "record_ids": ids[:4]},
                       {"id": "io", "label": "입력 지연 누락", "record_ids": ids[4:]}],
            "reason": "객체가 달라도 같은 위반 내용을 기준으로 분류했습니다.", "reference_ids": []}
    value["group_rationales"] = [{"group_id": group["id"], "reason": group["label"] + ": 原文の共通パターンを確認するための合成試験の根拠です。",
        "evidence_ids": group["record_ids"][:2], "reference_groups": []} for group in value["groups"]]
    return value


def events(value):
    return (json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}})+"\n"+
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}})).encode()


class CodexMockTests(unittest.TestCase):
    def advisor(self, output):
        return CodexMockClusteringAdvisor(enabled=True, executable=sys.executable, transport=lambda *_: events(output))

    def test_explicit_opt_in_is_required(self):
        with self.assertRaises(ReviewError) as raised:
            CodexMockClusteringAdvisor()
        self.assertEqual("AI_NOT_CONFIGURED", raised.exception.code)

    def test_semantic_groups_and_names_preserve_all_distinct_objects(self):
        value = context()
        advisor = self.advisor(answer(value))
        result = advisor.propose(value, [], FIELDS)
        self.assertEqual((2, 4), (len(result["groups"]), len(result["groups"][0]["record_ids"])))
        self.assertEqual("클록 정의 누락", result["groups"][0]["label"])
        self.assertEqual(8, len({record["fields"]["object"] for record in value["report"]["records"]}))
        self.assertEqual(result, validate_receipt(advisor.receipt, value, [], FIELDS))

    def test_an_origin_marker_alone_cannot_send_arbitrary_report_text(self):
        value = context(); value["report"]["raw_report"] = "Non-fixture content"
        value["source_sha256"] = digest(value["report"])
        called = []
        advisor = CodexMockClusteringAdvisor(enabled=True, executable=sys.executable, transport=lambda *_: called.append(True))
        with self.assertRaises(ReviewError) as raised:
            advisor.propose(value, [], FIELDS)
        self.assertEqual("SYNTHETIC_ONLY", raised.exception.code)
        self.assertEqual([], called)
        self.assertIsNone(advisor.receipt)

    def test_missing_duplicate_and_foreign_members_are_not_accepted(self):
        value = context()
        for ids in (["missing"], [row["id"] for row in value["report"]["records"]][:-1],
                    [row["id"] for row in value["report"]["records"]]*2):
            with self.subTest(ids=len(ids)), self.assertRaises(ReviewError):
                self.advisor({"groups": [{"id": "g1", "label": "invalid", "record_ids": ids}],
                              "reason": "Invalid test response", "reference_ids": []}).propose(value, [], FIELDS)

    def test_tool_calls_failures_and_incomplete_streams_are_not_successful_advice(self):
        samples = [b'{"type":"item.started","item":{"type":"command_execution"}}',
                   b'{"type":"turn.failed"}', events(answer(context())).splitlines()[0],
                   b'{"type":"turn.completed","usage":{"input_tokens":-1}}']
        for sample in samples:
            with self.subTest(sample=sample[:40]), self.assertRaises(ReviewError):
                decode_events(sample)

    def test_receipt_is_bound_to_the_frozen_source_catalog_and_prompt(self):
        value = context(); advisor = self.advisor(answer(value)); advisor.propose(value, [], FIELDS)
        receipt = advisor.receipt
        for field in ("source_sha256", "case_catalog_sha256"):
            changed = copy.deepcopy(receipt); changed[field] = "0"*64
            with self.assertRaises(ReviewError) as raised:
                validate_receipt(changed, value, [], FIELDS)
            self.assertEqual("PROPOSAL_STALE", raised.exception.code)
        changed = copy.deepcopy(receipt); changed["metadata"]["input_sha256"] = "0"*64
        with self.assertRaises(ReviewError): validate_receipt(changed, value, [], FIELDS)

    def test_process_receives_only_stdin_fixture_and_no_database_or_api_secrets(self):
        value = context()
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary)/"fake-codex"
            executable.write_text(f"#!{sys.executable}\n" + '''import json,os,sys
assert not any(key.startswith(('SANITY_','DBAAS_','DBASS_','PG')) for key in os.environ)
assert 'OPENAI_API_KEY' not in os.environ
assert '--ignore-user-config' in sys.argv and '--ephemeral' in sys.argv
assert not os.path.exists('.env')
text=sys.stdin.read()
data=json.loads(text.split('UNTRUSTED_SYNTHETIC_DATA_JSON:\\n',1)[1])
result={'groups':[{'id':'mock','label':'合成テスト','record_ids':[row['id'] for row in data['report']['records']]}],
        'reason':'Synthetic transport test','reference_ids':[],
        'group_rationales':[{'group_id':'mock','reason':'Synthetic process transport test','evidence_ids':[data['report']['records'][0]['id']], 'reference_groups':[]}]}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(result)}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
''')
            executable.chmod(0o700)
            with patch.dict(os.environ, {"SANITY_OA_DSN": "never-forward-this-test-value", "OPENAI_API_KEY": "never-forward"}):
                advisor = CodexMockClusteringAdvisor(enabled=True, executable=str(executable))
                result = advisor.propose(value, [], FIELDS)
            self.assertEqual(8, len(result["groups"][0]["record_ids"]))

    def test_timeout_never_retries_the_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary)/"sleeping-codex"
            executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(10)\n")
            executable.chmod(0o700)
            advisor = CodexMockClusteringAdvisor(enabled=True, executable=str(executable), timeout=1)
            with self.assertRaises(ReviewError) as raised:
                advisor.propose(context(), [], FIELDS)
            self.assertEqual("AI_OUTCOME_UNKNOWN", raised.exception.code)
            self.assertIsNone(advisor.receipt)
