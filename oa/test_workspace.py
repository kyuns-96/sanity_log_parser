"""Real PostgreSQL workspace authorization, persistence and PoR invariants."""
import importlib.util
from pathlib import Path
import sys


from common.sanity_common import digest

spec = importlib.util.spec_from_file_location("workspace_api_tests", Path(__file__).with_name("workspace_api.py"))
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
ORIGIN = "http://127.0.0.1"


def fixture():
    users = [{"id": f"user-{i:02d}", "name": f"Fixture {i}", "role": "TOP" if i < 4 else "블록 담당자"} for i in range(1, 11)]
    projects = [{"id": f"project-{i}", "name": name, "top_user": f"user-{i:02d}"} for i, name in enumerate(("Atlas", "Boreal", "Cygnus"), 1)]
    owners = (4, 5, 6, 7, 8, 9, 10, 4, 5)
    blocks, items = [], []
    for pi, project in enumerate(projects):
        for bi, name in enumerate(("CPU", "GPU", "DDR")):
            bid = project["id"] + "-" + name.lower()
            blocks.append({"id": bid, "project_id": project["id"], "name": name, "owner": f"user-{owners[pi * 3 + bi]:02d}", "revision": "SYNTHETIC_" + bid})
            index = pi * 3 + bi + 1
            items.append({"id": f"sample-{index:04d}", "project_id": project["id"], "block_id": bid,
                          "item_id": "gca_constraints", "family": "GCA", "rule": "CLK_0003", "check_status": "FAIL", "parse_status": "COMPLETE",
                          "parser": "test_parser", "record_count": 1, "records": [], "blockers": [],
                          "analysis_ready": True, "raw_report": "Synthetic test report for " + bid})
    data = {"schema_version": 1, "summary": {"origin": "SYNTHETIC_PUBLIC_REFERENCE", "actual_tools_run": False,
             "real_ai_called": False, "db_ingestion_verified": True}, "users": users, "projects": projects, "blocks": blocks, "items": items}
    data["dataset_sha256"] = digest(data)
    return data
