"""Extract a sanitized Floorplan SVG + Scene locally from DEF/LEF on VWP and upload via VWP-bridge.

SECURITY & DB BOUNDARY:
- Raw DEF/LEF files are parsed strictly locally on VWP and NEVER transmitted over HTTP or stored in PostgreSQL.
- Run with: python -m vwp.floorplan --project PROJ --block BLK --fp-ver FP_v1.0 --def block.def.gz --lef macros.lef
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import sys
from typing import Any

import httpx

from common.area_power.jsonutil import dumps, loads
from common.floorplan import (
    apply_bist_map_to_scene,
    parse_def_to_scene,
    parse_lef_macros,
    reject_raw_def_or_lef,
    render_scene_svg,
)
from vwp.integration.corporate_floorplan import (
    CorporateClassifierNotConfigured,
    create_macro_classifier,
    extract_bist_groups,
)


def _load_bist_map_file(path: Path) -> dict[str, dict[str, str | None] | str]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        raw = loads(text)
        if not isinstance(raw, dict):
            raise ValueError("JSON --bist-map must be an object mapping inst_path -> bist_group or {bist_group, controller}")
        return raw
    mapping: dict[str, dict[str, str | None] | str] = {}
    reader = csv.DictReader(text.splitlines())
    for row in reader:
        inst = (row.get("inst_name") or row.get("inst_path") or row.get("instance") or "").strip()
        grp = (row.get("bist_group") or row.get("group") or "").strip()
        ctrl = (row.get("controller") or row.get("bist_controller") or "").strip() or None
        if inst and grp:
            mapping[inst] = {"bist_group": grp, "controller": ctrl}
    return mapping


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vwp-floorplan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="Registered project name or ID")
    p.add_argument("--block", required=True, help="Registered block name or ID")
    p.add_argument("--fp-ver", dest="fp_ver", required=True, help="Independent Floorplan version label (e.g. FP_v1.0)")
    p.add_argument("--prev-fp-ver", dest="prev_fp_ver", help="Optional previous Floorplan version label for diff/carry-over")
    p.add_argument("--scope-kind", choices=("top", "subblock"), default="subblock", help="top (Full-chip) or subblock")
    p.add_argument("--title", default="", help="Optional Floorplan version title")
    p.add_argument("--net-ver", dest="net_ver", help="Optional linked NET_VER")
    p.add_argument("--revision", help="Optional linked revision")
    p.add_argument("--layout", choices=("PRE", "POST"), help="Optional PRE or POST layout stage")
    p.add_argument("--eco-num", dest="eco_num", help="Optional linked ECO_NUM")
    p.add_argument("--def", dest="def_path", type=Path, help="Local DEF or .def.gz path (parsed locally, NEVER uploaded)")
    p.add_argument("--lef", dest="lef_paths", nargs="*", type=Path, default=[], help="Local LEF or .lef.gz paths for macro sizes (parsed locally, NEVER uploaded)")
    p.add_argument("--bist-map", dest="bist_map", type=Path, help="Optional JSON or CSV mapping SRAM instances to BIST groups")
    p.add_argument("--use-corporate-bist", action="store_true", help="Invoke vwp.integration.corporate_floorplan.extract_bist_groups")
    p.add_argument("--use-corporate-classifier", action="store_true", help="Require vwp.integration.corporate_floorplan.create_macro_classifier()")
    p.add_argument("--extra-image", dest="extra_images", action="append", default=[], metavar="KIND:VIEW:PATH",
                   help="Optional auxiliary PNG/WebP/JPEG screenshot (e.g. congestion:routing_cong:./cong.png)")
    p.add_argument("--dry-run-svg", dest="dry_run_svg", type=Path, help="Write extracted SVG locally and exit without HTTP upload")
    p.add_argument("--workers", type=int, default=None, help="Parallel worker processes for large uncompressed DEF parsing (default: auto)")
    p.add_argument("--url", default=os.environ.get("AREA_POWER_SERVER_URL"), help="VWP-bridge base URL (or AREA_POWER_SERVER_URL)")
    p.add_argument("--timeout", type=float, default=180.0, help="HTTP timeout seconds")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.def_path and not args.extra_images:
        print("Provide --def (for vector floorplan extraction) and/or --extra-image.", file=sys.stderr)
        return 2

    scene_dict: dict[str, Any] | None = None
    svg_bytes: bytes | None = None

    if args.def_path:
        if not args.def_path.is_file():
            print(f"DEF file not found: {args.def_path}", file=sys.stderr)
            return 2
        for lp in args.lef_paths:
            if not lp.is_file():
                print(f"LEF file not found: {lp}", file=sys.stderr)
                return 2
        lef_macros = parse_lef_macros(args.lef_paths)
        classifier = create_macro_classifier()
        if args.use_corporate_classifier and classifier is None:
            raise CorporateClassifierNotConfigured(
                "Corporate macro classifier is not configured yet. Implement vwp/integration/corporate_floorplan.py:create_macro_classifier()."
            )
        if classifier is not None:
            classifier.prepare(
                work_dir=args.def_path.resolve().parent,
                project=args.project.strip(),
                block=args.block.strip(),
                fp_ver=args.fp_ver.strip(),
                scope_kind=args.scope_kind,
                lef_macros=lef_macros,
            )
        bist_map: dict[str, Any] = {}
        if args.bist_map:
            bist_map.update(_load_bist_map_file(args.bist_map))
        prelim = parse_def_to_scene(
            args.def_path,
            lef_macros,
            fp_ver=args.fp_ver.strip(),
            prev_fp_ver=args.prev_fp_ver.strip() if args.prev_fp_ver else None,
            scope_kind=args.scope_kind,
            bist_map=bist_map,
            macro_classifier=classifier,
            workers=args.workers,
        )
        if args.use_corporate_bist:
            sram_list = [
                {"inst_path": m.inst_name, "master": m.master, "bbox_um": list(m.bbox())}
                for m in prelim.macros
                if m.kind == "sram"
            ]
            corp_map = extract_bist_groups(
                sram_list,
                work_dir=args.def_path.resolve().parent,
                project=args.project,
                block=args.block,
                fp_ver=args.fp_ver.strip(),
            )
            if corp_map:
                prelim = apply_bist_map_to_scene(prelim, corp_map)
        svg_bytes = render_scene_svg(prelim, mode="overview")
        reject_raw_def_or_lef(svg_bytes, f"{prelim.design_name}.svg")
        scene_dict = prelim.model_dump(mode="python")

        if args.dry_run_svg:
            args.dry_run_svg.write_bytes(svg_bytes)
            print(dumps({"status": "dry_run_ok", "svg_path": str(args.dry_run_svg), "stats": prelim.stats}))
            return 0

    if not args.url:
        print("VWP-bridge URL required (--url or AREA_POWER_SERVER_URL).", file=sys.stderr)
        return 2

    base = args.url.rstrip("/")
    with httpx.Client(timeout=args.timeout) as client:
        if svg_bytes is not None and scene_dict is not None:
            meta = {
                "project": args.project.strip(),
                "block": args.block.strip(),
                "fp_ver": args.fp_ver.strip(),
                "prev_fp_ver": args.prev_fp_ver.strip() if args.prev_fp_ver else None,
                "scope_kind": args.scope_kind,
                "title": args.title.strip(),
                "net_ver": args.net_ver.strip() if args.net_ver else None,
                "revision": args.revision.strip() if args.revision else None,
                "layout": args.layout,
                "eco_num": args.eco_num.strip() if args.eco_num else None,
                "scene": scene_dict,
            }
            resp = client.post(
                f"{base}/api/area-power/floorplans",
                data={"metadata": dumps(meta)},
                files={"file": (f"{args.block.strip()}_{args.fp_ver.strip()}.svg", svg_bytes, "image/svg+xml")},
            )
            if resp.status_code not in (200, 201):
                print(resp.text, file=sys.stderr)
                return 1
            print(resp.text)

        for spec in args.extra_images:
            parts = spec.split(":", 2)
            if len(parts) != 3:
                print(f"Invalid --extra-image format (expected KIND:VIEW:PATH): {spec}", file=sys.stderr)
                return 2
            kind, view_name, raw_path = parts[0].strip(), parts[1].strip(), Path(parts[2].strip())
            img_bytes = raw_path.read_bytes()
            reject_raw_def_or_lef(img_bytes, raw_path.name)
            ext = raw_path.suffix.lower()
            mime = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}.get(ext, "image/png")
            att_meta = {
                "project": args.project.strip(),
                "block": args.block.strip(),
                "fp_ver": args.fp_ver.strip(),
                "artifact_kind": kind,
                "view_name": view_name,
                "mime_type": mime,
                "net_ver": args.net_ver.strip() if args.net_ver else None,
                "revision": args.revision.strip() if args.revision else None,
                "layout": args.layout,
                "eco_num": args.eco_num.strip() if args.eco_num else None,
            }
            aresp = client.post(
                f"{base}/api/area-power/floorplans/attachments",
                data={"metadata": dumps(att_meta)},
                files={"file": (raw_path.name, img_bytes, mime)},
            )
            if aresp.status_code not in (200, 201):
                print(aresp.text, file=sys.stderr)
                return 1
            print(aresp.text)
    return 0
