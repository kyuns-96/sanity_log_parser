#!/usr/bin/env python3
"""Create a fresh, socket-only PostgreSQL fixture and test a credential-free code snapshot.

Never accepts an external DB endpoint. Requires local PostgreSQL 12.1+ binaries and
the project's Python requirements. Runtime artifacts are retained after shutdown.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATTERNS = ("*.py", "common/*.py", "devtools/*.py", "tests/*.py", "vwp/*.py", "vwp_bridge/*.py", "oa/*.py",
                   "bin/sanity",
                   "vwp/integration/__init__.py", "oa/integration/__init__.py",
                   "vwp_bridge/integration/__init__.py", "vwp_bridge/integration/parser_example.py",
                   "vwp_bridge/integration/public_parsers.py",
                   "db/*.sql", "db/form/*.sql", "db/tests/*.sql", "db/tests/*.py",
                   "db/portal/*.py", "db/portal/*.json")
DB_NAME = "sanity_test_mock"


class MockError(RuntimeError):
    """Only runner-authored diagnostics, safe to display without driver details."""


def isolated_environment(environment):
    # Also drop libpq defaults, so neither the server nor tests can select a
    # company endpoint/service/password from the invoking shell.
    return {key: value for key, value in environment.items()
            if not key.startswith(("SANITY_", "DBAAS_", "DBASS_", "PG", "VITE_"))
            and key not in ("PYTHONPATH", "PYTHONHOME")}


def stage_source(source, target):
    target.mkdir()
    for pattern in SOURCE_PATTERNS:
        for path in sorted(source.glob(pattern)):
            if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                raise MockError("Source snapshot must not follow symlinks")
            if path.is_file():
                dest = target / path.relative_to(source)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
                if path.relative_to(source).as_posix() == 'bin/sanity':
                    shutil.copymode(path,dest)


def pg_binaries(explicit):
    if explicit:
        directory = Path(explicit).resolve()
    else:
        executable = shutil.which("initdb")
        if executable:
            directory = Path(executable).resolve().parent
        elif shutil.which("pg_config"):
            directory = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
        else:
            raise MockError("Install PostgreSQL 12.1+ or pass --pg-bin /path/to/postgresql/bin")
    if not all((directory / name).is_file() and os.access(directory / name, os.X_OK)
               for name in ("initdb", "pg_ctl", "postgres")):
        raise MockError("PostgreSQL server binaries are missing; check --pg-bin")
    return directory


class MockDatabase:
    def __init__(self, binaries):
        self.binaries = binaries
        self.root = Path(tempfile.mkdtemp(prefix="sanity-mock-pg-", dir="/tmp"))
        self.data = self.root / "data"
        self.env = isolated_environment(os.environ)
        self.credentials = {name: secrets.token_urlsafe(32) for name in ("admin", "vwp", "oa")}
        self.identity = {}
        if os.geteuid() == 0:
            account = pwd.getpwnam("nobody")
            os.chown(self.root, account.pw_uid, account.pw_gid)
            self.identity = {"user": account.pw_uid, "group": account.pw_gid, "extra_groups": []}
        self.attempted_start = False

    def pg(self, name, *args, timeout=60):
        with (self.root / "setup.log").open("ab") as output:
            return subprocess.run([str(self.binaries / name), *map(str, args)], env=self.env,
                                  cwd=self.root, stdout=output, stderr=subprocess.STDOUT,
                                  timeout=timeout, check=True, **self.identity)

    def start(self):
        password_file = self.root / "init-password"
        descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(self.credentials["admin"] + "\n")
        if self.identity:
            os.chown(password_file, self.identity["user"], self.identity["group"])
        try:
            self.pg("initdb", "-D", self.data, "-U", "sanity_mock_admin", "--encoding=UTF8", "--locale=C",
                    "--auth-local=scram-sha-256", "--auth-host=reject", "--pwfile", password_file)
        finally:
            password_file.unlink()
        self.attempted_start = True
        # A unique private socket directory permits concurrent fixtures using the
        # same port number. No TCP listener, even on localhost.
        self.pg("pg_ctl", "-D", self.data, "-l", self.root / "server.log", "-o",
                f"-k {self.root} -p 55439 -c listen_addresses='' -c unix_socket_permissions=0700",
                "-w", "-t", "30", "start")

    def parameters(self, side, database=DB_NAME):
        return {"host": str(self.root), "port": 55439, "dbname": database,
                "user": "sanity_mock_" + side, "password": self.credentials[side],
                "sslmode": "disable", "connect_timeout": 5}

    def setup(self, staged):
        import psycopg2
        from psycopg2 import sql
        connection = psycopg2.connect(**self.parameters("admin", "postgres"))
        connection.autocommit = True
        try:
            self.server_version_num = connection.server_version
            if self.server_version_num < 120001:
                raise MockError("PostgreSQL 12.1+ is required")
            with connection.cursor() as cursor:
                for side in ("vwp", "oa"):
                    cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
                                   .format(sql.Identifier("sanity_" + side + "_app")))
                    cursor.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                           "NOBYPASSRLS PASSWORD %s IN ROLE {}")
                                   .format(sql.Identifier("sanity_mock_" + side), sql.Identifier("sanity_" + side + "_app")),
                                   (self.credentials[side],))
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB_NAME)))
        finally:
            connection.close()
        connection = psycopg2.connect(**self.parameters("admin"))
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                # PG12 grants schema CREATE to PUBLIC in a new database. This
                # disposable fixture must enforce the same no-DDL application
                # contract as newer clusters; never change an external DB here.
                cursor.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
                for table in ("sanity_vwp_data", "sanity_oa_data", "sanity_vwp_to_oa", "sanity_oa_to_vwp"):
                    columns = (staged / "db/form" / (table + ".columns.sql")).read_text(encoding="utf-8")
                    cursor.execute(sql.SQL("CREATE TABLE public.{} (").format(sql.Identifier(table)) + sql.SQL(columns + ")"))
                for script in ("002_indexes.sql", "003_grants.sql", "004_clustering.sql", "005_workspace.sql", "006_workspace_reviews.sql", "007_workspace_pipeline.sql", "008_submissions.sql", "tests/smoke.sql"):
                    cursor.execute((staged / "db" / script).read_text(encoding="utf-8"))
        finally:
            connection.close()
        # Prove this fixture really authenticates, rather than silently using trust.
        try:
            invalid = psycopg2.connect(**{**self.parameters("vwp"), "password": "deliberately-incorrect"})
        except psycopg2.OperationalError:
            return
        invalid.close()
        raise MockError("Fixture accepted an invalid password")

    def test_environment(self):
        from psycopg2.extensions import make_dsn
        return {**self.env, "SANITY_DB_ACCESS": "postgresql", "SANITY_DB_SCHEMA": "public",
                "SANITY_AI_MODE": "disabled",
                "SANITY_TEST_ADMIN_DSN": make_dsn(**self.parameters("admin")),
                "SANITY_VWP_DSN": make_dsn(**self.parameters("vwp")),
                "SANITY_OA_DSN": make_dsn(**self.parameters("oa"))}

    def stop(self):
        if self.attempted_start and (self.data / "postmaster.pid").exists():
            self.pg("pg_ctl", "-D", self.data, "-m", "fast", "-w", "-t", "30", "stop")


def execute(staged, environment, log, script, *args):
    with log.open("wb") as output:
        command = ([sys.executable, str(staged / script)] if script.endswith(".py")
                   else [sys.executable, "-m", script])
        process = subprocess.run([*command, *map(str, args)], cwd=staged,
                                 env=environment, stdout=output, stderr=subprocess.STDOUT)
    if process.returncode:
        raise MockError(f"{script} failed; see {log.name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", help="directory containing initdb, pg_ctl and postgres")
    parser.add_argument("--rows", type=int, default=10000, help="synthetic parser/storage scale probe size")
    parser.add_argument("--groups", type=int, default=100)
    args = parser.parse_args(argv)
    if not 1 <= args.groups <= args.rows <= 100000:
        parser.error("Require 1 <= groups <= rows <= 100000")
    database = None
    summary = {"status": "FAILED", "db_stopped": False}
    started = time.monotonic()
    try:
        import psycopg2
        database = MockDatabase(pg_binaries(args.pg_bin))
        print(f"artifacts={database.root}", flush=True)
        staged = database.root / "source"
        stage_source(ROOT, staged)
        print("phase=initdb auth=SCRAM transport=UNIX_SOCKET", flush=True)
        database.start()
        database.setup(staged)
        summary['server_version_num'] = database.server_version_num
        environment = database.test_environment()
        print("phase=schema_and_permissions_passed", flush=True)
        for side in ("vwp", "oa"):
            execute(staged, environment, database.root / f"check-{side}.log", "common.sanity_db", "check", "--side", side)
        print("phase=regression_tests", flush=True)
        execute(staged, environment, database.root / "tests.log", "run_tests.py", "--require-db",
                "--report-json", database.root / "tests.json")
        summary["tests"] = json.loads((database.root / "tests.json").read_text(encoding="utf-8"))
        if summary["tests"]["skipped"]:
            raise MockError("Mock DB validation must not skip tests")
        print(f"phase=tests_passed count={summary['tests']['tests_run']}", flush=True)
        scenarios = {}
        for scenario in ("complete", "missing", "malformed", "unsupported"):
            output = database.root / ("demo-" + scenario)
            execute(staged, environment, database.root / ("demo-" + scenario + ".log"), "devtools/demo.py",
                    "--scenario", scenario, "--out", output)
            scenarios[scenario] = json.loads((output / "receipt.json").read_text(encoding="utf-8"))["coverage"]
        summary["scenarios"] = scenarios
        print(f"phase=scale_probe rows={args.rows} groups={args.groups}", flush=True)
        execute(staged, environment, database.root / "scale.json", "tests/benchmark_postgresql.py",
                "--rows", args.rows, "--groups", args.groups)
        summary["scale"] = json.loads((database.root / "scale.json").read_text(encoding="utf-8"))
        summary.update(status="PASSED", driver="psycopg2", auth="scram-sha-256", tcp_listener=False,
                       private_env_used=False, real_ai_called=False)
    except (Exception, KeyboardInterrupt) as exc:
        # Avoid driver error strings, exception repr/command lines and credentials.
        summary["error_type"] = type(exc).__name__
        print(f"status=FAILED error_type={type(exc).__name__}; inspect retained phase logs", file=sys.stderr)
        if isinstance(exc, MockError):
            print(str(exc), file=sys.stderr)
    finally:
        if database is not None:
            try:
                database.stop()
                summary["db_stopped"] = True
            except (Exception, KeyboardInterrupt) as exc:
                summary.update(status="FAILED", stop_error_type=type(exc).__name__)
                print("Database shutdown failed; check retained runtime before cleanup", file=sys.stderr)
            summary["seconds"] = round(time.monotonic() - started, 3)
            with (database.root / "summary.json").open("x", encoding="utf-8") as stream:
                json.dump(summary, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(f"status={summary['status']} db_stopped={summary['db_stopped']} report={database.root / 'summary.json'}", flush=True)
    return 0 if summary["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
