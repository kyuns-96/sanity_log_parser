"""Upload an actual report_export CSV to VWP, never directly to a database.

Run from the host release with bin/area-power or python -m area_power.vwp.
Set AREA_POWER_SERVER_URL or use --url for the internal VWP service.

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

from area_power.common.jsonutil import dumps, loads


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="area-power", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="?", type=Path, help="Existing CSV or gzip output path to upload (checked after optional export)")
    parser.add_argument("--metadata", type=Path, help="UTF-8 JSON file containing UploadMetadata, including catalog IDs and hierarchy_separator")
    parser.add_argument("--config", type=Path, help="PC JSON configuration with csv, metadata and optional report_export argv; exclusive with explicit input options")
    parser.add_argument("--url", default=os.environ.get("AREA_POWER_SERVER_URL"), help="VWP service base URL (or AREA_POWER_SERVER_URL)")
    parser.add_argument("--timeout", type=float, default=300, help="HTTP request timeout in seconds (default: 300)")
    parser.add_argument("--report-export", nargs=argparse.REMAINDER, metavar="ARGV", help="Execute user-supplied exporter argv with shell=False before upload; must be last")
    return parser


def _config_inputs(path: Path) -> tuple[Path, Path, list[str] | None, Path]:
    directory = path.resolve().parent
    config = loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.keys() - {"csv", "metadata", "report_export"}:
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
    return directory / config["csv"], directory / config["metadata"], exporter, directory


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    export_cwd = None
    if args.config is not None:
        if args.csv is not None or args.metadata is not None or args.report_export is not None:
            parser.error("--config cannot be combined with csv, --metadata or --report-export")
        try:
            args.csv, args.metadata, args.report_export, export_cwd = _config_inputs(args.config)
        except (OSError, UnicodeError, ValueError, TypeError, RuntimeError):
            parser.error("Cannot read a valid config JSON object with csv, metadata and optional report_export argv")
    elif args.csv is None or args.metadata is None:
        parser.error("csv and --metadata are required unless --config is used")
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
    try:
        metadata = loads(args.metadata.read_text(encoding="utf-8-sig"))
        if not isinstance(metadata, dict):
            raise ValueError("Metadata must be a JSON object")
        metadata_json = dumps(metadata)
    except (OSError, UnicodeError, ValueError, TypeError):
        print("Cannot read a valid metadata JSON object.", file=sys.stderr)
        return 2
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
            try:
                detail = loads(response.content).get("detail", {})
                message = detail.get("message", "") if isinstance(detail, dict) else ""
                message = "".join(char if char.isprintable() else " " for char in str(message))[:400]
            except (ValueError, TypeError, AttributeError):
                message = ""
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
