import copy
import importlib.util
import hashlib
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import httpx

from demo import demo, stage
from sanity_ai import AIAdapter, parse_output
from sanity_clustering import assignment, classify_record, scope_for, validate_ai_response
from sanity_cluster_store import ClusterStore, receive_cluster
from sanity_cluster_worker import process_cluster
from sanity_common import ReviewError, digest, pack_json, unpack_json, atomic_json, atomic_text
from sanity_db import Database, Queue, json_value
from sanity_privacy import SecretGuard
from tests_support import PgTestCase, ROOT


def rule(ref="r-one", fields=None):
    return {"identity_fields": fields or ["object", "check_point", "mode"], "where": {},
            "reason": "객체와 검사 지점 및 모드를 기준으로 구분", "evidence_ids": [ref]}


class FakeAI:
    calls = 0

    def metadata(self):
        return {"adapter": "TEST_DOUBLE", "model": "synthetic-only", "prompt_version": "test-v1"}

    def propose(self, value):
        type(self).calls += 1
        return {"schema_version": 1, "rules": [rule(value["sample"][0]["record_id"])],
                "explanation": "합성 응답: 전체 기록에 규칙을 시험 적용하세요.", "needs_clarification": False}


class ClusterUnitTests(unittest.TestCase):
    def test_empty_partial_conflicting_and_invalid_identity_never_force_assignment(self):
        fields = {"object": "top/A", "check_point": "P", "mode": "M", "code": "C", "severity": "ERROR"}
        self.assertIsNone(assignment(fields, {}, [])["group_id"])
        self.assertIsNotNone(assignment(fields, {}, [rule()])["group_id"])
        self.assertIsNone(assignment({**fields, "object": None}, {}, [rule()])["group_id"])
        self.assertEqual("RULE_CONFLICT", assignment(fields, {}, [rule(), rule(fields=["object"])])["reason"])
        self.assertNotEqual(assignment(fields, {}, [rule()])["group_id"],
                            assignment({**fields, "object": "top/B"}, {}, [rule()])["group_id"])

    def test_ai_cannot_cite_unsupplied_records_or_return_executable_rules(self):
        response = {"schema_version": 1, "rules": [rule()], "explanation": "test", "needs_clarification": False}
        with self.assertRaises(ReviewError):
            validate_ai_response(response, [{"record_id": "r-other"}])
        response["rules"][0]["script"] = "execute()"
        with self.assertRaises(ReviewError):
            validate_ai_response(response, [{"record_id": "r-one"}])

    def test_privacy_redacts_raw_known_tokens_passwords_and_nested_fields(self):
        guard = SecretGuard({"SANITY_AI_TOKEN": "unique-credential"})
        raw = 'object=top/A token=unique-credential password="hidden value" Authorization: Bearer abc123'
        clean = guard.text(raw)
        for secret in ("unique-credential", "hidden value", "abc123"):
            self.assertNotIn(secret, clean)
        self.assertIn("object=top/A", clean)
        self.assertEqual("[REDACTED]", guard.redact({"db_password": "hidden"})["db_password"])
        with self.assertRaises(ReviewError):
            guard.ensure_public({"reason": raw})

    def adapter(self, handler, **extra):
        return AIAdapter({"SANITY_AI_MODE": "api", "SANITY_AI_URL": "https://internal.example/v1/chat/completions",
                          "SANITY_AI_TOKEN": "test-private-token", "SANITY_AI_MODEL": "internal-model", **extra},
                         transport=httpx.MockTransport(handler))

    def test_openai_chat_request_and_response_without_leaking_configuration(self):
        def handler(request):
            self.assertEqual("Bearer test-private-token", request.headers["authorization"])
            data = json.loads(request.content)
            self.assertEqual({"model", "messages", "stream"}, set(data))
            self.assertEqual("internal-model", data["model"])
            self.assertEqual("system", data["messages"][0]["role"])
            self.assertNotIn("test-private-token", request.content.decode())
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
        self.assertEqual({"ok": True}, self.adapter(handler).propose({"sample": []}))

    def test_responses_mode_json_option_is_explicit(self):
        def handler(request):
            value = json.loads(request.content)
            self.assertEqual({"type": "json_object"}, value["text"]["format"])
            self.assertFalse(value["store"])
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"ok":true}'}]}]})
        self.assertEqual({"ok": True}, self.adapter(handler, SANITY_AI_API="responses", SANITY_AI_JSON_MODE="on").propose({}))

    def test_failed_truncated_duplicate_and_refused_outputs_are_not_retried(self):
        for envelope in ({"choices": [{"finish_reason": "length", "message": {"content": '{}'}}]},
                         {"choices": [{"finish_reason": "stop", "message": {"refusal": "no", "content": '{}'}}]},
                         {"choices": [{"finish_reason": "stop", "message": {"content": '{"a":1,"a":2}'}}]}):
            calls = []
            def handler(request):
                calls.append(1)
                return httpx.Response(200, json=envelope)
            with self.assertRaises(ReviewError):
                self.adapter(handler).propose({})
            self.assertEqual(1, len(calls))
        with self.assertRaises(ReviewError):
            AIAdapter({})

    def test_cli_contract_and_private_environment_do_not_pass_db_credentials(self):
        script = "import sys,json,os; r=json.load(sys.stdin); print(json.dumps({'schema':r['schema_version'],'db_present':'SANITY_VWP_DSN' in os.environ,'token_present':'SANITY_AI_TOKEN' in os.environ}))"
        adapter = AIAdapter({"SANITY_AI_MODE": "cli", "SANITY_AI_MODEL": "test", "SANITY_AI_TOKEN": "synthetic-token",
                             "SANITY_AI_ARGV": json.dumps([sys.executable, "-c", script])})
        with patch.dict(os.environ, {"SANITY_VWP_DSN": "password=do-not-forward"}):
            self.assertEqual({"schema": 1, "db_present": False, "token_present": True}, adapter.propose({}))

    def test_invalid_url_token_server_error_and_timeout_do_not_expose_configuration(self):
        for config in ({"SANITY_AI_URL": "https://internal.example:invalid"},
                       {"SANITY_AI_TOKEN": "token\r\nheader: injected"}):
            with self.assertRaises(ReviewError) as error:
                self.adapter(lambda _: self.fail("Invalid configuration must not call API"), **config).propose({})
            self.assertEqual("AI_CONFIG", error.exception.code)
            self.assertNotIn("internal.example", str(error.exception))
        for response in (httpx.Response(503, text="server-private-password"), httpx.Response(307, headers={"Location": "https://other.invalid"})):
            calls = []
            def handler(request):
                calls.append(1)
                return response
            with self.assertRaises(ReviewError) as error:
                self.adapter(handler).propose({})
            self.assertEqual(1, len(calls))
            self.assertNotIn("server-private-password", str(error.exception))
        def timeout(request):
            raise httpx.ReadTimeout("secret URL details", request=request)
        with self.assertRaises(ReviewError) as error:
            self.adapter(timeout).propose({})
        self.assertEqual("AI_OUTCOME_UNKNOWN", error.exception.code)
        self.assertNotIn("secret URL", str(error.exception))
        with self.assertRaises(ReviewError):
            parse_output(b'{"value":1e999}')


class ClusterDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.receipt = demo(Path(self.temp.name) / "source")
        self.bid = self.receipt["bundle_id"]
        self.uid = "u-numeric"

    def request(self, revision=0, mode="AI", rules=None, instruction="", rid=None):
        with Database("oa") as db, db.transaction():
            return ClusterStore(db, self.bid, self.uid, lock=True).request(rid or uuid.uuid4(), revision, instruction, rules, mode, "tester")

    def receive(self, jid):
        for _ in range(100):
            if receive_cluster(jid)["status"] == "NO_MESSAGE":
                return
        self.fail("Queue did not drain")

    def view(self):
        with Database("oa") as db, db.transaction(readonly=True):
            return ClusterStore(db, self.bid, self.uid).view()

    def action(self, action, jid, revision, rid=None, **kwargs):
        with Database("oa") as db, db.transaction():
            return ClusterStore(db, self.bid, self.uid, lock=True).action(rid or uuid.uuid4(), revision, action, jid,
                    "전체 시험 결과 확인", "tester", **kwargs)

    def complete_and_confirm(self, jid, revision=0):
        self.assertEqual("COMPLETE", process_cluster(jid, FakeAI)["status"])
        self.receive(jid)
        revision = self.action("APPLY", jid, revision)["revision"]
        with Database("oa") as db:
            members = ClusterStore(db, self.bid, self.uid).members(jid)["items"]
        for member in members:
            for candidate in member["candidates"]:
                revision = self.action("CANDIDATE", jid, revision, record_id=member["record_id"],
                    group_id=candidate["group_id"], decision="CONFIRM_MATCH")["revision"]
        result = self.action("CONFIRM", jid, revision)
        self.assertTrue(result["confirmed"])
        return result["revision"]

    def confirmed_pair(self):
        first_bundle = self.bid
        first = self.request()["job_id"]
        revision = self.complete_and_confirm(first)
        self.action("SAVE_RULES", first, revision)
        self.next_report()
        second = self.request(mode="AUTO")["job_id"]
        self.complete_and_confirm(second)
        return first_bundle, first, self.bid, second

    def test_reclassifying_ancestor_excludes_self_dependencies_and_new_runs_recover(self):
        first_bundle, _, second_bundle, second = self.confirmed_pair()
        immutable = self.admin_query("SELECT record_id,payload,state FROM {oa} WHERE record_type IN ('RECEIVED_BUNDLE','REPORT') ORDER BY record_id").fetchall()
        self.bid = first_bundle
        request_id = uuid.uuid4()
        updated = self.request(revision=3, mode="AUTO", rid=request_id)["job_id"]
        self.assertEqual("ALREADY_REQUESTED", self.request(revision=3, mode="AUTO", rid=request_id)["status"])
        job = self.view()["jobs"][0]
        self.assertTrue(job["catalog_exclusions"])
        self.assertEqual({"SELF_DEPENDENCY"}, {e["reason"] for e in job["catalog_exclusions"]})
        self.assertEqual({second_bundle}, {e["source_bundle"] for e in job["catalog_exclusions"]})
        self.assertTrue(job["references_current"])
        self.complete_and_confirm(updated, revision=3)
        self.assertEqual(immutable, self.admin_query("SELECT record_id,payload,state FROM {oa} WHERE record_type IN ('RECEIVED_BUNDLE','REPORT') ORDER BY record_id").fetchall())
        # B is no longer valid for approval, but withdrawing it and rebuilding is safe.
        self.bid = second_bundle
        stale = self.view()
        self.assertFalse(stale["jobs"][0]["references_current"])
        for action in ("CONFIRM", "SAVE_RULES"):
            with self.assertRaises(ReviewError) as error:
                self.action(action, second, stale["head"]["revision"])
            self.assertEqual("STALE_REFERENCE", error.exception.code)
        revision = self.action("DEFER", second, stale["head"]["revision"])["revision"]
        self.assertFalse(self.view()["head"]["confirmed"])
        rebuilt = self.request(revision=revision, mode="AUTO")["job_id"]
        self.complete_and_confirm(rebuilt, revision=revision)
        # A later report can use the rebuilt chain and confirm normally.
        self.next_report()
        third = self.request(mode="AUTO")["job_id"]
        self.complete_and_confirm(third)

    def test_stale_catalog_falls_back_to_older_valid_group_with_audited_exclusion(self):
        first_bundle = self.bid
        first = self.request()["job_id"]
        self.complete_and_confirm(first)
        self.action("SAVE_RULES", first, 2)
        different_rule = copy.deepcopy(self.view()["saved_rules"])
        different_rule[0]["identity_fields"] = ["object"]
        self.next_report()
        dependency_bundle = self.bid
        dependency = self.request(mode="RULES", rules=different_rule)["job_id"]
        dependency_revision = self.complete_and_confirm(dependency)
        self.next_report()
        stale_bundle = self.bid
        stale = self.request(mode="AUTO")["job_id"]
        self.complete_and_confirm(stale)
        self.bid = dependency_bundle
        self.action("DEFER", dependency, dependency_revision)
        self.next_report()
        with patch("sanity_cluster_store.CATALOG_PAGE_SIZE", 1):
            jid = self.request(mode="AUTO")["job_id"]
        with Database("oa") as db:
            payload = ClusterStore(db, self.bid, self.uid).get("CLUSTER_JOB", jid)["payload"]
        self.assertTrue(payload["known_groups"])
        self.assertEqual({first_bundle}, {g["source_bundle"] for g in payload["known_groups"]})
        self.assertEqual({stale_bundle}, {e["source_bundle"] for e in payload["catalog_exclusions"]})
        self.assertEqual({"STALE_REFERENCE"}, {e["reason"] for e in payload["catalog_exclusions"]})
        # Hitting a bound is an error, never an apparently complete partial catalog.
        before = self.admin_query("SELECT count(*) FROM {oa_to_vwp}").fetchone()[0]
        with patch("sanity_cluster_store.MAX_CATALOG_SOURCES", 1), self.assertRaises(ReviewError) as error:
            self.request(mode="AUTO")
        self.assertEqual("CATALOG_LIMIT", error.exception.code)
        self.assertEqual(before, self.admin_query("SELECT count(*) FROM {oa_to_vwp}").fetchone()[0])
        self.complete_and_confirm(jid)

    def test_existing_cyclic_jobs_cannot_apply_but_already_applied_jobs_can_be_rebuilt(self):
        first_bundle, _, _, _ = self.confirmed_pair()
        self.bid = first_bundle
        # Simulate an immutable job queued before the cycle-prevention fix.
        with patch.object(ClusterStore, "reference_problem", return_value=None):
            cyclic = self.request(revision=3, mode="AUTO")["job_id"]
        process_cluster(cyclic, FakeAI)
        self.receive(cyclic)
        with self.assertRaises(ReviewError) as error:
            self.action("APPLY", cyclic, 3)
        self.assertEqual("STALE_REFERENCE", error.exception.code)
        self.assertEqual(3, self.view()["head"]["revision"])
        # Emulate a head already changed by the old implementation in this isolated fixture.
        self.admin_query("UPDATE {oa} SET state=%s,version=4 WHERE bundle_id=%s AND record_type='CLUSTER_STATE'",
            (json_value({"revision": 4, "active_job": cyclic, "confirmed": False, "confirmed_revision": None}), self.bid))
        self.assertEqual(5, self.action("DEFER", cyclic, 4)["revision"])
        replacement = self.request(revision=5, mode="AUTO")["job_id"]
        self.complete_and_confirm(replacement, revision=5)

    def test_cold_start_apply_confirm_save_and_feedback_loop_preserves_sealed_review(self):
        before = self.admin_query("SELECT record_id,payload,state FROM {oa} WHERE record_type IN ('RECEIVED_BUNDLE','REPORT') ORDER BY record_id").fetchall()
        request_id = uuid.uuid4()
        jid = self.request(rid=request_id)["job_id"]
        self.assertEqual("ALREADY_REQUESTED", self.request(rid=request_id)["status"])
        self.assertEqual("COMPLETE", process_cluster(jid, FakeAI)["status"])
        self.assertEqual("NO_MESSAGE", process_cluster(jid, FakeAI)["status"])
        self.receive(jid)
        state = self.view()["jobs"][0]["state"]
        self.assertEqual("READY", state["phase"], state)
        self.assertEqual({"records": 4, "targets": 3, "assigned": 3, "groups": 2, "unresolved": 0, "candidate_records": 0}, state["stats"])
        self.assertEqual(1, self.action("APPLY", jid, 0)["revision"])
        self.assertTrue(self.action("CONFIRM", jid, 1)["confirmed"])
        self.action("SAVE_RULES", jid, 2)
        self.assertEqual(1, len(self.view()["saved_rules"]))
        next_job = self.request(revision=3, mode="AUTO", instruction="모드를 유지하고 다시 검토해줘")["job_id"]
        self.assertEqual("COMPLETE", process_cluster(next_job, FakeAI)["status"])
        self.receive(next_job)
        self.assertEqual(4, self.action("APPLY", next_job, 3)["revision"])
        self.assertFalse(self.view()["head"]["confirmed"])
        self.assertEqual(before, self.admin_query("SELECT record_id,payload,state FROM {oa} WHERE record_type IN ('RECEIVED_BUNDLE','REPORT') ORDER BY record_id").fetchall())

    def test_late_result_cannot_overwrite_human_revision_and_actions_are_idempotent(self):
        first, late = self.request()["job_id"], self.request()["job_id"]
        for jid in (first, late):
            self.assertEqual("COMPLETE", process_cluster(jid, FakeAI)["status"])
            self.receive(jid)
        rid = uuid.uuid4()
        self.action("APPLY", first, 0, rid)
        self.assertEqual("ALREADY_APPLIED", self.action("APPLY", first, 0, rid)["status"])
        with self.assertRaises(ReviewError) as error:
            self.action("APPLY", late, 1)
        self.assertEqual("REVISION_CONFLICT", error.exception.code)
        self.assertEqual(first, self.view()["head"]["active_job"])

    def test_no_rules_explicit_mode_leaves_unresolved_and_blocks_confirmation(self):
        jid = self.request(mode="RULES", rules=[])["job_id"]
        self.assertEqual("COMPLETE", process_cluster(jid, lambda: self.fail("AI must not run"))["status"])
        self.receive(jid)
        self.assertEqual(3, self.view()["jobs"][0]["state"]["stats"]["unresolved"])
        self.action("APPLY", jid, 0)
        with self.assertRaises(ReviewError) as error:
            self.action("CONFIRM", jid, 1)
        self.assertEqual("CLUSTER_BLOCKED", error.exception.code)

    def test_unconfigured_ai_is_a_failed_job_not_fake_success(self):
        jid = self.request()["job_id"]
        result = process_cluster(jid, lambda: AIAdapter({}))
        self.assertEqual("AI_NOT_CONFIGURED", result["code"])
        self.receive(jid)
        self.assertEqual("FAILED", self.view()["jobs"][0]["state"]["phase"])
        self.assertEqual(0, self.view()["head"]["revision"])

    def test_final_before_parts_is_incomplete_then_becomes_ready(self):
        jid = self.request()["job_id"]
        process_cluster(jid, FakeAI)
        self.admin_query("UPDATE {vwp_to_oa} SET next_attempt_at=clock_timestamp()+interval '1 hour' WHERE message_type='CLUSTER_PART'")
        self.assertEqual("RECEIVED", receive_cluster(jid)["status"])
        self.assertEqual("RECEIVING", self.view()["jobs"][0]["state"]["phase"])
        self.admin_query("UPDATE {vwp_to_oa} SET next_attempt_at=clock_timestamp() WHERE message_type='CLUSTER_PART'")
        self.receive(jid)
        self.assertEqual("READY", self.view()["jobs"][0]["state"]["phase"])

    def test_model_out_of_scope_evidence_is_rejected_without_members(self):
        class InvalidAI(FakeAI):
            def propose(self, value):
                response = super().propose(value)
                response["rules"][0]["evidence_ids"] = ["r-invented"]
                return response
        jid = self.request()["job_id"]
        self.assertEqual("INVALID_AI_RESPONSE", process_cluster(jid, InvalidAI)["code"])
        self.receive(jid)
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='CLUSTER_MEMBER'").fetchone()[0])

    def test_member_forgery_rolls_back_entire_part(self):
        jid = self.request()["job_id"]
        process_cluster(jid, FakeAI)
        with Database("oa") as db:
            msg = Queue(db, "vwp_to_oa").get(jid + "-p-0")
        data = unpack_json(msg)
        data["members"][0]["record_id"] = "r-invented"
        packed = pack_json(data)
        self.admin_query("UPDATE {vwp_to_oa} SET payload_gzip=%s,payload_sha256=%s,uncompressed_bytes=%s WHERE message_id=%s",
                         (packed["payload_gzip"], packed["payload_sha256"], packed["uncompressed_bytes"], jid + "-p-0"))
        with self.assertRaises(ReviewError):
            receive_cluster(jid)
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa} WHERE record_type='CLUSTER_MEMBER'").fetchone()[0])

    def next_report(self, count=4, pass_a=False):
        spec = importlib.util.spec_from_file_location("cluster_collector", ROOT / "vwp-dev/main.py")
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        root = Path(self.temp.name) / uuid.uuid4().hex
        reports = root / "reports"
        reports.mkdir(parents=True)
        plan = collector.demo_plan("complete")
        plan["run"]["run_id"] = "run-" + uuid.uuid4().hex
        plan["inventory"]["units"] = plan["inventory"]["units"][:1]
        atomic_json(root / "plan.json", plan)
        rows = [f"top/{'A' if n % 2 == 0 else 'B'},P1,M1,CHECK-101,WARNING,{8 if pass_a and n % 2 == 0 else 12},ns\n" for n in range(count)]
        atomic_text(reports / "numeric.csv", "object,check_point,mode,code,severity,observed,unit\n" + "".join(rows) + f"# END rows={count}\n")
        collector.collect(root / "plan.json", reports, root / "collected")
        sent = json.loads(stage("vwp-bridge", "analyze", "--input", root / "collected/run.json"))
        received = json.loads(stage("oa-bridge", "receive", "--message-id", sent["message_id"]))
        self.bid = received["bundle_id"]

    def test_saved_rules_cross_run_candidates_pass_separation_and_stale_reference(self):
        source_bid = self.bid
        first = self.request()["job_id"]
        process_cluster(first, FakeAI)
        self.receive(first)
        self.action("APPLY", first, 0)
        self.action("CONFIRM", first, 1)
        self.action("SAVE_RULES", first, 2)
        self.next_report(pass_a=True)
        jid = self.request(mode="AUTO")["job_id"]
        self.assertEqual("COMPLETE", process_cluster(jid, lambda: self.fail("Saved rules should avoid AI"))["status"])
        self.receive(jid)
        with Database("oa") as db:
            members = ClusterStore(db, self.bid, self.uid).members(jid)["items"]
        self.assertEqual(4, len(members))
        self.assertTrue(all(m["candidates"] for m in members))
        for m in members:
            if m["check_status"] == "PASS":
                self.assertIsNone(m["group_id"])
            else:
                self.assertEqual(m["group_id"], m["candidates"][0]["group_id"])
        self.action("APPLY", jid, 0)
        with self.assertRaises(ReviewError) as error:
            self.action("CONFIRM", jid, 1)
        self.assertEqual("CANDIDATES_UNRESOLVED", error.exception.code)
        revision = 1
        for m in members:
            self.action("CANDIDATE", jid, revision, record_id=m["record_id"], group_id=m["candidates"][0]["group_id"], decision="CONFIRM_MATCH")
            revision += 1
        self.assertTrue(self.action("CONFIRM", jid, revision)["confirmed"])
        with Database("oa") as db, db.transaction():
            ClusterStore(db, source_bid, self.uid, lock=True).action(uuid.uuid4(), 3, "DEFER", first, "새 근거 재확인", "tester")
        with self.assertRaises(ReviewError) as error:
            self.action("SAVE_RULES", jid, revision + 1)
        self.assertEqual("STALE_REFERENCE", error.exception.code)
        with Database("oa") as db:
            after = ClusterStore(db, self.bid, self.uid).members(jid)["items"]
        self.assertTrue(all(m["candidates"][0]["needs_recheck"] for m in after))
        self.assertEqual([m["check_status"] for m in members], [m["check_status"] for m in after])

    def test_multichunk_resume_after_commit_does_not_recall_ai_or_duplicate_members(self):
        self.next_report(count=270)
        jid = self.request()["job_id"]
        original = Queue.renew
        class Interrupted(Exception):
            pass
        def interrupt_after_part(queue, message, lease_seconds=300):
            if queue.db.execute("SELECT 1 FROM {vwp} WHERE record_type='CLUSTER_PART' LIMIT 1").fetchone():
                raise Interrupted()
            return original(queue, message, lease_seconds)
        with patch.object(Queue, "renew", interrupt_after_part), self.assertRaises(Interrupted):
            process_cluster(jid, FakeAI)
        self.assertEqual(1, self.admin_query("SELECT count(*) FROM {vwp_to_oa} WHERE message_type='CLUSTER_PART'").fetchone()[0])
        self.admin_query("UPDATE {oa_to_vwp} SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE message_id=%s", (jid,))
        self.assertEqual("COMPLETE", process_cluster(jid, lambda: self.fail("Do not repeat AI on resume"))["status"])
        self.receive(jid)
        state = self.view()["jobs"][0]["state"]
        self.assertEqual("READY", state["phase"])
        self.assertEqual(3, state["received_parts"])
        self.assertEqual(270, state["stats"]["records"])
        with Database("oa") as db:
            page = ClusterStore(db, self.bid, self.uid).members(jid, 260, 20)
        self.assertEqual(10, len(page["items"]))

    def test_uncertain_ai_call_is_not_automatically_repeated(self):
        class InterruptedAI(FakeAI):
            def propose(self, value):
                raise RuntimeError("simulate process interruption")
        jid = self.request()["job_id"]
        with self.assertRaises(RuntimeError):
            process_cluster(jid, InterruptedAI)
        self.admin_query("UPDATE {oa_to_vwp} SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE message_id=%s", (jid,))
        result = process_cluster(jid, lambda: self.fail("Outcome unknown must not repeat AI"))
        self.assertEqual("AI_OUTCOME_UNKNOWN", result["code"])
        self.receive(jid)
        self.assertEqual("FAILED", self.view()["jobs"][0]["state"]["phase"])

    def test_heartbeat_cannot_extend_expired_or_reclaimed_lease(self):
        jid = self.request()["job_id"]
        with Database("vwp") as db:
            queue = Queue(db, "oa_to_vwp")
            old = queue.claim(("CLUSTER_REQUEST",), jid)
            queue.renew(old)
            self.admin_query("UPDATE {oa_to_vwp} SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE message_id=%s", (jid,))
            with self.assertRaises(ReviewError):
                queue.renew(old)
            new = queue.claim(("CLUSTER_REQUEST",), jid)
            with self.assertRaises(ReviewError):
                queue.renew(old)
            queue.renew(new)

    def test_manual_rule_cannot_cite_invented_evidence(self):
        jid = self.request(mode="RULES", rules=[rule("r-invented")])["job_id"]
        self.assertEqual("RULE_EVIDENCE_NOT_FOUND", process_cluster(jid, FakeAI)["code"])

    def test_raw_export_masks_credentials_preserving_private_bytes_and_source_hash(self):
        from sanity_exchange import request_evidence, respond_evidence, receive_evidence
        row = self.admin_query("SELECT record_id,entity_key,raw_bytes FROM {vwp} WHERE record_type='EVIDENCE' AND review_unit_id=%s LIMIT 1", (self.uid,)).fetchone()
        # Isolated fixture: emulate a secret outside the previously transmitted example excerpt.
        raw = row[2] + b' token=synthetic-private-value password="hidden password"'
        source_hash = hashlib.sha256(raw).hexdigest()
        self.admin_query("UPDATE {vwp} SET raw_bytes=%s,raw_sha256=%s WHERE record_id=%s", (raw, source_hash, row[0]))
        rid = uuid.uuid4()
        request_evidence(self.bid, self.uid, [row[1]], "원문 확인", rid)
        self.assertEqual("FULFILLED", respond_evidence(rid)["result"])
        receive_evidence(rid)
        with Database("oa") as db:
            value = unpack_json(Queue(db, "vwp_to_oa").get("response-" + str(rid)))["records"][0]
        self.assertTrue(value["redacted"])
        self.assertEqual(source_hash, value["source_raw_sha256"])
        self.assertEqual(hashlib.sha256(value["raw"].encode()).hexdigest(), value["raw_sha256"])
        self.assertNotIn("synthetic-private-value", value["raw"])
        self.assertNotIn("hidden password", value["raw"])
        self.assertEqual(raw, self.admin_query("SELECT raw_bytes FROM {vwp} WHERE record_id=%s", (row[0],)).fetchone()[0])

    def test_dashboard_classification_and_full_raw_are_authenticated_scoped_and_separate(self):
        from fastapi.testclient import TestClient
        from sanity_exchange import respond_evidence
        spec = importlib.util.spec_from_file_location("cluster_dashboard_test", ROOT / "oa-bridge/dashboard_api.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        token = "synthetic-only-dashboard-token-for-cluster-tests"
        with patch.dict(os.environ, {"SANITY_DASHBOARD_TOKEN": token, "SANITY_DASHBOARD_REVIEWER": "fixed-reviewer"}), TestClient(module.create_app(), base_url="http://127.0.0.1") as client:
            path = f"/api/bundles/{self.bid}/units/{self.uid}/clustering"
            self.assertEqual(401, client.get(path).status_code)
            client.headers["Authorization"] = "Bearer " + token
            body = {"request_id": str(uuid.uuid4()), "expected_revision": 0, "mode": "AI"}
            self.assertEqual(400, client.post(path, json={**body, "reviewer": "forged"}).status_code)
            result = client.post(path, json=body)
            self.assertEqual(200, result.status_code, result.text)
            jid = result.json()["job_id"]
            process_cluster(jid, FakeAI)
            self.assertEqual(200, client.post(path + f"/{jid}/receive", json={}).status_code)
            self.assertEqual("READY", client.get(path).json()["jobs"][0]["state"]["phase"])
            member = client.get(path + f"/{jid}/members").json()["items"][0]
            rid = str(uuid.uuid4())
            request = {"request_id": rid, "unit_id": self.uid, "record_ids": [member["record_id"]], "reason": "원문 확인"}
            self.assertEqual(200, client.post(f"/api/bundles/{self.bid}/evidence", json=request).status_code)
            self.assertEqual("FULFILLED", respond_evidence(rid)["result"])
            full = f"/api/evidence/{rid}/records/{member['record_id']}"
            self.assertEqual(409, client.get(full).status_code)
            client.post(f"/api/evidence/{rid}/receive", json={})
            value = client.get(full + "?start=0&length=5").json()
            self.assertEqual(5, len(value["raw"]))
            self.assertGreater(value["total_chars"], 5)
            self.assertEqual(404, client.get(f"/api/evidence/{rid}/records/r-other").status_code)
            self.assertEqual(400, client.get(full + "?length=16001").status_code)
            self.assertEqual(0, self.view()["head"]["revision"])
            client.headers.clear()
            self.assertEqual(401, client.get(full).status_code)
