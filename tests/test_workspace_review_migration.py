from tests_support import PgTestCase, ROOT, psycopg2


class WorkspaceReviewMigrationTests(PgTestCase):
    migrate_workspace_reviews = False

    def migrate(self):
        code = (ROOT / "db/006_workspace_reviews.sql").read_text().replace("public.", self.schema + ".")
        try:
            self.admin.execute(code)
        except psycopg2.Error:
            self.admin.execute("ROLLBACK")
            raise

    def test_upgrade_preserves_tables_installed_rows_and_restricted_roles(self):
        from sanity_db import Database
        from sanity_workspace import WorkspaceStore
        with Database("oa") as db, db.transaction():
            WorkspaceStore(db).put("WS_CONFIG", "preserved-config", {"migration_fixture": True})
        before = self.admin_query("SELECT record_id,payload,state,version FROM {oa} ORDER BY record_id").fetchall()
        oids = self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid", (self.schema,)).fetchall()
        self.migrate()
        self.assertEqual(before, self.admin_query("SELECT record_id,payload,state,version FROM {oa} ORDER BY record_id").fetchall())
        self.assertEqual(oids, self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid", (self.schema,)).fetchall())
        self.assertEqual(4, len(oids))
        with Database("oa"): pass
        with Database("vwp"): pass

    def test_wrong_prior_schema_rolls_back(self):
        from psycopg2 import sql
        self.admin.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT sanity_oa_kind_v3, ADD CONSTRAINT sanity_oa_kind_v3 CHECK (record_type IS NOT NULL)").format(sql.Identifier(self.schema, "sanity_oa_data")))
        with self.assertRaises(psycopg2.Error): self.migrate()
        self.assertEqual((0,), self.admin.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=%s AND column_name='workspace_review_report_ref'", (self.schema,)).fetchone())

    def test_reapplication_fails_without_changing_installed_rows_or_constraints(self):
        self.migrate()
        before = self.admin_query("SELECT record_id,payload,state,version FROM {oa} ORDER BY record_id").fetchall()
        constraints = self.admin.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace=%s::regnamespace ORDER BY conname", (self.schema,)).fetchall()
        with self.assertRaises(psycopg2.Error):
            self.migrate()
        self.assertEqual(before, self.admin_query("SELECT record_id,payload,state,version FROM {oa} ORDER BY record_id").fetchall())
        self.assertEqual(constraints, self.admin.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace=%s::regnamespace ORDER BY conname", (self.schema,)).fetchall())
