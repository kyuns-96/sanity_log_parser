"""Shared-login contract on disposable PostgreSQL schemas; never a company DB."""
import os
import secrets
import uuid
from unittest.mock import patch

from common.sanity_common import ReviewError
from common.sanity_db import Database, Queue, TABLES
from tests.support import PgTestCase


class SingleAccountDatabaseTests(PgTestCase):
    def setUp(self):
        super().setUp()
        from psycopg2 import sql
        from psycopg2.extensions import make_dsn

        self.login = "sanity_shared_" + uuid.uuid4().hex
        password = secrets.token_urlsafe(32)
        self.admin.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB "
                                   "NOCREATEROLE NOBYPASSRLS PASSWORD %s").format(sql.Identifier(self.login)),
                           (password,))
        self.addCleanup(self._drop_login)
        self.admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(self.schema), sql.Identifier(self.login)))
        self.admin.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
            sql.Identifier(self.schema), sql.Identifier(self.login)))
        dsn = make_dsn(os.environ["SANITY_TEST_ADMIN_DSN"], user=self.login, password=password)
        environment = patch.dict(os.environ, {"SANITY_VWP_DSN": dsn, "SANITY_OA_DSN": dsn,
                                              "SANITY_DB_ALLOW_SINGLE_ACCOUNT": "true"})
        environment.start()
        self.addCleanup(environment.stop)

    def _drop_login(self):
        from psycopg2 import sql
        self.admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(self.login)))
        self.admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(self.login)))

    def table_right(self, action, right, table):
        from psycopg2 import sql
        self.admin.execute(sql.SQL(action + " " + right + " ON {} " +
                                   ("TO" if action == "GRANT" else "FROM") + " {}").format(
            sql.Identifier(self.schema, table), sql.Identifier(self.login)))

    def assert_rejected(self, code="DATABASE_PRIVILEGES", side="oa"):
        with self.assertRaises(ReviewError) as error:
            with Database(side):
                pass
        self.assertEqual(code, error.exception.code)

    def test_shared_login_requires_opt_in_and_both_sides_check_all_four_tables(self):
        for side in ("vwp", "oa"):
            with self.subTest(side=side):
                with patch.dict(os.environ, {"SANITY_DB_ALLOW_SINGLE_ACCOUNT": "false"}):
                    self.assert_rejected(side=side)
                # This login has no membership in either named restricted role.
                with Database(side) as db:
                    self.assertEqual("single_account", db.privilege_mode)
                    self.assertEqual(self.login, db.conn.execute("SELECT current_user AS name").fetchone()["name"])
                    for key in TABLES:
                        self.assertEqual(0, db.execute("SELECT count(*) AS n FROM {" + key + "}").fetchone()["n"])

    def test_single_account_never_looks_up_the_restricted_role_names(self):
        # Fail if the connection tries to resolve either role name, as it would
        # on an installation where sanity_*_app roles do not exist at all.
        import common.sanity_db as sanity_db
        from common.sanity_postgres import connect as real_connect

        def connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            execute = connection.execute
            def checked_execute(query, params=None):
                self.assertFalse(params and any(isinstance(value, str) and value in
                    ("sanity_vwp_app", "sanity_oa_app") for value in params))
                return execute(query, params)
            connection.execute = checked_execute
            return connection

        with patch.object(sanity_db, "connect", connect):
            for side in ("vwp", "oa"):
                with self.subTest(side=side), Database(side):
                    pass

    def test_delete_is_optional_while_all_effective_read_insert_update_rights_are_required(self):
        for table in TABLES.values():
            self.table_right("REVOKE", "DELETE", table)
        for side in ("vwp", "oa"):
            with Database(side):
                pass
        for table in TABLES.values():
            for right in ("SELECT", "INSERT", "UPDATE"):
                with self.subTest(table=table, right=right):
                    self.table_right("REVOKE", right, table)
                    try:
                        self.assert_rejected()
                    finally:
                        self.table_right("GRANT", right, table)

    def test_each_ddl_related_table_right_is_still_forbidden(self):
        for table in TABLES.values():
            for right in ("TRUNCATE", "REFERENCES", "TRIGGER"):
                with self.subTest(table=table, right=right):
                    self.table_right("GRANT", right, table)
                    try:
                        self.assert_rejected()
                    finally:
                        self.table_right("REVOKE", right, table)

    def test_administrator_attributes_remain_forbidden(self):
        from psycopg2 import sql
        for enabled, disabled in (("SUPERUSER", "NOSUPERUSER"), ("CREATEROLE", "NOCREATEROLE"),
                                  ("BYPASSRLS", "NOBYPASSRLS")):
            with self.subTest(attribute=enabled):
                self.admin.execute(sql.SQL("ALTER ROLE {} " + enabled).format(sql.Identifier(self.login)))
                try:
                    self.assert_rejected()
                finally:
                    self.admin.execute(sql.SQL("ALTER ROLE {} " + disabled).format(sql.Identifier(self.login)))

    def test_membership_in_an_elevated_role_remains_forbidden(self):
        from psycopg2 import sql
        owner = self.admin.execute("SELECT current_user").fetchone()[0]
        self.admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(owner), sql.Identifier(self.login)))
        self.assert_rejected()

    def test_create_in_either_application_or_public_schema_remains_forbidden(self):
        from psycopg2 import sql
        for schema in (self.schema, "public"):
            with self.subTest(schema=schema):
                self.admin.execute(sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(self.login)))
                try:
                    self.assert_rejected()
                finally:
                    self.admin.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
                        sql.Identifier(schema), sql.Identifier(self.login)))

    def test_owning_an_application_table_remains_forbidden(self):
        from psycopg2 import sql
        self.admin.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(
            sql.Identifier(self.schema, TABLES["oa"]), sql.Identifier(self.login)))
        self.assert_rejected()

    def test_missing_table_remains_a_schema_error(self):
        self.admin_query("DROP TABLE {oa_to_vwp} CASCADE")
        self.assert_rejected("DATABASE_SCHEMA")

    def test_missing_required_unique_index_remains_a_schema_error(self):
        from psycopg2 import sql
        self.admin.execute(sql.SQL("DROP INDEX {}").format(sql.Identifier(self.schema, "sanity_oa_event_uq")))
        self.assert_rejected("DATABASE_SCHEMA")

    def test_queue_still_rejects_sender_state_fields_and_conflicting_messages(self):
        message = {"message_id": "single-account-ping", "message_type": "PING", "payload": {},
                   "project": "synthetic", "run_id": "single-account"}
        with Database("vwp") as db:
            queue = Queue(db, "vwp_to_oa")
            with self.assertRaises(ReviewError) as error:
                queue.enqueue({**message, "status": "RECEIVED"})
            self.assertEqual("INVALID_MESSAGE", error.exception.code)
            queue.enqueue(message)
            queue.enqueue(message)
            with self.assertRaises(ReviewError) as error:
                queue.enqueue({**message, "payload": {"changed": True}})
            self.assertEqual("MESSAGE_CONFLICT", error.exception.code)
            self.assertEqual({}, queue.get(message["message_id"])["payload"])

    def test_expired_leases_and_transaction_rollback_remain_enforced(self):
        with Database("vwp") as sender:
            Queue(sender, "vwp_to_oa").enqueue({"message_id": "single-account-lease", "message_type": "PING",
                "payload": {}, "project": "synthetic", "run_id": "single-account"})
        with Database("oa") as receiver:
            queue = Queue(receiver, "vwp_to_oa")
            first = queue.claim(("PING",))
            self.admin_query("UPDATE {vwp_to_oa} SET lease_expires_at=clock_timestamp()-interval '1 second'")
            with self.assertRaises(ReviewError) as error, receiver.transaction():
                queue.finish(first)
            self.assertEqual("LEASE_LOST", error.exception.code)
            second = queue.claim(("PING",))
            self.assertNotEqual(first["lease_token"], second["lease_token"])
            with self.assertRaises(RuntimeError), receiver.transaction():
                queue.finish(second)
                raise RuntimeError("Synthetic rollback")
            self.assertEqual("PROCESSING", queue.get(second["message_id"])["status"])
            with receiver.transaction():
                queue.finish(second)
            self.assertEqual("RECEIVED", queue.get(second["message_id"])["status"])
