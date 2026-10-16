"""Explicit local test-workspace entry point; company .env and SSO are never read."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config.get("environment") != "SYNTHETIC_WORKSPACE":
        raise SystemExit("Explicit synthetic workspace configuration required")
    # Existing SANITY_* / PG* / .env settings must never select a company endpoint.
    for key in list(os.environ):
        if key.startswith(("SANITY_", "DBAAS_", "DBASS_", "PG")):
            os.environ.pop(key)
    os.environ.update(config["settings"])
    if config.get("codex_mock_enabled") is True:
        from psycopg2.extensions import parse_dsn
        params = parse_dsn(config["settings"]["SANITY_OA_DSN"])
        if (config["settings"].get("SANITY_DB_ACCESS") != "postgresql"
                or config["settings"].get("SANITY_DB_SCHEMA") != "public"
                or params.get("host") != config["root"] or params.get("port") != "55439"
                or params.get("dbname") != "sanity_test_mock" or params.get("user") != "sanity_mock_oa"
                or not Path(config["root"]).name.startswith("sanity-mock-pg-")):
            raise SystemExit("Codex mock requires the restricted local synthetic fixture")
    os.chdir(ROOT)
    from oa.workspace_api import create_app
    import uvicorn
    uvicorn.run(create_app(origins=config["origins"], static_dir=config["static_dir"],
                          codex_mock_enabled=config.get("codex_mock_enabled") is True),
                host="127.0.0.1", port=config["port"], access_log=False, log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
