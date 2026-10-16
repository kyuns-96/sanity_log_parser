"""Management migration preserves installed data, ACLs and the original four tables."""
import uuid

from common.sanity_db import Database
from tests.support import PgTestCase, ROOT, psycopg2


class WorkspaceMigrationTests(PgTestCase):
    migrate_workspace = False

    def setUp(self):
        super().setUp()
        self.admin_query("INSERT INTO {oa}(record_id,record_type,project,run_id,bundle_id,payload) "
                         "VALUES (%s,'RECEIVED_BUNDLE','existing','existing-run',%s,'{{\"preserved\":true}}')",
                         (uuid.uuid4(), "b-" + "a" * 64))

    def migrate(self):
        code = (ROOT / 'db/005_workspace.sql').read_text().replace('public.', self.schema + '.')
        try:
            self.admin.execute(code)
        except psycopg2.Error:
            self.admin.execute('ROLLBACK')
            raise

    def original(self):
        return self.admin_query("SELECT record_id,record_type,project,run_id,bundle_id,payload,state,version,created_at,updated_at FROM {oa} ORDER BY record_id").fetchall()

    def test_existing_rows_oids_and_restricted_application_access_are_preserved(self):
        before = self.original()
        oids = self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid", (self.schema,)).fetchall()
        self.migrate()
        self.assertEqual(before, self.original())
        self.assertEqual(oids, self.admin.execute("SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace AND relkind='r' ORDER BY oid", (self.schema,)).fetchall())
        self.assertEqual(4, len(oids))
        for side in ('vwp', 'oa'):
            with Database(side): pass

    def test_a_changed_existing_kind_contract_rolls_back_the_entire_migration(self):
        from psycopg2 import sql
        self.admin.execute(sql.SQL('ALTER TABLE {} DROP CONSTRAINT sanity_oa_kind_v2, ADD CONSTRAINT sanity_oa_kind_v2 CHECK (record_type IS NOT NULL)').format(sql.Identifier(self.schema, 'sanity_oa_data')))
        before = self.original()
        with self.assertRaises(psycopg2.Error): self.migrate()
        self.assertEqual(before, self.original())
        self.assertEqual((0,), self.admin.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=%s AND column_name='workspace_key'", (self.schema,)).fetchone())
        self.assertEqual((3,), self.admin.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=%s AND table_name='sanity_oa_data' AND column_name IN ('project','run_id','bundle_id') AND is_nullable='NO'", (self.schema,)).fetchone())

    def test_reapplying_migration_fails_and_does_not_reset_existing_records(self):
        self.migrate()
        before = self.original()
        with self.assertRaises(psycopg2.Error): self.migrate()
        self.assertEqual(before, self.original())
