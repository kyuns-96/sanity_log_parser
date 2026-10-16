from tests.support import PgTestCase, ROOT, psycopg2


class WorkspacePipelineMigrationTests(PgTestCase):
    migrate_workspace_pipeline = False

    def migrate(self):
        try:
            self.admin.execute((ROOT / "db/007_workspace_pipeline.sql").read_text().replace("public.", self.schema + "."))
        except psycopg2.Error:
            self.admin.execute("ROLLBACK")
            raise

    def test_existing_records_table_identity_and_application_rights_are_preserved(self):
        from common.sanity_db import Database
        from oa.sanity_workspace import WorkspaceStore
        with Database("oa") as db, db.transaction(): WorkspaceStore(db).put("WS_CONFIG", "preserved", {"original": True})
        before = self.admin_query("SELECT record_id,payload,state,version FROM {oa} ORDER BY record_id").fetchall()
        tables = self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid", (self.schema,)).fetchall()
        self.migrate()
        self.assertEqual(before, self.admin_query("SELECT record_id,payload,state,version FROM {oa} ORDER BY record_id").fetchall())
        self.assertEqual(tables, self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid", (self.schema,)).fetchall())
        self.assertEqual(4, len(tables))
        with Database("oa"): pass
        with Database("vwp"): pass
        with self.assertRaises(psycopg2.Error): self.migrate()
        self.assertEqual(before, self.admin_query("SELECT record_id,payload,state,version FROM {oa} ORDER BY record_id").fetchall())
