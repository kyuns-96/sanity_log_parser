"""Container boundary and real child-process lifecycle; no company SDK or DB."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from oa.docker import start


class HostBoundaryTests(unittest.TestCase):
    def test_exact_names_ips_and_local_health_host(self):
        names = start.server_names(" OA.Example.Invalid,192.0.2.10,::1,oa.example.invalid ")
        self.assertEqual("oa.example.invalid 192.0.2.10 [::1] 127.0.0.1", names)

    def test_nginx_injection_wildcards_ports_and_scoped_ipv6_are_rejected(self):
        for value in ("", " ", "oa.example.invalid,", "*", "*.example.invalid", ".example.invalid",
                      "https://oa.example.invalid", "oa.example.invalid:8080", "good; return 200; #",
                      "good\nbad", "good bad", "~.*", "bad_name", "-bad.example.invalid",
                      "a" * 254, "[::1];", "fe80::1%eth0", "[fe80::1%injected;]"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                start.server_names(value)

    def test_config_render_cannot_add_an_unconfigured_host(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "nginx"
            start.render_config("oa.example.invalid", runtime=runtime)
            text = (runtime / "server.conf").read_text()
            self.assertIn("server_name oa.example.invalid 127.0.0.1;", text)
            self.assertNotIn("__OA_SERVER_NAMES__", text)
            self.assertIn("return 400;", text)
            self.assertIn("proxy_set_header Host 127.0.0.1;", text)

    def test_invalid_config_is_rejected_before_creating_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "nginx"
            with self.assertRaises(ValueError):
                start.render_config("*", runtime=runtime)
            self.assertFalse(runtime.exists())


class ProcessLifecycleTests(unittest.TestCase):
    def run_recorded(self, commands, stop):
        actual_popen = subprocess.Popen
        children = []

        def spawn(*args, **kwargs):
            child = actual_popen(*args, **kwargs)
            children.append(child)
            return child

        with patch.object(start.subprocess, "Popen", side_effect=spawn):
            with contextlib.redirect_stderr(io.StringIO()):
                result = start.run_services(commands, stop)
        self.assertTrue(all(child.poll() is not None for child in children))
        return result, children

    def test_peer_exit_terminates_other_service_and_reports_failure(self):
        sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
        for exit_code in (0, 3):
            with self.subTest(exit_code=exit_code):
                result, children = self.run_recorded(
                    [sleeper, [sys.executable, "-c", f"raise SystemExit({exit_code})"]], threading.Event())
                self.assertEqual(1, result)
                self.assertLess(children[0].returncode, 0)

    def test_shutdown_request_terminates_both_services(self):
        stop = threading.Event()
        timer = threading.Timer(0.4, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
        result, children = self.run_recorded([sleeper, sleeper], stop)
        self.assertEqual(0, result)
        self.assertEqual(2, len(children))

    def test_missing_nginx_executable_cleans_up_started_backend(self):
        sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
        result, children = self.run_recorded([sleeper, ["/nonexistent/test-nginx"]], threading.Event())
        self.assertEqual(1, result)
        self.assertEqual(1, len(children))

    def test_services_are_not_started_after_a_shutdown_request(self):
        stop = threading.Event()
        stop.set()
        result, children = self.run_recorded([["/nonexistent/test-nginx"]], stop)
        self.assertEqual(0, result)
        self.assertEqual([], children)

    def test_unresponsive_service_is_killed_after_the_shutdown_deadline(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
             "print('ready', flush=True); time.sleep(60)"],
            start_new_session=True, stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual("ready\n", child.stdout.readline())
            start.stop_services([child], timeout=0.1)
            self.assertEqual(-9, child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()
