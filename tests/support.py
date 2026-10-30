"""Isolated schemas in an explicitly configured disposable PostgreSQL test database."""
from pathlib import Path
import os
import unittest
from unittest.mock import patch
import uuid

try:
    import psycopg2
    from psycopg2 import sql
    from common.sanity_postgres import connect
except ImportError:
    psycopg2 = None

ROOT = Path(__file__).resolve().parents[1]


def migration_body(script):
    """Keep every fixture DDL statement inside one enclosing transaction."""
    lines = script.splitlines()
    begins = [i for i, line in enumerate(lines) if line.strip() == "BEGIN;"]
    commits = [i for i, line in enumerate(lines) if line.strip() == "COMMIT;"]
    if len(begins) != 1 or len(commits) != 1 or begins[0] >= commits[0]:
        raise ValueError("Fixture migrations must have exactly one transaction wrapper")
    return "\n".join(line for i, line in enumerate(lines) if i not in (begins[0], commits[0]))


@unittest.skipUnless(psycopg2 is not None and os.environ.get("SANITY_TEST_ADMIN_DSN"),
                     "PostgreSQL integration requires SANITY_TEST_ADMIN_DSN and both restricted app DSNs")
class PgTestCase(unittest.TestCase):
    migrate_clustering = True
    migrate_workspace = True
    migrate_workspace_reviews = True
    migrate_workspace_pipeline = True

    def setUp(self):
        self.admin = connect(os.environ["SANITY_TEST_ADMIN_DSN"], autocommit=True)
        self.addCleanup(self.admin.close)
        database = self.admin.execute("SELECT current_database()").fetchone()[0]
        if not database.startswith("sanity_test_"):
            raise RuntimeError("Integration tests require a dedicated sanity_test_* database")
        if not os.environ.get("SANITY_VWP_DSN") or not os.environ.get("SANITY_OA_DSN"):
            raise RuntimeError("Set both restricted app DSNs for the same test database")
        self.schema = "sanity_test_" + uuid.uuid4().hex
        commands = [sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema))]
        for name in ("sanity_vwp_data", "sanity_oa_data", "sanity_vwp_to_oa", "sanity_oa_to_vwp"):
            columns = (ROOT / "db/form" / f"{name}.columns.sql").read_text(encoding="utf-8")
            commands.append(sql.SQL("CREATE TABLE {}.{} (").format(
                sql.Identifier(self.schema), sql.Identifier(name)) + sql.SQL(columns) + sql.SQL(")"))
        for name in ("002_indexes.sql", "003_grants.sql", "004_clustering.sql", "005_workspace.sql", "006_workspace_reviews.sql", "007_workspace_pipeline.sql"):
            if name == "004_clustering.sql" and not self.migrate_clustering:
                continue
            if name == "005_workspace.sql" and (not self.migrate_clustering or not self.migrate_workspace):
                continue
            if name == "006_workspace_reviews.sql" and (not self.migrate_clustering or not self.migrate_workspace or not self.migrate_workspace_reviews):
                continue
            if name == "007_workspace_pipeline.sql" and (not self.migrate_clustering or not self.migrate_workspace or not self.migrate_workspace_reviews or not self.migrate_workspace_pipeline):
                continue
            script = (ROOT / "db" / name).read_text(encoding="utf-8")
            script = script.replace("public.", self.schema + ".").replace("SCHEMA public", "SCHEMA " + self.schema)
            commands.append(sql.SQL(migration_body(script)))
        # A fresh schema has no readers until setup completes. Execute all of
        # its original DDL once, preserving statements, order, ACLs and flags.
        # Tests of migration atomicity still execute the original SQL files.
        with self.admin.transaction():
            self.admin.execute(sql.SQL(";\n").join(commands))
        self.addCleanup(self._drop_own_schema)
        env = patch.dict(os.environ, {"SANITY_DB_SCHEMA": self.schema, "SANITY_DB_ACCESS": "postgresql"})
        env.start()
        self.addCleanup(env.stop)

    def _drop_own_schema(self):
        if os.environ.get("SANITY_KEEP_TEST_SCHEMA") != "1":
            self.admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def admin_query(self, query, params=None):
        names = {"vwp": "sanity_vwp_data", "oa": "sanity_oa_data", "vwp_to_oa": "sanity_vwp_to_oa", "oa_to_vwp": "sanity_oa_to_vwp"}
        return self.admin.execute(sql.SQL(query).format(**{key: sql.Identifier(self.schema, name) for key, name in names.items()}), params)
