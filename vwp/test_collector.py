import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("vwp_dev_main", Path(__file__).with_name("main.py"))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class CollectorTests(unittest.TestCase):
    def test_complete_snapshot_and_determinism(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            a = collector.demo(root / "a")
            b = collector.demo(root / "b")
            self.assertEqual(a, b)
            self.assertTrue(a["ready"])
            self.assertEqual(len(a["inventory"]["units"]), 3)
            self.assertEqual(len(a["files"]), 2)
            inventory = dict(a["inventory"])
            checksum = inventory.pop("sha256")
            self.assertEqual(checksum, collector.digest(inventory))
            for entry in a["files"]:
                path = root / "a" / entry["path"]
                self.assertEqual(entry["sha256"], collector.sha256_file(path))
                self.assertEqual(entry["size_bytes"], path.stat().st_size)

    def test_missing_and_unexpected_preserve_inventory_and_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = collector.demo(root / "source", "missing")
            reports = root / "source" / "reports"
            collector.atomic_text(reports / "unexpected.txt", "unregistered report\n")
            before = {p.name: p.read_bytes() for p in reports.iterdir()}
            result = collector.collect(root / "source" / "run.json", reports, root / "out")
            self.assertEqual(result["inventory"], original["inventory"])
            self.assertEqual(result["unexpected_files"], ["reports/unexpected.txt"])
            self.assertEqual(before, {p.name: p.read_bytes() for p in reports.iterdir()})
            self.assertFalse((root / "out" / "reports" / "summary.json").exists())

    def test_refuses_nonempty_output_and_nested_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector.demo(root / "source")
            with self.assertRaises(collector.ReviewError):
                collector.demo(root / "source")
            with self.assertRaises(collector.ReviewError):
                collector.collect(root / "source" / "run.json", root / "source" / "reports",
                                  root / "source" / "reports" / "nested")

    def test_accepts_empty_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            output.mkdir()
            collector.demo(output)
            self.assertTrue((output / "run.json").is_file())

    def test_report_changed_during_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector.demo(root / "source")
            copyfile = collector.shutil.copyfile

            def copy_then_change(source, destination):
                result = copyfile(source, destination)
                collector.atomic_text(source, "concurrent update\n")
                return result

            with mock.patch.object(collector.shutil, "copyfile", side_effect=copy_then_change):
                with self.assertRaises(collector.ReviewError) as caught:
                    collector.collect(root / "source" / "run.json", root / "source" / "reports",
                                      root / "out")
            self.assertEqual(caught.exception.code, "SOURCE_CHANGED")
            self.assertFalse((root / "out").exists())

    def test_rejects_symlink_and_leaves_no_ready_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector.demo(root / "source")
            (root / "source" / "reports" / "linked").symlink_to(root / "source" / "run.json")
            with self.assertRaises(collector.ReviewError):
                collector.collect(root / "source" / "run.json", root / "source" / "reports", root / "out")
            self.assertFalse((root / "out").exists())

    def test_rejects_duplicate_units_and_unsafe_paths(self):
        for mutation in ("duplicate", "unsafe"):
            plan = collector.demo_plan("complete")
            if mutation == "duplicate":
                plan["inventory"]["units"].append(plan["inventory"]["units"][0])
            else:
                plan["inventory"]["units"][0]["report"] = "reports/../outside"
            with self.subTest(mutation=mutation), self.assertRaises(collector.ReviewError):
                collector.validate_plan(plan)

    def test_scenarios_stay_in_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for scenario in ("missing", "malformed", "unsupported"):
                result = collector.demo(root / scenario, scenario)
                self.assertEqual(len(result["inventory"]["units"]), 3)
            malformed = (root / "malformed" / "reports" / "numeric.csv").read_text()
            self.assertNotIn("# END", malformed)
            unsupported = collector.read_json(root / "unsupported" / "run.json")
            self.assertEqual(unsupported["inventory"]["units"][1]["parser"], "vendor_summary_v99")


if __name__ == "__main__":
    unittest.main()
