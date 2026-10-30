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
    def shard_reports(self):
        manifest = {"profile": "full", "test_ids": ["test-d", "test-a", "test-c", "test-b"]}
        reports = []
        for index, ids in enumerate((["test-a", "test-c"], ["test-b", "test-d"])):
            reports.append({"profile": "full", "shard": f"{index+1}/2", "tests_total": 4,
                "test_ids": ids, "tests_selected": 2, "tests_run": 2, "failures": 0,
                "errors": 0, "skipped": 0, "successful": True, "failed_tests": [],
                "timings": [{"test": name, "seconds": 0.1} for name in ids]})
        return manifest, reports

    def test_parallel_reports_require_exact_complete_nonduplicated_execution(self):
        manifest, reports = self.shard_reports()
        report = runner.combine_shards(manifest, reports, [0, 0], 1.2)
        self.assertTrue(report["successful"])
        self.assertTrue(report["complete"])
        self.assertEqual((4, 0, 0, 0), tuple(report[key] for key in ("tests_run", "failures", "errors", "skipped")))
        self.assertCountEqual(manifest["test_ids"], [row["test"] for row in report["timings"]])
        empty=runner.combine_shards({"profile":"full","test_ids":[]},[],[],0)
        self.assertFalse(empty["successful"])
        self.assertFalse(empty["complete"])
        for defect in ("missing", "duplicate", "partial", "exit", "profile"):
            with self.subTest(defect=defect):
                manifest, reports = self.shard_reports()
                codes = [0, 0]
                if defect == "missing": reports[1] = None
                elif defect == "duplicate": reports[1] = reports[0]
                elif defect == "partial": reports[1]["timings"].pop()
                elif defect == "exit": codes[1] = -9
                elif defect == "profile": reports[1]["profile"] = "quick"
                report = runner.combine_shards(manifest, reports, codes, 1.2)
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
                report = runner.combine_shards(manifest, reports, codes, 1.2)
                self.assertTrue(report["complete"])
                self.assertFalse(report["successful"])
                self.assertEqual(1, report[key])

    def test_fast_cluster_is_explicit_and_preserves_scram_and_private_socket(self):
        from types import SimpleNamespace
        # Runner wall budgets must count suspend on systems with BOOTTIME.
        for boot_clock in (True, False):
            clock = SimpleNamespace(monotonic=Mock(side_effect=[100, 107]))
            if boot_clock:
                clock.CLOCK_BOOTTIME = 9
                clock.clock_gettime = Mock(side_effect=[100, 131])
            with patch.object(runner, "time", clock):
                began = runner.elapsed_time()
                self.assertEqual(31 if boot_clock else 7, runner.elapsed_time() - began)
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

    def test_profiles_forward_scope_and_keep_large_probes_explicit(self):
        for profile, arguments, rows, groups, scenario_count in (
            ("quick", [], 100, 10, 1), ("regression", ["--profile", "regression"], 100, 10, 1),
            ("full", ["--profile", "full"], 10000, 100, 4),
        ):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                database = Mock(root=root, server_version_num=120001)
                calls = []

                def execute(staged, environment, log, script, *args):
                    calls.append((script, args))
                    if script == "run_tests.py":
                        (root / "tests.json").write_text(json.dumps({
                            "profile": profile, "tests_run": 1, "skipped": 0, "successful": True}))
                    elif script == "devtools/demo.py":
                        output = Path(args[args.index("--out") + 1])
                        output.mkdir()
                        (output / "receipt.json").write_text('{"coverage": {}}')
                    elif script == "tests/benchmark_postgresql.py":
                        log.write_text(json.dumps({"rows": args[1], "groups": args[3]}))

                with patch.object(runner, "pg_binaries", return_value=Path("/synthetic/bin")), \
                        patch.object(runner, "MockDatabase", return_value=database), \
                        patch.object(runner, "stage_source"), patch.object(runner, "execute", side_effect=execute), \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(0, runner.main(["--jobs", "1", *arguments]))
                database.stop.assert_called_once()
                result = json.loads((root / "summary.json").read_text())
                self.assertEqual(profile, result["profile"])
                self.assertEqual(profile == "full", result["full_suite"])
                self.assertEqual({"rows": rows, "groups": groups}, result["scale"])
                self.assertEqual(scenario_count, len(result["scenarios"]))
                test_call = next(args for script, args in calls if script == "run_tests.py")
                self.assertEqual(profile, test_call[test_call.index("--profile") + 1])
                self.assertIn("--require-db", test_call)

    def test_failed_regression_retains_counts_and_timings_in_the_failed_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Mock(root=root, server_version_num=180000)
            report = {"profile": "quick", "tests_run": 9, "failures": 5, "errors": 2,
                      "skipped": 0, "successful": False, "seconds": 42.0, "timings": []}

            def execute(staged, environment, log, script, *args):
                if script == "run_tests.py":
                    (root / "tests.json").write_text(json.dumps(report))
                    raise runner.MockError("run_tests.py failed; see tests.log")

            with patch.object(runner, "pg_binaries", return_value=Path("/synthetic/bin")), \
                    patch.object(runner, "MockDatabase", return_value=database), \
                    patch.object(runner, "stage_source"), patch.object(runner, "execute", side_effect=execute), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(1, runner.main(["--jobs", "1"]))
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual("FAILED", summary["status"])
            self.assertEqual(report, summary["tests"])
            self.assertTrue(summary["db_stopped"])
            database.stop.assert_called_once()

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
