"""Apply the additive migration to existing form-created tables, with existing data."""
import hashlib
import uuid

from common.sanity_db import Database
from tests.support import PgTestCase, ROOT, psycopg2


class ClusteringMigrationTests(PgTestCase):
    migrate_clustering = False

    def setUp(self):
        super().setUp()
        self.analysis = uuid.uuid4()
        self.bundle = "b-" + "a" * 64
        self.admin_query("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,payload,status) "
                         "VALUES (%s,'RUN',%s,'existing-run','{{}}','SEALED')", (self.analysis, self.analysis))
        uid = uuid.uuid4()
        self.admin_query("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,review_unit_id,parent_id,payload,status) "
                         "VALUES (%s,'UNIT',%s,'u-one','u-one',%s,'{{}}','COMPLETE')", (uid, self.analysis, self.analysis))
        raw = b"original report evidence"
        self.admin_query("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,review_unit_id,parent_id,"
                         "payload,status,raw_bytes,raw_sha256) VALUES (%s,'EVIDENCE',%s,'r-one','u-one',%s,'{{}}','RECORDED',%s,%s)",
                         (uuid.uuid4(), self.analysis, uid, raw, hashlib.sha256(raw).hexdigest()))
        self.admin_query("INSERT INTO {oa}(record_id,record_type,project,run_id,bundle_id,payload) "
                         "VALUES (%s,'RECEIVED_BUNDLE','test','existing-run',%s,'{{}}')", (uuid.uuid4(), self.bundle))
        body = b"immutable human review report"
        self.admin_query("INSERT INTO {oa}(record_id,record_type,project,run_id,bundle_id,review_revision,payload,payload_bytes,payload_sha256) "
                         "VALUES (%s,'REPORT','test','existing-run',%s,0,'{{}}',%s,%s)",
                         (uuid.uuid4(), self.bundle, body, hashlib.sha256(body).hexdigest()))
        for key in ("vwp_to_oa", "oa_to_vwp"):
            self.admin_query("INSERT INTO {" + key + "}(message_id,message_type,project,run_id) "
                             "VALUES ('old-ping','PING','test','existing-run')")

    def migrate(self):
        script = (ROOT / "db/004_clustering.sql").read_text().replace("public.", self.schema + ".")
        try:
            self.admin.execute(script)
        except psycopg2.Error:
            self.admin.execute("ROLLBACK")
            raise

    def snapshot(self):
        values = {}
        for key in ("vwp", "oa", "vwp_to_oa", "oa_to_vwp"):
            order = "message_id" if "to" in key else "record_id"
            values[key] = self.admin_query("SELECT to_jsonb(t) - 'entity_key' - 'classification_revision' FROM {" + key +
                                          "} t ORDER BY " + order).fetchall() if key == "oa" else self.admin_query(
                                          "SELECT to_jsonb(t) FROM {" + key + "} t ORDER BY " + order).fetchall()
        return values

    def test_migration_preserves_all_rows_table_oids_and_restricted_access(self):
        before = self.snapshot()
        oids = self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid",
                                 (self.schema,)).fetchall()
        self.migrate()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(oids, self.admin.execute(
            "SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid", (self.schema,)).fetchall())
        self.assertEqual((0,), self.admin_query("SELECT count(*) FROM {oa} WHERE entity_key IS NOT NULL OR classification_revision IS NOT NULL").fetchone())
        for side in ("vwp", "oa"):
            with Database(side):
                pass

    def test_gui_generated_check_names_are_not_assumed(self):
        from psycopg2 import sql
        checks = self.admin.execute("SELECT c.conname,t.relname FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                                   "WHERE t.relnamespace=%s::regnamespace AND c.contype='c' ORDER BY c.oid", (self.schema,)).fetchall()
        for i, (name, table) in enumerate(checks):
            self.admin.execute(sql.SQL("ALTER TABLE {} RENAME CONSTRAINT {} TO {}").format(
                sql.Identifier(self.schema, table), sql.Identifier(name), sql.Identifier("gui_check_" + str(i))))
        before = self.snapshot()
        self.migrate()
        self.assertEqual(before, self.snapshot())

    def test_schema_drift_rolls_back_columns_indexes_and_constraint_replacements(self):
        from psycopg2 import sql
        names = self.admin.execute("SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                                  "WHERE t.relnamespace=%s::regnamespace AND t.relname='sanity_vwp_data' "
                                  "AND c.contype='c' AND c.conkey=ARRAY[2]::smallint[]", (self.schema,)).fetchall()
        self.assertEqual(1, len(names))
        self.admin.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}, ADD CHECK (record_type IN ('RUN','UNIT','FILE','GROUP','EVIDENCE','CUSTOM'))").format(
            sql.Identifier(self.schema, "sanity_vwp_data"), sql.Identifier(names[0][0])))
        before = self.snapshot()
        with self.assertRaises(psycopg2.Error):
            self.migrate()
        self.assertEqual(before, self.snapshot())
        self.assertEqual((0,), self.admin.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=%s "
                         "AND column_name IN ('entity_key','classification_revision') AND table_name='sanity_oa_data'", (self.schema,)).fetchone())
        self.assertEqual((0,), self.admin.execute("SELECT count(*) FROM pg_constraint WHERE connamespace=%s::regnamespace "
                         "AND conname LIKE '%%v2'", (self.schema,)).fetchone())

    def test_reapplication_fails_without_modifying_existing_data(self):
        self.migrate()
        before = self.snapshot()
        with self.assertRaises(psycopg2.errors.DuplicateColumn):
            self.migrate()
        self.assertEqual(before, self.snapshot())

    def test_new_kinds_validate_but_old_evidence_and_message_guards_remain(self):
        self.migrate()
        self.admin_query("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,review_unit_id,parent_id,payload,status) "
                         "VALUES (%s,'CLUSTER_JOB',%s,'job-one','u-one',%s,'{{}}','WORKING')",
                         (uuid.uuid4(), self.analysis, self.analysis))
        self.admin_query("INSERT INTO {oa}(record_id,record_type,project,run_id,bundle_id,review_unit_id,entity_key,payload) "
                         "VALUES (%s,'CLUSTER_STATE','test','existing-run',%s,'u-one','head','{{}}')", (uuid.uuid4(), self.bundle))
        self.admin_query("INSERT INTO {oa_to_vwp}(message_id,message_type,project,run_id,bundle_id,expires_at) "
                         "VALUES ('cluster-one','CLUSTER_REQUEST','test','existing-run',%s,clock_timestamp()+interval '1 hour')", (self.bundle,))
        self.admin_query("INSERT INTO {vwp_to_oa}(message_id,message_type,project,run_id,bundle_id,correlation_id) "
                         "VALUES ('cluster-result','CLUSTER_RESULT','test','existing-run',%s,'cluster-one')", (self.bundle,))
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.admin_query("UPDATE {vwp} SET raw_sha256=%s WHERE record_type='EVIDENCE'", ("f" * 64,))
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.admin_query("UPDATE {vwp_to_oa} SET message_type='EVIDENCE_RESPONSE' WHERE message_id='cluster-result'")
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.admin_query("UPDATE {vwp} SET review_unit_id=NULL WHERE record_type='CLUSTER_JOB'")
