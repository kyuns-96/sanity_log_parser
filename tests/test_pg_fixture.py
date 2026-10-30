"""Batched fixture setup must retain every original schema and ACL contract."""
import json
import uuid

from tests.support import PgTestCase, ROOT


class PgFixtureTests(PgTestCase):
    def metadata(self, schema):
        queries = (
            "SELECT c.relname,c.relkind,c.relacl::text,c.relrowsecurity,c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind IN ('r','p') ORDER BY c.relname",
            "SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
            "a.attacl::text,pg_get_expr(d.adbin,d.adrelid) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid "
            "LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
            "WHERE n.nspname=%s AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped "
            "ORDER BY c.relname,a.attnum",
            "SELECT c.relname,k.conname,k.condeferrable,k.condeferred,k.convalidated,pg_get_constraintdef(k.oid) "
            "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,k.conname",
            "SELECT c.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s ORDER BY c.relname,pg_get_indexdef(i.indexrelid)",
            "SELECT nspacl::text FROM pg_namespace WHERE nspname=%s",
        )
        rows = [self.admin.execute(query, (schema,)).fetchall() for query in queries]
        return json.dumps(rows, default=str).replace(schema, "<fixture>")

    def reference(self, schema, count):
        from psycopg2 import sql
        self.admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(self.admin.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        for name in ("sanity_vwp_data", "sanity_oa_data", "sanity_vwp_to_oa", "sanity_oa_to_vwp"):
            columns = (ROOT / "db/form" / f"{name}.columns.sql").read_text()
            self.admin.execute(sql.SQL("CREATE TABLE {}.{} (").format(sql.Identifier(schema), sql.Identifier(name))
                               + sql.SQL(columns + ")"))
        files = ("002_indexes.sql", "003_grants.sql") + (
            "004_clustering.sql", "005_workspace.sql", "006_workspace_reviews.sql", "007_workspace_pipeline.sql")[:count]
        for filename in files:
            script = (ROOT / "db" / filename).read_text().replace("public.", schema + ".").replace("SCHEMA public", "SCHEMA " + schema)
            self.admin.execute(script)

    def test_all_migration_flags_keep_original_columns_constraints_indexes_and_permissions(self):
        flags = ("migrate_clustering", "migrate_workspace", "migrate_workspace_reviews", "migrate_workspace_pipeline")
        for count in range(5):
            with self.subTest(migrations=count):
                reference = "sanity_test_ref_" + uuid.uuid4().hex
                self.reference(reference, count)
                case = PgTestCase()
                for index, flag in enumerate(flags):
                    setattr(case, flag, index < count)
                try:
                    case.setUp()
                    self.assertEqual(self.metadata(reference), self.metadata(case.schema))
                finally:
                    case.doCleanups()
