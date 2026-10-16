#!/usr/bin/env python3
"""Create/start/stop the persistent isolated PostgreSQL workspace. Never read a company .env."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "artifacts/workspace-runtime.json"


def load_runner():
    spec = importlib.util.spec_from_file_location("workspace_pg_runner", ROOT / "db/tests/run_mock.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)


def setup(args):
    if args.config.exists():
        raise RuntimeError("Existing workspace configuration is retained; use start instead")
    runner = load_runner()
    database = runner.MockDatabase(runner.pg_binaries(args.pg_bin))
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    destination = args.runtime_dir / database.root.name
    shutil.move(database.root, destination)
    database.root, database.data = destination, destination / "data"
    staged = destination / "source"
    runner.stage_source(ROOT, staged)
    try:
        database.start()
        database.setup(staged)
        settings = {key: value for key, value in database.test_environment().items()
                    if key in ("SANITY_DB_ACCESS", "SANITY_DB_SCHEMA", "SANITY_OA_DSN", "SANITY_AI_MODE")}
        config = {"environment": "SYNTHETIC_WORKSPACE", "settings": settings,
                  "root": str(destination), "binaries": str(database.binaries),
                  "static_dir": str(ROOT / "oa-bridge/dashboard/dist"), "origins": args.origin,
                  "port": args.port, "library_path": database.env.get("LD_LIBRARY_PATH", "")}
        seed_config = destination / "seed-config.json"
        write_private(seed_config, settings)
        code = """import json,os,sys
from pathlib import Path
root,config,source=map(Path,sys.argv[1:])
sys.path.insert(0,str(root));os.environ.update(json.loads(config.read_text()))
from sanity_db import Database
from sanity_workspace import seed_workspace
with Database('oa') as db,db.transaction():
    result=seed_workspace(db,json.loads(source.read_text()))
