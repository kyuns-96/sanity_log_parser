#!/usr/bin/env python3
"""Create a fresh, socket-only PostgreSQL fixture and test a credential-free code snapshot.

Never accepts an external DB endpoint. Requires local PostgreSQL 12.1+ binaries and
the project's Python requirements. Runtime artifacts are retained after shutdown.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
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
                   "db/*.sql", "db/form/*.sql", "db/maintenance/*.sql", "db/tests/*.sql", "db/tests/*.py",
                   "db/portal/*.py", "db/portal/*.json")
DB_NAME = "sanity_test_mock"
ELAPSED_CLOCK = "CLOCK_BOOTTIME" if hasattr(time, "CLOCK_BOOTTIME") else "monotonic"


def elapsed_time():
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


def default_jobs():
    # Respect the app's actual CPU affinity. Workers spend much of their time
    # waiting on SQL, so half the available CPUs needlessly serializes them.
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1
    return max(1, min(4, cpus))


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
    def __init__(self, binaries, *, durability="durable"):
        if durability not in ("durable", "ephemeral"):
            raise MockError("Unknown fixture durability")
        self.durability = durability
        self.binaries = binaries
        self.root = Path(tempfile.mkdtemp(prefix="sanity-mock-pg-"))
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
                    "--auth-local=scram-sha-256", "--auth-host=reject", "--pwfile", password_file,
                    *(["--no-sync"] if self.durability == "ephemeral" else []))
        finally:
            password_file.unlink()
        self.attempted_start = True
        # A unique private socket directory permits concurrent fixtures using the
        # same port number. No TCP listener, even on localhost.
        options = f"-k {self.root} -p 55439 -c listen_addresses='' -c unix_socket_permissions=0700"
        if self.durability == "ephemeral":
            # Only this newly-created disposable cluster. Application settings,
            # SQL, SCRAM authentication and permission checks remain unchanged.
            options += " -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c jit=off"
        self.pg("pg_ctl", "-D", self.data, "-l", self.root / "server.log", "-o", options,
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
                for script in ("002_indexes.sql", "003_grants.sql", "004_clustering.sql", "005_workspace.sql", "006_workspace_reviews.sql", "007_workspace_pipeline.sql", "008_submissions.sql", "009_sso_identity.sql", "tests/smoke.sql"):
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

    def worker_environment(self, index):
        import psycopg2
        from psycopg2 import sql
        name = f"sanity_test_worker_{index}"
        connection = psycopg2.connect(**self.parameters("admin", "postgres"))
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(name), sql.Identifier(DB_NAME)))
        finally:
            connection.close()
        return self.test_environment(name)

    def test_environment(self, database=DB_NAME):
        from psycopg2.extensions import make_dsn
        return {**self.env, "SANITY_DB_ACCESS": "postgresql", "SANITY_DB_SCHEMA": "public",
                "SANITY_AI_MODE": "disabled",
                "SANITY_TEST_ADMIN_DSN": make_dsn(**self.parameters("admin", database)),
                "SANITY_VWP_DSN": make_dsn(**self.parameters("vwp", database)),
                "SANITY_OA_DSN": make_dsn(**self.parameters("oa", database))}

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


def combine_shards(manifest, reports, returncodes, seconds):
    """A missing/duplicate shard or incomplete execution can never become green."""
    profile, expected = manifest["profile"], manifest["test_ids"]
    jobs = len(returncodes)
    result = {"profile": profile, "full_suite": profile == "full", "tests_selected": len(expected),
              "tests_run": 0, "failures": 0, "errors": 0, "skipped": 0, "successful": False,
              "seconds": round(seconds, 3), "elapsed_clock": ELAPSED_CLOCK, "failed_tests": [], "timings": [],
              "test_ids": expected, "jobs": jobs, "worker_errors": [], "complete": False}
    selected, executed = [], []
    ordered = sorted(expected)
    if not expected or len(set(expected)) != len(expected) or jobs < 1:
        result["worker_errors"].append("An explicit nonempty test manifest and workers are required")
    for index, (report, returncode) in enumerate(zip(reports, returncodes)):
        valid = (isinstance(report, dict) and report.get("profile") == profile
                 and report.get("shard") == f"{index + 1}/{jobs}"
                 and report.get("tests_total") == len(expected)
                 and report.get("test_ids") == ordered[index::jobs]
                 and report.get("tests_selected") == len(ordered[index::jobs])
                 and isinstance(report.get("timings"), list))
        if not valid:
            result["worker_errors"].append(f"Invalid or missing report for worker {index + 1}")
            continue
        selected.extend(report["test_ids"])
        ids = [row["test"] for row in report["timings"]]
        executed.extend(ids)
        if report["tests_run"] != len(ids) or Counter(ids) != Counter(report["test_ids"]):
            result["worker_errors"].append(f"Incomplete execution in worker {index + 1}")
        for key in ("tests_run", "failures", "errors", "skipped"):
            result[key] += report[key]
        result["failed_tests"].extend(report["failed_tests"])
        result["timings"].extend(report["timings"])
        if returncode not in (0, 1) or (returncode == 0) != report["successful"]:
            result["worker_errors"].append(f"Exit status mismatch in worker {index + 1}")
    if len(reports) != jobs or Counter(selected) != Counter(expected) or Counter(executed) != Counter(expected):
        result["worker_errors"].append("Workers did not execute the exact selected test set")
    result["complete"] = not result["worker_errors"]
    result["successful"] = (result["complete"] and not any(returncodes)
                            and not any(result[key] for key in ("failures", "errors", "skipped")))
    result["timings"].sort(key=lambda row: row["seconds"], reverse=True)
    return result


def parallel_regression(staged, environment, database, profile, jobs):
    manifest_path = database.root / "manifest.json"
    execute(staged, environment, database.root / "discovery.log", "run_tests.py", "--profile", profile,
            "--require-db", "--manifest-json", manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest["profile"] != profile or not manifest["test_ids"]
            or manifest["tests_selected"] != len(manifest["test_ids"])
            or len(set(manifest["test_ids"])) != len(manifest["test_ids"])):
        raise MockError("Invalid test manifest")
    jobs = min(jobs, len(manifest["test_ids"]))
    started = elapsed_time()
    environments = [database.worker_environment(index + 1) for index in range(jobs)]
    children = []
    with ExitStack() as files:
        try:
            for index, worker_env in enumerate(environments):
                log = files.enter_context((database.root / f"tests-worker-{index + 1}.log").open("wb"))
                command = [sys.executable, str(staged / "run_tests.py"), "--profile", profile, "--require-db",
                           "--shard", f"{index + 1}/{jobs}", "--report-json", str(database.root / f"tests-worker-{index + 1}.json")]
                children.append(subprocess.Popen(command, cwd=staged, env=worker_env, stdout=log, stderr=subprocess.STDOUT))
                print(f"phase=worker_started shard={index + 1}/{jobs}", flush=True)
            returncodes = [child.wait() for child in children]
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    reports = []
    for index in range(jobs):
        try:
            reports.append(json.loads((database.root / f"tests-worker-{index + 1}.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            reports.append(None)
    report = combine_shards(manifest, reports, returncodes, elapsed_time() - started)
    with (database.root / "tests.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", help="directory containing initdb, pg_ctl and postgres")
    parser.add_argument("--profile", choices=("quick", "regression", "full"), default="quick",
                        help="quick: three-minute core checks + 100-row probe; regression: broader checks + 100-row probe; full: all tests + 10,000-row probe")
    parser.add_argument("--rows", type=int, help="override parser/storage probe rows; does not change test fixtures")
    parser.add_argument("--groups", type=int, help="override parser/storage probe groups")
    parser.add_argument("--jobs", type=int, default=default_jobs(), help="isolated test worker databases (default: available CPUs, up to 4; 1: serial)")
    parser.add_argument("--durability", choices=("ephemeral", "durable"), default="ephemeral",
                        help="ephemeral: disposable cluster without disk sync (default); durable: PostgreSQL disk sync")
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be between 1 and 16")
    args.rows = args.rows if args.rows is not None else (10000 if args.profile == "full" else 100)
    args.groups = args.groups if args.groups is not None else min(args.rows, 100 if args.profile == "full" else 10)
    if not 1 <= args.groups <= args.rows <= 100000:
        parser.error("Require 1 <= groups <= rows <= 100000")
    database = None
    summary = {"status": "FAILED", "db_stopped": False, "profile": args.profile,
               "full_suite": args.profile == "full", "jobs": args.jobs, "durability": args.durability,
               "elapsed_clock": ELAPSED_CLOCK}
    started = elapsed_time()
    try:
        import psycopg2
        database = MockDatabase(pg_binaries(args.pg_bin), durability=args.durability)
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
        print(f"phase=regression_tests profile={args.profile}", flush=True)
        if args.jobs == 1:
            try:
                execute(staged, environment, database.root / "tests.log", "run_tests.py", "--require-db",
                        "--profile", args.profile, "--report-json", database.root / "tests.json")
            finally:
                if (database.root / "tests.json").is_file():
                    summary["tests"] = json.loads((database.root / "tests.json").read_text(encoding="utf-8"))
        else:
            summary["tests"] = parallel_regression(staged, environment, database, args.profile, args.jobs)
            if not summary["tests"]["successful"]:
                raise MockError("Regression tests failed; see tests.json and tests-worker-*.log")
        if summary["tests"].get("profile") != args.profile:
            raise MockError("Test report profile does not match the requested scope")
        if summary["tests"]["skipped"]:
            raise MockError("Mock DB validation must not skip tests")
        print(f"phase=tests_passed count={summary['tests']['tests_run']}", flush=True)
        scenarios = {}
        selected_scenarios = ("complete", "missing", "malformed", "unsupported") if args.profile == "full" else ("complete",)
        for scenario in selected_scenarios:
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
            summary["seconds"] = round(elapsed_time() - started, 3)
            with (database.root / "summary.json").open("x", encoding="utf-8") as stream:
                json.dump(summary, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(f"status={summary['status']} profile={args.profile} jobs={args.jobs} seconds={summary['seconds']:.3f} elapsed_clock={ELAPSED_CLOCK} db_stopped={summary['db_stopped']} report={database.root / 'summary.json'}", flush=True)
    return 0 if summary["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
