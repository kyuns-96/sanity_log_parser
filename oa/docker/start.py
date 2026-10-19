"""Run nginx:8080 and the unchanged loopback SSO app as one container service."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parent
RUNTIME = Path("/tmp/sanity-nginx")
DEFAULT_HOSTS = "127.0.0.1,localhost,[::1]"
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def server_names(value: str) -> str:
    """Accept exact DNS names/IPs, never nginx patterns or config fragments."""
    if not value or len(value) > 4096:
        raise ValueError("CONTAINER_HOST_CONFIG")
    names = []
    for raw in value.split(","):
        host = raw.strip()
        if not host or len(host) > 253:
            raise ValueError("CONTAINER_HOST_CONFIG")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host.startswith("[") and host.endswith("]"):
                try:
                    address = ipaddress.IPv6Address(host[1:-1])
                except ValueError:
                    raise ValueError("CONTAINER_HOST_CONFIG") from None
            elif all(HOST_LABEL.fullmatch(label) for label in host.split(".")):
                names.append(host.lower())
                continue
            else:
                raise ValueError("CONTAINER_HOST_CONFIG") from None
        if address.version == 6 and address.scope_id is not None:
            raise ValueError("CONTAINER_HOST_CONFIG")
        names.append(f"[{address}]" if address.version == 6 else str(address))
    # Health checks must work even when only a company hostname is configured.
    return " ".join(dict.fromkeys([*names, "127.0.0.1"]))


def render_config(hosts: str, *, runtime: Path = RUNTIME) -> None:
    names = server_names(hosts)
    template = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    (runtime / "server.conf").write_text(template.replace("__OA_SERVER_NAMES__", names), encoding="utf-8")


def stop_services(children, *, timeout: float = 10) -> None:
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def run_services(commands, stop: threading.Event) -> int:
    children = []
    try:
        for command in commands:
            if stop.is_set():
                return 0
            children.append(subprocess.Popen(command, start_new_session=True))
        while not stop.wait(0.2):
            if any(child.poll() is not None for child in children):
                print("code=CONTAINER_PROCESS_EXITED", file=sys.stderr, flush=True)
                return 1
        return 0
    except OSError:
        print("code=CONTAINER_START_FAILED", file=sys.stderr, flush=True)
        return 1
    finally:
        stop_services(children)


def main() -> int:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        render_config(os.environ.get("OA_ALLOWED_HOSTS", DEFAULT_HOSTS))
        checked = subprocess.run(
            ["nginx", "-e", "stderr", "-t", "-q", "-c", str(ROOT / "nginx-main.conf")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=10,
        )
        if checked.returncode:
            print("code=CONTAINER_NGINX_CONFIG", file=sys.stderr)
            return 2
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print("code=CONTAINER_CONFIG", file=sys.stderr)
        return 2
    return run_services([
        [sys.executable, "-m", "oa.sso_app"],
        ["nginx", "-e", "stderr", "-c", str(ROOT / "nginx-main.conf"), "-g", "daemon off;"],
    ], stop)


if __name__ == "__main__":
    raise SystemExit(main())
