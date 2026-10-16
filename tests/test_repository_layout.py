"""Server imports stay independent after moving the source into packages."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RepositoryLayoutTests(unittest.TestCase):
    def run_python(self, code, *, stdlib_only=False):
        command = [sys.executable, *(["-S"] if stdlib_only else []), "-c", code]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_collector_needs_only_standard_library_and_shared_contract(self):
        loaded = self.run_python("""import json,sys
import vwp.main
from common.sanity_integration_contracts import SanityPlanProvider
print(json.dumps(sorted(sys.modules)))
""", stdlib_only=True)
        self.assertFalse(any(name.split('.')[0] in {"oa", "vwp_bridge", "devtools", "psycopg2", "httpx", "fastapi"}
                             for name in loaded))

    def test_bridge_starts_without_oa_or_company_sdk(self):
        loaded = self.run_python("""import json,sys
import vwp_bridge.main, vwp_bridge.worker
print(json.dumps(sorted(sys.modules)))
""")
        self.assertFalse(any(name.split('.')[0] in {"oa", "devtools", "vwp"} for name in loaded))
        self.assertFalse(any(".integration.corporate_" in name for name in loaded))

    def test_oa_starts_without_vwp_model_or_external_mock(self):
        loaded = self.run_python("""import json,sys
import oa.dashboard_api, oa.worker
from oa.workspace_api import create_app
app=create_app(origins=['http://testserver'])
print(json.dumps(sorted(sys.modules)))
""")
        self.assertFalse(any(name.split('.')[0] in {"vwp_bridge", "vwp", "devtools"} for name in loaded))

    def test_shared_code_and_each_server_use_their_own_namespace(self):
        forbidden = {"common": {"oa", "vwp", "vwp_bridge", "devtools"},
                     "vwp": {"oa", "vwp_bridge", "devtools"},
                     "vwp_bridge": {"oa", "vwp", "devtools"},
                     "oa": {"vwp", "vwp_bridge"}}
        for package, blocked in forbidden.items():
            for path in (ROOT / package).glob("*.py"):
                if path.name.startswith("test_"):
                    continue
                for node in ast.walk(ast.parse(path.read_text())):
                    names = ([node.module] if isinstance(node, ast.ImportFrom) and node.module else
                             [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                    for name in names:
                        with self.subTest(file=str(path.relative_to(ROOT)), imported=name):
                            self.assertNotIn(name.split('.')[0], blocked)
                            self.assertFalse(name.startswith("sanity_"), "Legacy flat import survived the move")

    def test_configuration_stays_at_checkout_root_after_move(self):
        from common.sanity_config import ENV_FILE
        self.assertEqual(ROOT / ".env", ENV_FILE)
