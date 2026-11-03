"""Role selection must keep owner boundaries, failures and integrated coverage."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import run_tests as runner


class RoleRunnerTests(unittest.TestCase):


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


if __name__ == "__main__":
    unittest.main()
