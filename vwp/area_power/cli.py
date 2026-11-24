"""Upload an actual report_export CSV to VWP, never directly to a database.

Run from the host release with bin/area-power or python -m vwp.area_power.
Set AREA_POWER_SERVER_URL or use --url for the internal VWP service.

Read-only catalog UUID lookup (pre-registered & OA-bound scopes only):
  bin/area-power --resolve --project PROJ --block BLK --net-ver NET_01 [--revision REV] [--layout PRE|POST]

Register (or reuse) a new net_ver + revision under an already OA-registered & bound project/block
(projects and blocks are never auto-created on VWP):
  bin/area-power --register-version --project PROJ --block BLK --net-ver NET_02 --revision r1 --layout POST
  bin/area-power out.csv --metadata meta.json --project PROJ --block BLK --net-ver NET_02 --revision r1 --layout POST --register-version

The same --project/--block/--net-ver selector (or a "catalog" object in --config, with optional
"register_version": true) can be passed during upload to resolve/register version UUIDs before
exporting/uploading.

The proprietary report_export executable and its licensed execution environment
must be installed by the user. Its command-line contract is not available here:
provide the complete argv with --report-export (last option) or in --config,
including any real exporter options needed to write the explicitly supplied CSV
path. No exporter flags are invented or appended. The output file must exist
before upload. Config paths and exporter cwd are relative to its directory.
"""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import subprocess
import sys

import httpx

from common.area_power.jsonutil import dumps, loads

