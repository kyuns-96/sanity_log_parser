"""Role selection must keep owner boundaries, failures and integrated coverage."""
from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from db.tests import run_mock as mock_runner
import run_tests as runner


class RoleRunnerTests(unittest.TestCase):
    def public_role_suite(self, role, **kwargs):
        with patch.object(runner, "ROLE_ADAPTER_DIRS", {name: () for name in runner.ROLES}):
            return runner.build_role_suite(role, **kwargs)

    def test_public_roles_are_nonempty_unique_db_free_and_keep_native_file_checks(self):
        scopes = {}
        for role in runner.ROLES:
            suite, company = self.public_role_suite(role)
            cases = list(runner.test_cases(suite))
            scopes[role] = [test.id() for test in cases]
            self.assertTrue(cases)
            self.assertEqual(len(cases), len(set(scopes[role])))
            self.assertFalse(any("_FailedTest" in name or "MigrationTests" in name or "100000" in name
                                 for name in scopes[role]))
            from tests.support import PgTestCase
            self.assertFalse(any(isinstance(test, PgTestCase) for test in cases))
        self.assertIn("tests.test_submission_contracts.SubmissionContractTests.test_hardlinks_symlinks_and_changed_snapshot_are_rejected", scopes["vwp"])
        self.assertIn("tests.test_submission_https.HttpsSubmissionTests.test_transport_config_is_explicit_https_and_receipts_are_bound_to_the_owner", scopes["vwp-bridge"])
        self.assertTrue(any("ConnectionApiTests" in name for name in scopes["oa"]))
        self.assertFalse(any("CollectorTests" in name for name in scopes["oa"] + scopes["vwp-bridge"]))
        self.assertFalse(any("VwpWorkerTests" in name for name in scopes["oa"] + scopes["vwp"]))

    def test_db_extension_keeps_role_contracts_and_has_explicit_small_db_scope(self):
        for role in ("oa", "vwp-bridge"):
            base, _ = self.public_role_suite(role)
            extended, _ = self.public_role_suite(role, require_db=True)
            base_ids = {test.id() for test in runner.test_cases(base)}
            ids = [test.id() for test in runner.test_cases(extended)]
            self.assertLess(base_ids, set(ids))
            self.assertEqual(len(ids), len(set(ids)))
            self.assertFalse(any("_FailedTest" in name or "MigrationTests" in name or "100000" in name for name in ids))
        with self.assertRaises(ValueError):
            runner.build_role_suite("vwp", require_db=True)

    def company_tree(self, root):
        package = "synthetic_role_" + uuid.uuid4().hex
        directories = {role: (f"{package}/{role.replace('-', '_')}",) for role in runner.ROLES}
        for role, (directory,) in directories.items():
            folder = root / directory
            folder.mkdir(parents=True)
            (folder / "helper.py").write_text("VALUE = True\n")
            (folder / "test_support.py").write_text("VALUE = True\n")
            (folder / "test_adapter.py").write_text(
                "import unittest\nfrom .helper import VALUE\n"
                "class AdapterTests(unittest.TestCase):\n"
                "    def test_local_adapter(self): self.assertTrue(VALUE)\n")
        self.addCleanup(lambda: [sys.modules.pop(name, None) for name in list(sys.modules)
                                 if name == package or name.startswith(package + ".")])
        return directories

    def test_company_discovery_is_owner_only_and_allows_identical_names_without_init(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directories = self.company_tree(root)
            with patch.object(runner, "ROOT", root), patch.object(runner, "ROLE_ADAPTER_DIRS", directories), \
                    patch.object(runner, "ROLE_TESTS", {role: () for role in runner.ROLES}), \
                    patch.object(sys, "path", [temp, *sys.path]):
                importlib.invalidate_caches()
                ids = []
                for role in runner.ROLES:
                    suite, company = runner.build_role_suite(role)
                    selected = [test.id() for test in runner.test_cases(suite)]
                    self.assertEqual(1, len(selected))
                    self.assertIn(role.replace('-', '_'), selected[0])
                    self.assertEqual(selected, company[0]["test_ids"])
                    result = unittest.TestResult()
                    suite.run(result)
                    self.assertTrue(result.wasSuccessful())
                    ids.extend(selected)
                self.assertEqual(3, len(set(ids)))

    def test_company_import_errors_are_reported_instead_of_omitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directories = self.company_tree(root)
            folder = root / directories["oa"][0]
            (folder / "test_adapter.py").write_text("raise RuntimeError('synthetic import error')\n")
            report = root / "result.json"
            with patch.object(runner, "ROOT", root), patch.object(runner, "ROLE_ADAPTER_DIRS", directories), \
                    patch.object(runner, "ROLE_TESTS", {"oa": ()}), \
                    patch.object(sys, "path", [temp, *sys.path]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(1, runner.main(["--role", "oa", "--report-json", str(report)]))
            data = json.loads(report.read_text())
            self.assertEqual(("oa", 1, 1, False), (data["role"], data["company_tests_selected"], data["errors"], data["successful"]))

    def test_company_test_file_with_zero_tests_cannot_claim_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directories = self.company_tree(root)
            (root / directories["oa"][0] / "test_adapter.py").write_text("VALUE = 1\n")
            with patch.object(runner, "ROOT", root), patch.object(runner, "ROLE_ADAPTER_DIRS", directories), \
                    patch.object(sys, "path", [temp, *sys.path]):
                with self.assertRaisesRegex(ValueError, "no tests"):
                    runner.build_role_suite("oa")

    def test_role_report_records_actual_scope_and_skip_is_not_success(self):
        for skipped in (False, True):
            def sample():
                if skipped:
                    raise unittest.SkipTest("synthetic skip")
            case = unittest.FunctionTestCase(sample)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "result.json"
                with patch.object(runner, "build_role_suite", return_value=(unittest.TestSuite([case]), [])) as build, \
                        patch.object(runner, "build_suite") as integrated, \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(int(skipped), runner.main(["--role", "oa", "--report-json", str(path)]))
                build.assert_called_once_with("oa", require_db=False)
                integrated.assert_not_called()
                data = json.loads(path.read_text())
                self.assertEqual(("quick", "oa", False, False, 1, 0),
                                 (data["profile"], data["role"], data["full_suite"], data["database_tests"],
                                  data["tests_run"], data["company_tests_selected"]))
                self.assertEqual(not skipped, data["successful"])

    def test_invalid_role_combinations_fail_before_loading_or_db_setup(self):
        for args in (["--role", "oa", "--profile", "full"], ["--role", "vwp", "--require-db"], ["--role", "unknown"],
                     ["--role", "oa", "--include", "tests.test_common"]):
            with self.subTest(args=args), patch.object(runner, "build_role_suite") as build, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    runner.main(args)
                build.assert_not_called()
        for args in (["--role", "vwp"], ["--role", "oa", "--profile", "regression"],
                     ["--role", "oa", "--include", "tests.test_common"]):
            with self.subTest(args=args), patch.object(mock_runner, "MockDatabase") as database, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    mock_runner.main(args)
                database.assert_not_called()

    def test_parallel_reports_cannot_mix_roles_or_db_modes(self):
        manifest = {"profile": "quick", "role": "oa", "database_tests": True, "test_ids": ["one"]}
        report = {**manifest, "shard": "1/1", "tests_total": 1, "tests_selected": 1, "tests_run": 1,
                  "failures": 0, "errors": 0, "skipped": 0, "successful": True, "failed_tests": []}
        combined = mock_runner.combine_shards(manifest, [report], [0])
        self.assertTrue(combined["successful"])
        self.assertEqual("oa", combined["role"])
        for defect in ({"role": "vwp-bridge"}, {"role": None}, {"database_tests": False}):
            combined = mock_runner.combine_shards(manifest, [{**report, **defect}], [0])
            self.assertFalse(combined["successful"])
            self.assertFalse(combined["complete"])

    def test_mock_role_runs_only_selected_tests_and_stops_database(self):
        for role in ("oa", "vwp-bridge"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                database = Mock(root=root)
                calls = []

                def execute(staged, environment, log, script, *args):
                    calls.append((script, args))
                    if script == "run_tests.py":
                        self.assertEqual(role, args[args.index("--role") + 1])
                        self.assertIn("--require-db", args)
                        (root / "tests.json").write_text(json.dumps({"profile": "quick", "role": role,
                            "database_tests": True, "tests_run": 1, "skipped": 0, "successful": True}))

                with patch.object(mock_runner, "pg_binaries", return_value=Path("/synthetic/bin")), \
                        patch.object(mock_runner, "MockDatabase", return_value=database), \
                        patch.object(mock_runner, "stage_source"), patch.object(mock_runner, "execute", side_effect=execute), \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(0, mock_runner.main(["--role", role, "--jobs", "1"]))
                database.stop.assert_called_once()
                self.assertEqual(["common.sanity_db", "common.sanity_db", "run_tests.py"], [script for script, _ in calls])

    def test_parallel_role_is_forwarded_to_discovery_and_each_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = Mock(root=root)
            database.worker_environment.return_value = {}
            manifest = {"profile": "quick", "role": "oa", "database_tests": True,
                        "test_ids": ["a", "b"], "tests_selected": 2}
            for index, name in enumerate(("a", "b"), 1):
                report = {**manifest, "test_ids": [name], "tests_total": 2, "tests_selected": 1,
                          "shard": f"{index}/2", "tests_run": 1, "failures": 0, "errors": 0,
                          "skipped": 0, "successful": True, "failed_tests": []}
                (root / f"tests-worker-{index}.json").write_text(json.dumps(report))

            def execute(staged, environment, log, script, *args):
                self.assertEqual("oa", args[args.index("--role") + 1])
                (root / "manifest.json").write_text(json.dumps(manifest))

            child = Mock()
            child.wait.return_value = 0
            child.poll.return_value = 0
            with patch.object(mock_runner, "execute", side_effect=execute), \
                    patch.object(mock_runner.subprocess, "Popen", return_value=child) as popen, redirect_stdout(io.StringIO()):
                report = mock_runner.parallel_regression(root, {}, database, "quick", 2, role="oa")
            self.assertTrue(report["successful"])
            self.assertEqual(2, report["tests_run"])
            self.assertEqual(2, popen.call_count)
            for call in popen.call_args_list:
                command = call.args[0]
                self.assertEqual("oa", command[command.index("--role") + 1])
                self.assertIn("--require-db", command)

    def test_serial_role_rejects_wrong_scope_and_still_stops_database(self):
        for defect in ({"role": "vwp-bridge"}, {"database_tests": False}):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                database = Mock(root=root)

                def execute(staged, environment, log, script, *args):
                    if script == "run_tests.py":
                        (root / "tests.json").write_text(json.dumps({"profile": "quick", "role": "oa",
                            "database_tests": True, "tests_run": 1, "skipped": 0, "successful": True, **defect}))

                with patch.object(mock_runner, "pg_binaries", return_value=Path("/synthetic/bin")), \
                        patch.object(mock_runner, "MockDatabase", return_value=database), \
                        patch.object(mock_runner, "stage_source"), patch.object(mock_runner, "execute", side_effect=execute), \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(1, mock_runner.main(["--role", "oa", "--jobs", "1"]))
                database.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
