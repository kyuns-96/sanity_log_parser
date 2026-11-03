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