print(json.dumps(result))
"""
        seed = destination / "seed_workspace.py"
        seed.write_text(code)
        try:
            with (destination / "seed.log").open("wb") as log:
                subprocess.run([sys.executable, str(seed), str(staged), str(seed_config), str(args.source.resolve())],
                               cwd=staged, env=database.env, stdout=log, stderr=subprocess.STDOUT, check=True)
        finally:
            seed_config.unlink(missing_ok=True)
        write_private(args.config, config)
        print(json.dumps({"status": "WORKSPACE_CREATED", "database": "ISOLATED_POSTGRESQL", "port": args.port}))
    except BaseException:
        database.stop()
        raise


def database_from_config(config):
    runner = load_runner()
    # Rehydrate without initializing or overwriting the existing cluster.
    database = runner.MockDatabase.__new__(runner.MockDatabase)
    database.binaries = Path(config["binaries"])
    database.root, database.data = Path(config["root"]), Path(config["root"]) / "data"
    database.env = runner.isolated_environment(os.environ)
    if config["library_path"]:
        database.env["LD_LIBRARY_PATH"] = config["library_path"]
    database.identity = {}
    if os.geteuid() == 0:
        import pwd
        account = pwd.getpwnam("nobody")
        database.identity = {"user": account.pw_uid, "group": account.pw_gid, "extra_groups": []}
    database.attempted_start = True
    return database


def is_workspace_process(pid, config):
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b'\0')
        expected = str(Path(config["root"]) / "source/oa-bridge/workspace_app.py").encode()
        return expected in arguments and b'--config' in arguments
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False


def start_database(database):
    if not (database.data / "postmaster.pid").exists():
        database.pg("pg_ctl", "-D", database.data, "-l", database.root / "server.log", "-o",
                    f"-k {database.root} -p 55439 -c listen_addresses='' -c unix_socket_permissions=0700",
                    "-w", "-t", "30", "start")


def start(args, config):
    database = database_from_config(config)
    start_database(database)
    pidfile = database.root / "api.pid"
    if pidfile.exists():
        pid = int(pidfile.read_text())
        if is_workspace_process(pid, config):
            print(json.dumps({"status": "ALREADY_RUNNING", "port": config["port"]})); return
    environment = database.env
    command = [sys.executable, str(database.root / "source/oa-bridge/workspace_app.py"), "--config", str(args.config.resolve())]
    with (database.root / "api.log").open("ab") as log:
        process = subprocess.Popen(command, cwd=database.root / "source", env=environment,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    pidfile.write_text(str(process.pid))
    from urllib.request import urlopen
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError("Workspace API failed; inspect its private api.log")
        try:
            with urlopen(f"http://127.0.0.1:{config['port']}/workspace", timeout=1) as response:
                if response.status == 200:
                    print(json.dumps({"status": "WORKSPACE_RUNNING", "port": config["port"], "persistent": True})); return
        except OSError:
            time.sleep(0.1)
    process.terminate()
    raise RuntimeError("Workspace API startup timed out")


def stop_api(config):
    database = database_from_config(config)
    pidfile = database.root / "api.pid"
    if pidfile.exists():
        pid = int(pidfile.read_text())
        if is_workspace_process(pid, config):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(50):
                if not is_workspace_process(pid, config): break
                time.sleep(0.1)
        pidfile.unlink()
    return database


def stop(config):
    stop_api(config).stop()
    print(json.dumps({"status": "STOPPED", "data_retained": True}))


def database_snapshot(args, config, database):
    # Run with a clean libpq environment, using only the restricted local fixture
    # login. Capture hashes/counts, never report bodies or credentials.
    code = '''import json,sys
from pathlib import Path
import psycopg2
from psycopg2.extensions import parse_dsn
config=json.loads(Path(sys.argv[1]).read_text())
params=parse_dsn(config["settings"]["SANITY_OA_DSN"])
if (not set(params)<={"host","port","dbname","user","password","sslmode","connect_timeout"}
    or params.get("host")!=config["root"] or params.get("port")!="55439"
    or params.get("dbname")!="sanity_test_mock" or params.get("user")!="sanity_mock_oa"):
    raise RuntimeError("Expected the restricted local synthetic fixture login")
with psycopg2.connect(**params) as conn, conn.cursor() as cursor:
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='sanity_oa_data' AND column_name IN ('workspace_review_report_ref','workspace_pipeline_report_ref')")
    columns={row[0] for row in cursor.fetchall()}
    migration=7 if "workspace_pipeline_report_ref" in columns else 6 if "workspace_review_report_ref" in columns else 5
    cursor.execute("SELECT oid,relname FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r' ORDER BY relname")
    tables=cursor.fetchall()
    if {row[1] for row in tables}!={"sanity_vwp_data","sanity_oa_data","sanity_vwp_to_oa","sanity_oa_to_vwp"}:
        raise RuntimeError("Expected four synthetic fixture tables")
    cursor.execute("SELECT count(*),md5(string_agg(jsonb_build_array(record_id,record_type,payload,state,version,created_at,updated_at)::text,'' ORDER BY record_id)) FROM public.sanity_oa_data")
    rows=cursor.fetchone()
print(json.dumps({"migration":migration,"tables":tables,"rows":rows}))
'''
    with (database.root / "upgrade-check.log").open("ab") as log:
        result = subprocess.run([sys.executable, "-c", code, str(args.config.resolve())],
                                env=database.env, cwd=database.root, stdout=subprocess.PIPE,
                                stderr=log, check=True, timeout=30, text=True)
    return json.loads(result.stdout)


def upgrade(args, config):
    """Owner-only offline migration for this helper's isolated synthetic cluster."""
    database = database_from_config(config)
    if (not database.root.name.startswith("sanity-mock-pg-") or database.root.is_symlink()
            or database.data.resolve().parent != database.root.resolve()
            or not (database.data / "PG_VERSION").is_file()
            or config["settings"].get("SANITY_DB_SCHEMA") != "public"
            or config["settings"].get("SANITY_DB_ACCESS") != "postgresql"):
        raise RuntimeError("Upgrade only supports the retained isolated synthetic fixture")
    stop_api(config)
    start_database(database)
    before = database_snapshot(args, config, database)
    if before["migration"] == 7:
        start(args, config)
        print(json.dumps({"status": "MIGRATION_CURRENT", "data_retained": True})); return
    database.stop()
    # Single-user mode needs no retained administrator password or auth changes.
    # Its default input terminator is a newline: submit this fixed migration as
    # one line, retaining the SQL's explicit BEGIN/COMMIT transaction.
    migrations = [(6, "006_workspace_reviews.sql"), (7, "007_workspace_pipeline.sql")]
    pending = [name for version, name in migrations if version > before["migration"]]
    migration = " ".join(line for name in pending for line in (ROOT / "db" / name).read_text().splitlines()
                         if not line.lstrip().startswith("--")) + "\n"
    try:
        with (database.root / "upgrade.log").open("ab") as log:
            subprocess.run([str(database.binaries / "postgres"), "--single", "-D", str(database.data),
                            "-c", "exit_on_error=on", "sanity_test_mock"], input=migration.encode(),
                           env=database.env, cwd=database.root, stdout=log, stderr=subprocess.STDOUT,
                           timeout=60, check=True, **database.identity)
        start_database(database)
        after = database_snapshot(args, config, database)
        if after["migration"] != 7 or before["tables"] != after["tables"] or before["rows"] != after["rows"]:
            raise RuntimeError("Migration preservation check failed; inspect private upgrade logs")
    finally:
        start(args, config)
    print(json.dumps({"status": "WORKSPACE_UPGRADED", "migrations": pending, "data_retained": True,
                      "rows_preserved": before["rows"][0], "tables_preserved": len(before["tables"])}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "start", "stop", "status", "restart", "upgrade"))
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--pg-bin")
    parser.add_argument("--runtime-dir", type=Path, default=Path("/var/lib/sanity-review-workspace"))
    parser.add_argument("--source", type=Path, default=ROOT / "oa-bridge/dashboard/public/sample-data/reports-library.json")
    parser.add_argument("--origin", action="append", default=[])
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.action == "setup":
        if not args.origin:
            parser.error("setup needs at least one explicit --origin")
        setup(args); return
    config = json.loads(args.config.read_text())
    if config.get("environment") != "SYNTHETIC_WORKSPACE":
        raise SystemExit("Wrong workspace configuration")
    if args.action == "restart":
        stop(config)
        database = database_from_config(config)
        runner = load_runner()
        staged = database.root / ("source-next-" + str(time.time_ns()))
        runner.stage_source(ROOT, staged)
        (database.root / "source").rename(database.root / ("source-previous-" + str(time.time_ns())))
        staged.rename(database.root / "source")
        start(args, config)
    elif args.action == "upgrade": upgrade(args, config)
    elif args.action == "start": start(args, config)
    elif args.action == "stop": stop(config)
    else:
        database = database_from_config(config)
        print(json.dumps({"database_running": (database.data / "postmaster.pid").exists(), "port": config["port"], "data_retained": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("WORKSPACE_START_FAILED type=" + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