CATALOG_KEYS = ("project", "block", "net_ver", "revision", "layout", "source_namespace", "version")
PREVIOUS_KEYS = ("previous_version", "previous_net_ver", "previous_revision", "previous_layout", "previous_source_namespace")
ALL_CATALOG_CONFIG_KEYS = set(CATALOG_KEYS) | set(PREVIOUS_KEYS) | {"register_version"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="area-power", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?", type=Path, help="Existing CSV or gzip output path to upload (checked after optional export)")
    parser.add_argument("--metadata", type=Path, help="UTF-8 JSON file containing UploadMetadata, including catalog IDs and hierarchy_separator")
    parser.add_argument("--config", type=Path, help="PC JSON configuration with csv, metadata, optional catalog lookup/version registration, and optional report_export argv; exclusive with explicit input options")
    parser.add_argument("--resolve", action="store_true", help="Read-only lookup of pre-registered Area/Power catalog UUIDs on VWP and exit")
    parser.add_argument("--register-version", dest="register_version", action="store_true", help="Register (or reuse) net_ver + revision under an already OA-registered & Area-bound project/block; standalone or combined with upload")
    parser.add_argument("--project", help="Registered project name or native project ID")
    parser.add_argument("--block", help="Registered block name or native block ID")
    parser.add_argument("--net-ver", dest="net_ver", help="NET_VER for catalog lookup or version registration")
    parser.add_argument("--revision", help="Revision for catalog lookup or version registration (required when creating a new version)")
    parser.add_argument("--layout", choices=("PRE", "POST"), help="Layout (PRE or POST; required when creating a new version)")
    parser.add_argument("--source-namespace", dest="source_namespace", help="Optional source_namespace to narrow lookup or declare for a new version")
    parser.add_argument("--version", help="Optional registered native version ID or Area version name for read-only lookup")
    parser.add_argument("--previous-net-ver", dest="previous_net_ver", help="Optional previous NET_VER lineage when registering a new version")
    parser.add_argument("--previous-revision", dest="previous_revision", help="Optional previous revision lineage when registering a new version")
    parser.add_argument("--previous-layout", dest="previous_layout", choices=("PRE", "POST"), help="Optional previous layout lineage when registering a new version")
    parser.add_argument("--previous-source-namespace", dest="previous_source_namespace", help="Optional previous source_namespace lineage when registering a new version")
    parser.add_argument("--previous-version", dest="previous_version", help="Optional previous native version ID lineage when registering a new version")
    parser.add_argument("--url", default=os.environ.get("AREA_POWER_SERVER_URL"), help="VWP service base URL (or AREA_POWER_SERVER_URL)")
    parser.add_argument("--timeout", type=float, default=300, help="HTTP request timeout in seconds (default: 300)")
    parser.add_argument("--report-export", nargs=argparse.REMAINDER, metavar="ARGV", help="Execute user-supplied exporter argv with shell=False before upload; must be last")
    return parser


def _clean_catalog_selector(raw: dict, *, allow_register: bool = False) -> tuple[dict[str, str], bool]:
    allowed = ALL_CATALOG_CONFIG_KEYS if allow_register else set(CATALOG_KEYS)
    if not isinstance(raw, dict) or raw.keys() - allowed:
        raise ValueError("Invalid catalog selector keys")
    register = False
    if "register_version" in raw:
        if type(raw["register_version"]) is not bool:
            raise ValueError("register_version must be a boolean")
        register = raw["register_version"]
    selector: dict[str, str] = {}
    for key in CATALOG_KEYS + (PREVIOUS_KEYS if allow_register else ()):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError(f"Invalid catalog field: {key}")
        selector[key] = value.strip()
    if not selector and not register:
        return {}, False
    if "project" not in selector or "block" not in selector:
        raise ValueError("Catalog lookup/registration requires both project and block")
    if register:
        if "version" in selector:
            raise ValueError("Version registration uses net_ver and revision, not --version")
        if "net_ver" not in selector or "revision" not in selector:
            raise ValueError("Version registration requires --project, --block, --net-ver and --revision")
    else:
        if any(key in selector for key in PREVIOUS_KEYS):
            raise ValueError("Previous version lineage options require --register-version")
        if "net_ver" not in selector and "version" not in selector:
            raise ValueError("Catalog lookup requires at least net_ver or version")
    if "layout" in selector and selector["layout"] not in ("PRE", "POST"):
        raise ValueError("Catalog layout must be PRE or POST")
    if "previous_layout" in selector and selector["previous_layout"] not in ("PRE", "POST"):
        raise ValueError("previous_layout must be PRE or POST")
    return selector, register


def _config_inputs(path: Path) -> tuple[Path, Path, list[str] | None, Path, dict[str, str] | None, bool]:
    directory = path.resolve().parent
    config = loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.keys() - {"csv", "metadata", "report_export", "catalog"}:
        raise ValueError("Invalid config object or keys")
    for key in ("csv", "metadata"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise ValueError("Invalid input path")
    exporter = config.get("report_export")
    if "report_export" in config and (
        not isinstance(exporter, list) or not exporter
        or any(not isinstance(arg, str) or "\0" in arg for arg in exporter)
        or not exporter[0].strip()
    ):
        raise ValueError("Invalid exporter argv")
    catalog = None
    register = False
    if "catalog" in config and config["catalog"] is not None:
        catalog, register = _clean_catalog_selector(config["catalog"], allow_register=True)
        if not catalog:
            raise ValueError("Catalog selector must not be empty")
    return directory / config["csv"], directory / config["metadata"], exporter, directory, catalog, register


def _error_detail(response: httpx.Response) -> tuple[str, str]:
    try:
        detail = loads(response.content).get("detail", {})
        if isinstance(detail, dict):
            code = "".join(ch if ch.isprintable() else " " for ch in str(detail.get("code", "")))[:100].strip()
            message = "".join(ch if ch.isprintable() else " " for ch in str(detail.get("message", "")))[:400].strip()
            return code, message
    except (ValueError, TypeError, AttributeError):
        pass
    return "", ""


def _resolve_catalog(url: httpx.URL, timeout: float, selector: dict[str, str], *, register: bool = False) -> tuple[int, dict | None]:
    action = "Version registration" if register else "Catalog lookup"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            if register:
                response = client.post(str(url).rstrip("/") + "/api/area-power/catalog/versions", json=selector)
            else:
                response = client.get(str(url).rstrip("/") + "/api/area-power/catalog/resolve", params=selector)
    except (OSError, httpx.HTTPError):
        print(f"{action} failed: unable to reach the VWP service.", file=sys.stderr)
        return 1, None
    if response.is_success:
        try:
            data = loads(response.content)
        except (ValueError, TypeError):
            data = None
        if (not isinstance(data, dict) or data.get("found") is not True
                or not all(isinstance(data.get(k), str) and data.get(k) for k in ("project_id", "block_id", "version_id"))):
            print(f"{action} returned an invalid response.", file=sys.stderr)
            return 1, None
        return 0, data
    code, message = _error_detail(response)
    label = f" ({code})" if code else ""
    if response.status_code == 404:
        print(f"Catalog not found{label}" + (f": {message}" if message else "."), file=sys.stderr)
        return 3, None
    if response.status_code == 409:
        print(f"Catalog ambiguous or conflicting{label}" + (f": {message}" if message else "."), file=sys.stderr)
        return 4, None
    print(f"{action} failed (HTTP {response.status_code}{f' · {code}' if code else ''})" + (f": {message}" if message else "."), file=sys.stderr)
    return 1, None


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    export_cwd = None
    cli_catalog_raw = {key: getattr(args, key) for key in CATALOG_KEYS + PREVIOUS_KEYS if getattr(args, key) is not None}
    catalog_selector: dict[str, str] | None = None
    register_mode = bool(args.register_version)
    standalone_catalog = False
    if args.resolve and args.register_version:
        parser.error("--resolve and --register-version are mutually exclusive")
    if args.resolve:
        if args.config is not None or args.csv is not None or args.metadata is not None or args.report_export is not None:
            parser.error("--resolve cannot be combined with csv, --metadata, --config or --report-export")
        try:
            catalog_selector, _ = _clean_catalog_selector(cli_catalog_raw, allow_register=False)
        except ValueError as exc:
            parser.error(str(exc))
        if not catalog_selector:
            parser.error("--resolve requires --project, --block and at least --net-ver or --version")
        standalone_catalog = True
    elif args.register_version and args.config is None and args.csv is None and args.metadata is None and args.report_export is None:
        try:
            catalog_selector, register_mode = _clean_catalog_selector({**cli_catalog_raw, "register_version": True}, allow_register=True)
        except ValueError as exc:
            parser.error(str(exc))
        standalone_catalog = True
    elif args.config is not None:
        if args.csv is not None or args.metadata is not None or args.report_export is not None or cli_catalog_raw or args.register_version:
            parser.error("--config cannot be combined with csv, --metadata, --report-export or catalog options")
        try:
            args.csv, args.metadata, args.report_export, export_cwd, catalog_selector, register_mode = _config_inputs(args.config)
        except (OSError, UnicodeError, ValueError, TypeError, RuntimeError):
            parser.error("Cannot read a valid config JSON object with csv, metadata and optional catalog/report_export")
    elif args.csv is None or args.metadata is None:
        parser.error("csv and --metadata are required unless --config, --resolve or standalone --register-version is used")
    elif cli_catalog_raw or args.register_version:
        try:
            catalog_selector, register_mode = _clean_catalog_selector(
                {**cli_catalog_raw, **({"register_version": True} if args.register_version else {})},
                allow_register=True,
            )
        except ValueError as exc:
            parser.error(str(exc))
    if not args.url:
        parser.error("--url (or AREA_POWER_SERVER_URL) is required")
    try:
        url = httpx.URL(args.url)
    except Exception:
        parser.error("--url must be a valid HTTP(S) service URL")
    if url.scheme not in {"https", "http"} or not url.host or url.userinfo or url.query or url.fragment:
        parser.error("--url must be an HTTP(S) service URL without credentials, query or fragment")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")

    if standalone_catalog:
        code, resolved = _resolve_catalog(url, args.timeout, catalog_selector or {}, register=register_mode)
        if code != 0 or resolved is None:
            return code
        print(dumps(resolved))
        return 0

    try:
        metadata = loads(args.metadata.read_text(encoding="utf-8-sig"))
        if not isinstance(metadata, dict):
            raise ValueError("Metadata must be a JSON object")
    except (OSError, UnicodeError, ValueError, TypeError):
        print("Cannot read a valid metadata JSON object.", file=sys.stderr)
        return 2

    if catalog_selector is not None:
        code, resolved = _resolve_catalog(url, args.timeout, catalog_selector, register=register_mode)
        if code != 0 or resolved is None:
            return code
        for key in ("project_id", "block_id", "version_id"):
            existing = metadata.get(key)
            if existing is not None and str(existing) != str(resolved[key]):
                print(f"Metadata {key} conflicts with the resolved catalog UUID.", file=sys.stderr)
                return 2
            metadata[key] = resolved[key]
    metadata_json = dumps(metadata)

    if args.report_export is not None:
        if not args.report_export:
            parser.error("--report-export requires the complete executable argv")
        try:
            subprocess.run(args.report_export, shell=False, check=True, stdout=sys.stderr, cwd=export_cwd)
        except FileNotFoundError:
            print("Exporter executable is unavailable; install the proprietary report_export environment and provide its real argv.", file=sys.stderr)
            return 2
        except (OSError, subprocess.CalledProcessError):
            print("Exporter execution failed; upload was not attempted.", file=sys.stderr)
            return 2
    if not args.csv.is_file():
        print("The explicitly supplied CSV output file does not exist or is not a regular file.", file=sys.stderr)
        return 2
    try:
        with args.csv.open("rb") as stream, httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
            response = client.post(
                str(url).rstrip("/") + "/api/area-power/uploads",
                data={"metadata": metadata_json},
                files={"file": (args.csv.name, stream, "application/octet-stream")},
            )
        if not response.is_success:
            _, message = _error_detail(response)
            print(f"Upload rejected (HTTP {response.status_code})" + (f": {message}" if message else "."), file=sys.stderr)
            return 1
        result = loads(response.content)
        if not isinstance(result, dict) or not isinstance(result.get("upload"), dict) or not result.get("snapshot_id"):
            raise ValueError("Invalid upload response")
        print(dumps(result))
        return 0
    except (OSError, httpx.HTTPError, ValueError, TypeError):
        print("Upload failed or the VWP service returned an invalid response. It may have committed; confirm receipt before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
