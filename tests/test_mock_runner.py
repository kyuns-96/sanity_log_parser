"""Safety boundaries for the explicitly requested local PostgreSQL fixture."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mock_runner", ROOT / "db/tests/run_mock.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class MockRunnerTests(unittest.TestCase):
    def test_failed_start_still_stops_and_records_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Mock(root=Path(temp))
            database.start.side_effect = runner.MockError("synthetic startup failure")
            with patch.object(runner, "pg_binaries", return_value=Path("/synthetic/bin")), \
                    patch.object(runner, "MockDatabase", return_value=database), \
                    patch.object(runner, "stage_source"), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(1, runner.main([]))
            database.stop.assert_called_once()
            database.setup.assert_not_called()
            summary = json.loads((Path(temp) / "summary.json").read_text())
            self.assertEqual("FAILED", summary["status"])
            self.assertTrue(summary["db_stopped"])

    def test_shell_database_and_ai_configuration_is_not_inherited(self):
        source = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "SANITY_VWP_DSN": "private",
                  "SANITY_DB_PASSWORD": "private", "SANITY_AI_TOKEN": "private",
                  "DBAAS_TOKEN": "private", "DBASS_BASE_URL": "private", "PGSERVICE": "private",
                  "PGPASSWORD": "private", "PGHOST": "private", "PYTHONPATH": "/private",
                  "VITE_API_TOKEN": "private"}
        self.assertEqual({"PATH": "/usr/bin", "LANG": "C.UTF-8"}, runner.isolated_environment(source))

    def test_snapshot_includes_only_code_and_sql_not_env_private_docs_or_reports(self):
        from common.sanity_common import atomic_text
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "input", Path(temp) / "snapshot"
            for path in ("common/sanity_config.py", "db/form/fixture.sql", "db/tests/run_mock.py", "tests/test_one.py",
                         ".env", "dbaccess.md", "reports/customer.txt", "oa/dashboard/.env"):
                atomic_text(source / path, "synthetic fixture")
            runner.stage_source(source, target)
            self.assertEqual({"common/sanity_config.py", "db/form/fixture.sql", "db/tests/run_mock.py", "tests/test_one.py"},
                             {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()})
            self.assertEqual("synthetic fixture", (source / ".env").read_text())

    def test_snapshot_rejects_symlinked_code(self):
        from common.sanity_common import atomic_text
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "input"
            source.mkdir()
            outside = Path(temp) / "private"
            atomic_text(outside, "synthetic secret")
            (source / "module.py").symlink_to(outside)
            with self.assertRaises(RuntimeError):
                runner.stage_source(source, Path(temp) / "snapshot")

    def test_parser_snapshot_excludes_company_implementations_and_private_fixtures(self):
        from common.sanity_common import atomic_text
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "input", Path(temp) / "snapshot"
            public = {"vwp_bridge/integration/__init__.py", "vwp/integration/__init__.py", "oa/integration/__init__.py", "vwp_bridge/integration/parser_example.py",
                      "vwp_bridge/integration/public_parsers.py"}
            private = {"vwp_bridge/integration/corporate_parsers.py", "vwp_bridge/integration/company_parsers/item.py",
                       "vwp_bridge/integration/company_parsers/test_item.py", "vwp_bridge/integration/private/real_report.txt", "oa/integration/corporate_sso.py", "oa/integration/company_sso/sdk.py",
                       "vwp/integration/corporate_plan.py", "vwp/integration/company_plan/source.py",
                       "vwp_bridge/integration/corporate_clustering.py", "vwp_bridge/integration/company_clustering/sdk.py",
                       "vwp_bridge/integration/corporate_review.py", "vwp_bridge/integration/company_review/sdk.py",
                       "artifacts/parser-fixtures/real_report.txt"}
            for path in public | private:
                atomic_text(source / path, "synthetic fixture")
            runner.stage_source(source, target)
            self.assertEqual(public, {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()})
