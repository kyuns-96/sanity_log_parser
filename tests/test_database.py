"""Real PostgreSQL tests with separate restricted bridge accounts and isolated schemas."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from devtools.demo import demo
from common.sanity_common import ReviewError, unpack_bundle
from common.sanity_db import Database, Queue, json_value
from oa.sanity_exchange import request_evidence
from tests.support import PgTestCase, ROOT


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


oa = module("oa_database_test", "oa/main.py")


class DatabaseTests(PgTestCase):

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


    def test_destructive_and_ddl_related_rights_are_rejected_in_restricted_mode(self):
        for side, table in (("vwp", "vwp_to_oa"), ("oa", "oa_to_vwp")):
            for right in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                with self.subTest(side=side, right=right):
                    self.admin_query(f"GRANT {right} ON {{{table}}} TO sanity_{side}_app")
                    try:
                        with self.assertRaises(ReviewError) as error:
                            Database(side)
                        self.assertEqual("DATABASE_PRIVILEGES", error.exception.code)
                    finally:
                        self.admin_query(f"REVOKE {right} ON {{{table}}} FROM sanity_{side}_app")


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


    def test_receive_ack_failure_rolls_back_oa_records_then_recovers(self):
        # Generate a valid message without receiving it first.
        collector = module("collector_database_test", "vwp/main.py")
        bridge = module("vwp_database_test", "vwp_bridge/main.py")
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


if __name__ == "__main__":
    unittest.main()
