"""Safety boundaries for the explicitly requested local PostgreSQL fixture."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import shutil
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mock_runner", ROOT / "db/tests/run_mock.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class MockRunnerTests(unittest.TestCase):
    def test_additional_scope_is_forwarded_once_in_a_single_serial_fixture(self):
        names = ['tests.test_common', 'tests.test_auth']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Mock(root=root)
            calls = []
            def execute(staged, environment, log, script, *args):
                calls.append((script, args))
                if script == 'run_tests.py':
                    start = args.index('--include') + 1
                    self.assertEqual(names, list(args[start:start + len(names)]))
                    (root / 'tests.json').write_text(json.dumps({'profile':'quick', 'include':names,
                        'tests_run':2, 'skipped':0, 'successful':True}))
            with patch.object(runner, 'pg_binaries', return_value=Path('/synthetic/bin')), \
                    patch.object(runner, 'MockDatabase', return_value=database) as create, \
                    patch.object(runner, 'stage_source'), patch.object(runner, 'execute', side_effect=execute), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(0, runner.main(['--jobs','1','--include',*names,names[0]]))
            self.assertEqual(1, create.call_count)
            database.stop.assert_called_once()
            self.assertEqual(1, sum(script == 'run_tests.py' for script, _ in calls))

    def test_additional_scope_is_bound_to_manifest_and_each_parallel_worker(self):
        names = ['tests.test_common']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);database = Mock(root=root)
            database.worker_environment.return_value = {}
            manifest, reports = self.shard_reports()
            manifest['include'] = names
            manifest['tests_selected'] = len(manifest['test_ids'])
            for index, report in enumerate(reports, 1):
                report['include'] = names
                (root/f'tests-worker-{index}.json').write_text(json.dumps(report))
            def discover(staged, environment, log, script, *args):
                self.assertEqual(names[0], args[args.index('--include') + 1])
                (root/'manifest.json').write_text(json.dumps(manifest))
            child = Mock();child.wait.return_value = 0;child.poll.return_value = 0
            with patch.object(runner, 'execute', side_effect=discover), \
                    patch.object(runner.subprocess, 'Popen', return_value=child) as popen, redirect_stdout(io.StringIO()):
                result = runner.parallel_regression(root, {}, database, 'full', 2, include=names)
            self.assertTrue(result['successful'])
            self.assertEqual(names, result['include'])
            for call in popen.call_args_list:
                command = call.args[0]
                self.assertEqual(names[0], command[command.index('--include') + 1])
            reports[1]['include'] = []
            self.assertFalse(runner.combine_shards(manifest, reports, [0,0])['complete'])

    def shard_reports(self):
        manifest = {"profile": "full", "test_ids": ["test-d", "test-a", "test-c", "test-b"]}
        reports = []
        for index, ids in enumerate((["test-a", "test-c"], ["test-b", "test-d"])):
            reports.append({"profile": "full", "shard": f"{index+1}/2", "tests_total": 4,
                "test_ids": ids, "tests_selected": 2, "tests_run": 2, "failures": 0,
                "errors": 0, "skipped": 0, "successful": True, "failed_tests": []})
        return manifest, reports

    def test_parallel_reports_require_exact_complete_nonduplicated_execution(self):
        manifest, reports = self.shard_reports()
        report = runner.combine_shards(manifest, reports, [0, 0])
        self.assertTrue(report["successful"])
        self.assertTrue(report["complete"])
        self.assertEqual((4, 0, 0, 0), tuple(report[key] for key in ("tests_run", "failures", "errors", "skipped")))
        empty=runner.combine_shards({"profile":"full","test_ids":[]},[],[])
        self.assertFalse(empty["successful"])
        self.assertFalse(empty["complete"])
        for defect in ("missing", "duplicate", "partial", "exit", "profile"):
            with self.subTest(defect=defect):
                manifest, reports = self.shard_reports()
                codes = [0, 0]
                if defect == "missing": reports[1] = None
                elif defect == "duplicate": reports[1] = reports[0]
                elif defect == "partial": reports[1]["tests_run"] -= 1
                elif defect == "exit": codes[1] = -9
                elif defect == "profile": reports[1]["profile"] = "quick"
                report = runner.combine_shards(manifest, reports, codes)
                self.assertFalse(report["successful"])
                self.assertFalse(report["complete"])
                self.assertTrue(report["worker_errors"])

    def test_parallel_failure_and_skip_counts_cannot_become_success(self):
        for key in ("failures", "errors", "skipped"):
            with self.subTest(key=key):
                manifest, reports = self.shard_reports()
                reports[1][key] = 1
                reports[1]["successful"] = key == "skipped"
                reports[1]["failed_tests"] = [] if key == "skipped" else ["test-d"]
                codes = [0, 0 if key == "skipped" else 1]
                report = runner.combine_shards(manifest, reports, codes)
                self.assertTrue(report["complete"])
                self.assertFalse(report["successful"])
                self.assertEqual(1, report[key])

    def test_fast_cluster_is_explicit_and_preserves_scram_and_private_socket(self):
        for durability in ("ephemeral", "durable"):
            with self.subTest(durability=durability), patch.object(runner.os, "geteuid", return_value=1000):
                database = runner.MockDatabase(Path("/synthetic/bin"), durability=durability)
                self.addCleanup(shutil.rmtree, database.root)
                database.pg = Mock()
                database.start()
                init = database.pg.call_args_list[0].args
                self.assertIn("--auth-local=scram-sha-256", init)
                self.assertIn("--auth-host=reject", init)
                self.assertEqual(durability == "ephemeral", "--no-sync" in init)
                start = database.pg.call_args_list[1].args
                options = start[start.index("-o") + 1]
                self.assertIn("listen_addresses=''", options)
                self.assertIn("unix_socket_permissions=0700", options)
                self.assertEqual(durability == "ephemeral", "fsync=off" in options)
                self.assertFalse((database.root / "init-password").exists())

    def test_invalid_worker_count_is_rejected_before_creating_a_database(self):
        for value in ("0", "17", "-1"):
            with self.subTest(jobs=value), patch.object(runner, "MockDatabase") as database, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    runner.main(["--jobs", value])
                database.assert_not_called()

    def test_failed_regression_stops_database_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Mock(root=root)
            report = {"profile": "quick", "tests_run": 9, "failures": 5, "errors": 2,
                      "skipped": 0, "successful": False}

            def execute(staged, environment, log, script, *args):
                if script == "run_tests.py":
                    (root / "tests.json").write_text(json.dumps(report))
                    raise runner.MockError("run_tests.py failed; see tests.log")

            with patch.object(runner, "pg_binaries", return_value=Path("/synthetic/bin")), \
                    patch.object(runner, "MockDatabase", return_value=database), \
                    patch.object(runner, "stage_source"), patch.object(runner, "execute", side_effect=execute), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(1, runner.main(["--jobs", "1"]))
            database.stop.assert_called_once()

    def test_failed_start_still_stops_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Mock(root=Path(temp))
            database.start.side_effect = runner.MockError("synthetic startup failure")
            with patch.object(runner, "pg_binaries", return_value=Path("/synthetic/bin")), \
                    patch.object(runner, "MockDatabase", return_value=database), \
                    patch.object(runner, "stage_source"), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(1, runner.main([]))
            database.stop.assert_called_once()
            database.setup.assert_not_called()

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
