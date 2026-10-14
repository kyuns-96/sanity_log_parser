"""Real PostgreSQL tests with separate restricted bridge accounts and isolated schemas."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from demo import demo
from sanity_common import ReviewError, pack_json, unpack_bundle, unpack_json
from sanity_db import Database, Queue, json_value
from sanity_exchange import request_evidence, respond_evidence, receive_evidence
from sanity_oa_store import OAStore
from sanity_workers import work
from tests_support import PgTestCase, ROOT


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


oa = module("oa_database_test", "oa-bridge/main.py")


class DatabaseTests(PgTestCase):
    def test_psycopg2_types_and_nested_transactions_preserve_outer_work(self):
        import psycopg2
        value = uuid.uuid4()
        with Database("vwp") as db:
            self.assertIsInstance(db.conn, psycopg2.extensions.connection)
            row = db.conn.execute("SELECT %s::uuid AS id, %s::jsonb AS data, %s::bytea AS raw",
                                  (value, json_value({"한글": [True, None, 3]}), b"\x00\xff")).fetchone()
            self.assertEqual(value, row["id"])
            self.assertEqual({"한글": [True, None, 3]}, row["data"])
            self.assertEqual(b"\x00\xff", row["raw"])
            self.assertIsInstance(row["raw"], bytes)
            def enqueue(mid):
                Queue(db, "vwp_to_oa").enqueue({"message_id": mid, "message_type": "PING", "payload": {},
                                                "project": "test", "run_id": "nested-test"})
            with db.transaction():
                # Nest before the outer context has issued any statement, too.
                with db.transaction():
                    enqueue("nested-committed")
                with self.assertRaises(psycopg2.errors.DivisionByZero), db.transaction():
                    enqueue("nested-rolled-back")
                    db.conn.execute("SELECT 1/0")
            self.assertTrue(db.conn.autocommit)
            self.assertIsNotNone(Queue(db, "vwp_to_oa").get("nested-committed"))
            self.assertIsNone(Queue(db, "vwp_to_oa").get("nested-rolled-back"))

    def test_psycopg2_outer_rollback_and_readonly_do_not_leak(self):
        with Database("vwp") as db:
            with self.assertRaises(RuntimeError), db.transaction():
                with db.transaction():
                    Queue(db, "vwp_to_oa").enqueue({"message_id": "outer-rollback", "message_type": "PING",
                                                    "payload": {}, "project": "test", "run_id": "test"})
                raise RuntimeError("synthetic abort")
            self.assertTrue(db.conn.autocommit)
            self.assertIsNone(Queue(db, "vwp_to_oa").get("outer-rollback"))
            with db.transaction(readonly=True):
                self.assertEqual("on", db.conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"])
                with db.conn.cursor(name="type_test") as cursor:
                    cursor.execute("SELECT generate_series(1,3) AS n")
                    self.assertEqual([1, 2, 3], [row["n"] for row in cursor])
            self.assertEqual("off", db.conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"])

    def test_field_based_connection_for_both_restricted_roles(self):
        from psycopg2.extensions import parse_dsn
        for side in ("vwp", "oa"):
            # Synthetic local fixture; prefer its actual password (SCRAM mock).
            params = parse_dsn(os.environ[f"SANITY_{side.upper()}_DSN"])
            settings = {"SANITY_DB_HOST": params["host"], "SANITY_DB_PORT": params.get("port", "5432"),
                        "SANITY_DB_NAME": params["dbname"], "SANITY_DB_USER_NAME": params["user"],
                        "SANITY_DB_PASSWORD": params.get("password", "synthetic-fixture-password"),
                        "SANITY_DB_SSLMODE": params.get("sslmode", "disable"), "SANITY_DB_SCHEMA": self.schema}
            import sanity_db
            with self.subTest(side=side), patch.object(sanity_db, "load_environment", return_value=settings):
                with Database(side) as db:
                    self.assertEqual(params["user"], db.conn.execute("SELECT current_user AS name").fetchone()["name"])

    def ping(self, message_id="ping-test"):
        with Database("vwp") as db, db.transaction():
            Queue(db, "vwp_to_oa").enqueue({"message_id": message_id, "message_type": "PING", "payload": {},
                                            "project": "test", "run_id": "ping-test"})

    def example(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        receipt = demo(Path(temp.name) / "run")
        with Database("oa") as db:
            value = unpack_bundle(Queue(db, "vwp_to_oa").get(receipt["bundle_id"]))
        group = next(g for g in value["finding_groups"] if g["review_unit_id"] == "u-numeric")
        self.bundle_id = receipt["bundle_id"]
        self.record_ids = [g["record_id"] for g in group["examples"]]
        self.out = Path(temp.name) / "evidence.json"
        return value

    def request(self, request_id=None, **kwargs):
        request_id = request_id or str(uuid.uuid4())
        result = request_evidence(self.bundle_id, "u-numeric", self.record_ids,
                                  "Please check full source evidence", request_id, **kwargs)
        return request_id, result

    def test_missing_configuration_and_admin_login_fail_closed(self):
        with patch.dict(os.environ, {"SANITY_VWP_DSN": ""}):
            with self.assertRaises(ReviewError) as err:
                Database("vwp")
            self.assertEqual("DATABASE_NOT_CONFIGURED", err.exception.code)
        with patch.dict(os.environ, {"SANITY_VWP_DSN": os.environ["SANITY_TEST_ADMIN_DSN"]}):
            with self.assertRaises(ReviewError) as err:
                Database("vwp")
            self.assertEqual("DATABASE_PRIVILEGES", err.exception.code)

    def test_excess_column_rights_are_detected(self):
        self.admin_query("GRANT SELECT (payload) ON {vwp} TO sanity_oa_app")
        with self.assertRaises(ReviewError) as err:
            Database("oa")
        self.assertEqual("DATABASE_PRIVILEGES", err.exception.code)

    def test_required_indexes_accept_existing_names(self):
        from psycopg2 import sql
        indexes = self.admin.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind='i'", (self.schema,)).fetchall()
        for number, (name,) in enumerate(indexes):
            self.admin.execute(sql.SQL("ALTER INDEX {} RENAME TO {}").format(
                sql.Identifier(self.schema, name), sql.Identifier(f"existing_index_{number}")))
        self.ping()
        with Database("oa") as db:
            self.assertIsNotNone(Queue(db, "vwp_to_oa").get("ping-test"))

    def test_missing_required_index_is_rejected(self):
        from psycopg2 import sql
        self.admin.execute(sql.SQL("DROP INDEX {}").format(sql.Identifier(self.schema, "sanity_oa_event_uq")))
        with self.assertRaises(ReviewError) as err:
            Database("oa")
        self.assertEqual("DATABASE_SCHEMA", err.exception.code)



    def test_same_index_name_with_wrong_definition_is_rejected(self):
        from psycopg2 import sql
        cases = [
            "CREATE UNIQUE INDEX sanity_oa_event_uq ON {oa}(bundle_id) WHERE record_type='REVIEW_EVENT'",
            "CREATE UNIQUE INDEX sanity_oa_event_uq ON {oa}(bundle_id,review_revision) WHERE record_type='REVIEW_UNIT'",
            "CREATE INDEX sanity_oa_event_uq ON {oa}(bundle_id,review_revision) WHERE record_type='REVIEW_EVENT'",
            "CREATE UNIQUE INDEX sanity_oa_event_uq ON {vwp_to_oa}(bundle_id,correlation_id) WHERE message_type='ANALYSIS_BUNDLE'",
        ]
        for query in cases:
            with self.subTest(query=query):
                self.admin.execute(sql.SQL("DROP INDEX {}").format(sql.Identifier(self.schema, "sanity_oa_event_uq")))
                # CREATE INDEX takes an unqualified name and uses its table's schema.
                self.admin_query(query)
                with self.assertRaises(ReviewError) as err:
                    Database("oa")
                self.assertEqual("DATABASE_SCHEMA", err.exception.code)

    def test_schema_create_right_is_rejected(self):
        from psycopg2 import sql
        self.admin.execute(sql.SQL("GRANT CREATE ON SCHEMA {} TO sanity_vwp_app").format(sql.Identifier(self.schema)))
        with self.assertRaises(ReviewError) as err:
            Database("vwp")
        self.assertEqual("DATABASE_PRIVILEGES", err.exception.code)

    def test_private_payload_and_sender_ack_cannot_be_modified(self):
        self.ping()
        cases = [("vwp", "UPDATE {vwp_to_oa} SET status='REJECTED'"),
                 ("oa", "UPDATE {vwp_to_oa} SET payload=%s"),
                 ("oa", "SELECT payload FROM {vwp}"),
                 ("vwp", "SELECT payload FROM {oa}"),
                 ("vwp", "DELETE FROM {vwp_to_oa}")]
        for side, query in cases:
            with self.subTest(query=query), self.assertRaises(ReviewError) as err:
                with Database(side) as db:
                    db.execute(query, (json_value({}),) if "%s" in query else None)
            self.assertEqual("DATABASE_ERROR", err.exception.code)

    def test_message_idempotence_and_conflict(self):
        self.ping()
        self.ping()
        self.assertEqual(1, self.admin_query("SELECT count(*) FROM {vwp_to_oa}").fetchone()[0])
        with self.assertRaises(ReviewError) as err:
            with Database("vwp") as db, db.transaction():
                Queue(db, "vwp_to_oa").enqueue({"message_id": "ping-test", "message_type": "PING", "payload": {"changed": True},
                                                "project": "test", "run_id": "ping-test"})
        self.assertEqual("MESSAGE_CONFLICT", err.exception.code)

    def test_two_workers_claim_distinct_rows(self):
        self.ping("ping-1")
        self.ping("ping-2")
        ready = threading.Barrier(2)
        def claim(_):
            with Database("oa") as db:
                ready.wait(timeout=10)
                message = Queue(db, "vwp_to_oa").claim(("PING",))
                return message["message_id"]
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual({"ping-1", "ping-2"}, set(pool.map(claim, range(2))))

    def test_expired_lease_cannot_ack_and_is_reclaimed(self):
        self.ping()
        with Database("oa") as db:
            queue = Queue(db, "vwp_to_oa")
            first = queue.claim(("PING",))
            self.admin_query("UPDATE {vwp_to_oa} SET lease_expires_at=clock_timestamp()-interval '1 second'")
            with self.assertRaises(ReviewError) as err, db.transaction():
                queue.finish(first)
            self.assertEqual("LEASE_LOST", err.exception.code)
            second = queue.claim(("PING",))
            self.assertNotEqual(first["lease_token"], second["lease_token"])
            self.assertEqual(2, second["attempt_count"])
            with self.assertRaises(ReviewError), db.transaction():
                queue.finish(first)
            with db.transaction():
                queue.finish(second)
            self.assertIsNone(queue.claim(("PING",)))

    def test_receive_ack_failure_rolls_back_oa_records_then_recovers(self):
        # Generate a valid message without receiving it first.
        collector = module("collector_database_test", "vwp-dev/main.py")
        bridge = module("vwp_database_test", "vwp-bridge/main.py")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        source = Path(temp.name) / "source"
        self.assertEqual(0, collector.main(["demo", "--out", str(source)]))
        _, receipt = bridge.analyze(source / "run.json")
        args = argparse.Namespace(message_id=receipt["message_id"])
        with patch.object(Queue, "finish", side_effect=ReviewError("LEASE_LOST", "test")):
            with self.assertRaises(ReviewError):
                oa.receive(args)
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa}").fetchone()[0])
        self.assertEqual("PROCESSING", self.admin_query("SELECT status FROM {vwp_to_oa}").fetchone()[0])
        self.admin_query("UPDATE {vwp_to_oa} SET lease_expires_at=clock_timestamp()-interval '1 second'")
        self.assertEqual("RECEIVED", oa.receive(args)["status"])
        self.assertEqual("ALREADY_RECEIVED", oa.receive(args)["status"])

    def test_concurrent_review_retry_creates_one_revision(self):
        self.example()
        rid = str(uuid.uuid4())
        ready = threading.Barrier(2)
        def review(_):
            ready.wait(timeout=10)
            return oa.review(argparse.Namespace(bundle_id=self.bundle_id, unit_id="u-numeric",
                reviewer="reviewer", conclusion="FAIL", reason="checked", request_id=rid))
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(review, range(2)))
        self.assertEqual({"REVIEWED", "ALREADY_REVIEWED"}, {r["status"] for r in results})
        self.assertEqual({1}, {r["review_revision"] for r in results})

    def test_evidence_request_response_roundtrip_is_idempotent(self):
        self.example()
        rid, first = self.request()
        self.assertEqual("REQUESTED", first["status"])
        self.assertEqual("ALREADY_REQUESTED", self.request(rid)[1]["status"])
        self.assertEqual("FULFILLED", respond_evidence(rid)["result"])
        self.assertEqual("ALREADY_RESPONDED", respond_evidence(rid, approve_export=True)["status"])
        received = receive_evidence(rid, self.out)
        self.assertEqual("FULFILLED", received["result"])
        self.assertEqual(len(self.record_ids), received["record_count"])
        self.assertTrue(self.out.is_file())
        self.assertEqual("ALREADY_RECEIVED", receive_evidence(rid)["status"])

    def test_default_worker_correlates_each_claimed_evidence_request(self):
        self.example()
        requests = {self.request()[0], self.request()[0]}
        handled = set()
        for _ in requests:
            result = work("vwp", once=True)
            self.assertEqual("FULFILLED", result["result"])
            rid = result["request_id"]
            self.assertIn(rid, requests - handled)
            handled.add(rid)
            with Database("oa") as db:
                response = Queue(db, "vwp_to_oa").get("response-" + rid)
            self.assertEqual("request-" + rid, response["correlation_id"])
            self.assertEqual(response["correlation_id"], unpack_json(response)["request_id"])
            self.assertEqual("FULFILLED", receive_evidence(rid)["result"])
            self.assertEqual("ALREADY_RESPONDED", respond_evidence(rid)["status"])
        self.assertEqual("NO_MESSAGE", work("vwp", once=True)["status"])
        self.assertEqual(2, self.admin_query("SELECT count(*) FROM {vwp_to_oa} WHERE message_type='EVIDENCE_RESPONSE'").fetchone()[0])

    def test_default_evidence_polling_rejection_and_expiry_remain_correlated(self):
        self.example()
        rid, _ = self.request()
        self.assertEqual("REJECTED", respond_evidence(approve_export=False)["result"])
        self.assertEqual("REJECTED", receive_evidence(rid)["result"])
        rid, req = self.request()
        self.admin_query("UPDATE {oa_to_vwp} SET expires_at=created_at+interval '1 microsecond' WHERE message_id=%s",
                         (req["message_id"],))
        self.assertEqual("EXPIRED", work("vwp", once=True)["result"])
        self.assertEqual("EXPIRED", receive_evidence(rid)["result"])
        self.record_ids.append("r-missing")
        rid, _ = self.request()
        self.assertEqual("REJECTED", work("vwp", once=True)["result"])
        self.assertEqual("REJECTED", receive_evidence(rid)["result"])

    def test_default_worker_recovers_an_expired_evidence_lease(self):
        self.example()
        rid, req = self.request()
        with Database("vwp") as db:
            Queue(db, "oa_to_vwp").claim(("EVIDENCE_REQUEST",), req["message_id"])
        self.admin_query("UPDATE {oa_to_vwp} SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE message_id=%s",
                         (req["message_id"],))
        self.assertEqual("FULFILLED", work("vwp", once=True)["result"])
        self.assertEqual("FULFILLED", receive_evidence(rid)["result"])
        self.assertEqual(("RECEIVED", 2), self.admin_query("SELECT status,attempt_count FROM {oa_to_vwp} WHERE message_id=%s",
                                                        (req["message_id"],)).fetchone())

    def test_evidence_explicit_rejection_and_expiry_are_final(self):
        self.example()
        rid, _ = self.request()
        self.assertEqual("REJECTED", respond_evidence(rid, approve_export=False)["result"])
        self.assertEqual("REJECTED", receive_evidence(rid)["result"])
        # New approval must use a new request: a rejected response is immutable.
        self.assertEqual("REJECTED", respond_evidence(rid, approve_export=True)["result"])
        rid, req = self.request()
        self.admin_query("UPDATE {oa_to_vwp} SET expires_at=created_at+interval '1 microsecond' WHERE message_id=%s",
                         (req["message_id"],))
        self.assertEqual("EXPIRED", respond_evidence(rid, approve_export=True)["result"])
        self.assertEqual("EXPIRED", receive_evidence(rid)["result"])

    def test_evidence_missing_record_rejects_whole_request(self):
        self.example()
        self.record_ids.append("r-not-found")
        rid, _ = self.request()
        self.assertEqual("REJECTED", respond_evidence(rid, approve_export=True)["result"])
        self.assertEqual(0, receive_evidence(rid)["record_count"])
        self.assertIsNone(self.admin_query("SELECT payload_gzip FROM {vwp_to_oa} WHERE message_type='EVIDENCE_RESPONSE'").fetchone()[0])

    def test_typed_response_forgery_is_rejected_without_traceback(self):
        self.example()
        rid, _ = self.request()
        respond_evidence(rid, approve_export=True)
        with Database("oa") as db:
            message = Queue(db, "vwp_to_oa").get("response-" + rid)
        value = unpack_json(message)
        value["records"][0]["record_id"] = []
        forged = pack_json(value)
        self.admin_query("UPDATE {vwp_to_oa} SET payload_gzip=%s,payload_sha256=%s,uncompressed_bytes=%s WHERE message_id=%s",
                         (forged["payload_gzip"], forged["payload_sha256"], forged["uncompressed_bytes"], "response-" + rid))
        with self.assertRaises(ReviewError) as err:
            receive_evidence(rid)
        self.assertEqual("INVALID_RESPONSE", err.exception.code)
        self.assertEqual("REJECTED", self.admin_query("SELECT status FROM {vwp_to_oa} WHERE message_id=%s", ("response-" + rid,)).fetchone()[0])

    def test_dashboard_schema_rejects_retired_jira_records_and_publication_states(self):
        self.example()
        columns = self.admin.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
            (self.schema, "sanity_oa_data")).fetchall()
        self.assertTrue({"part_number", "remote_id", "confirmed_at"}.isdisjoint({row[0] for row in columns}))
        before = self.admin_query("SELECT * FROM {oa} ORDER BY record_id").fetchall()
        for record_type in ("JIRA_ISSUE_TASK", "JIRA_COMMENT_TASK"):
            with self.subTest(record_type=record_type), self.assertRaises(ReviewError) as err:
                with Database("oa") as db, db.transaction():
                    OAStore(db).put(record_type, {}, "demo", "demo-complete", bundle_id=self.bundle_id)
            self.assertEqual("DATABASE_ERROR", err.exception.code)
        with self.assertRaises(ReviewError) as err:
            with Database("oa") as db, db.transaction():
                db.execute("UPDATE {oa} SET status='UNCERTAIN' WHERE record_type='RECEIVED_BUNDLE'")
        self.assertEqual("DATABASE_ERROR", err.exception.code)
        self.assertEqual(before, self.admin_query("SELECT * FROM {oa} ORDER BY record_id").fetchall())



if __name__ == "__main__":
    unittest.main()
